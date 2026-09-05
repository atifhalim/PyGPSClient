"""
rtcm_mavlink.py

Headless (GUI-free) RTCM3 -> MAVLink GPS_RTCM_DATA injector and RTK monitor.

This module lets a companion computer (Raspberry Pi, Jetson, laptop) forward
RTCM3 base-station corrections to a vehicle over a MAVLink link - for example
by sending them onto a Herelink Wi-Fi MAVLink stream (UDP 14552), from where
the autopilot unpacks them and relays them to the rover GNSS receiver.

Two pieces:

* :func:`fragment_rtcm` / :class:`RTCMMAVLinkInjector` - split RTCM3 into
  ``GPS_RTCM_DATA`` messages and send them. The fragmentation mirrors
  QGroundControl's own ``RTCMMavlink::RTCMDataUpdate`` byte-for-byte (see
  mavlink/qgroundcontrol ``src/GPS/RTCMMavlink.cc``) so the autopilot handles
  our stream exactly as it would QGC's.

* :class:`RTKMonitor` - read ``GPS_RAW_INT`` / ``GPS2_RAW`` / ``GPS_RTK`` /
  ``GPS2_RTK`` back off the same MAVLink stream to prove, conclusively, that
  the rover received the corrections and reached RTK (fix type 5/6).

``fragment_rtcm`` is pure and has no third-party dependency, so it is unit
tested without hardware. The injector and monitor import ``pymavlink`` lazily.

Created on 5 Sep 2026

:author: semuadmin (Steve Smith)
:copyright: 2020 semuadmin
:license: BSD 3-Clause
"""

from __future__ import annotations

# MAVLINK_MSG_GPS_RTCM_DATA_FIELD_DATA_LEN
MAX_FRAGMENT = 180
# fragment id is 2 bits -> at most 4 fragments -> 720 bytes per sequenced set
MAX_FRAGMENTS = 4
MAX_SET_BYTES = MAX_FRAGMENT * MAX_FRAGMENTS
# GPS_FIX_TYPE values that mean the rover is consuming corrections
FIX_RTK_FLOAT = 5
FIX_RTK_FIXED = 6


def fragment_rtcm(data: bytes, sequence_id: int) -> list[tuple[int, int, bytes]]:
    """
    Split one RTCM3 chunk into GPS_RTCM_DATA fragments.

    Mirrors QGroundControl's ``RTCMMavlink::RTCMDataUpdate`` exactly:

    * ``len(data) < 180`` -> a single, non-fragmented message; ``flags`` holds
      only the 5-bit sequence id in bits 3..7.
    * otherwise -> 1..4 fragments; each ``flags`` has bit0 set (fragmented),
      bits1..2 the fragment id, bits3..7 the sequence id. The final fragment is
      whatever remains (QGC sends no zero-length terminator).

    :param bytes data: one RTCM3 chunk, at most :data:`MAX_SET_BYTES` bytes
    :param int sequence_id: set sequence id (only low 5 bits are used)
    :return: list of ``(flags, length, data)`` where ``data`` is padded to 180
    :rtype: list[tuple[int, int, bytes]]
    :raises ValueError: if ``data`` exceeds :data:`MAX_SET_BYTES`
    """

    if len(data) > MAX_SET_BYTES:
        raise ValueError(
            f"RTCM chunk {len(data)} exceeds {MAX_SET_BYTES}; split it first"
        )

    seq = sequence_id & 0x1F
    out: list[tuple[int, int, bytes]] = []

    if len(data) < MAX_FRAGMENT:
        flags = seq << 3
        out.append((flags, len(data), data.ljust(MAX_FRAGMENT, b"\x00")))
    else:
        fragment_id = 0
        start = 0
        while start < len(data):
            flags = 0x01 | (fragment_id << 1) | (seq << 3)
            fragment_id += 1
            length = min(len(data) - start, MAX_FRAGMENT)
            chunk = data[start : start + length]
            out.append((flags, length, chunk.ljust(MAX_FRAGMENT, b"\x00")))
            start += length

    return out


def split_chunks(data: bytes, size: int = MAX_SET_BYTES):
    """
    Yield ``data`` in pieces of at most ``size`` bytes.

    RTCM frames are normally well under 720 bytes, but a large MSM message can
    exceed it; each piece becomes its own sequenced GPS_RTCM_DATA set.

    :param bytes data: bytes to split
    :param int size: maximum piece size
    :return: generator of byte pieces
    """

    for i in range(0, max(len(data), 1), size):
        yield data[i : i + size]


class RTCMMAVLinkInjector:
    """
    Send RTCM3 corrections onto a MAVLink link as GPS_RTCM_DATA.
    """

    def __init__(
        self,
        dest: str,
        source_system: int = 255,
        source_component: int = 220,
    ):
        """
        Constructor.

        :param str dest: pymavlink connection string for the OUTPUT link, e.g.
            ``"udpout:192.168.144.11:14552"`` for a Herelink Wi-Fi stream, or
            ``"udpout:127.0.0.1:14555"`` for a local SITL/loopback test
        :param int source_system: MAVLink source system id
        :param int source_component: MAVLink source component id
        """

        # lazy import keeps fragment_rtcm dependency-free
        from pymavlink import mavutil  # pylint: disable=import-outside-toplevel

        self._conn = mavutil.mavlink_connection(
            dest,
            source_system=source_system,
            source_component=source_component,
            input=False,
        )
        self._sequence_id = 0
        self.sets_sent = 0
        self.messages_sent = 0
        self.bytes_sent = 0

    def send_rtcm(self, data: bytes) -> int:
        """
        Fragment and send one or more RTCM3 chunks.

        :param bytes data: raw RTCM3 (one or more frames)
        :return: number of GPS_RTCM_DATA messages sent
        :rtype: int
        """

        sent = 0
        for chunk in split_chunks(data):
            if not chunk:
                continue
            for flags, length, payload in fragment_rtcm(chunk, self._sequence_id):
                self._conn.mav.gps_rtcm_data_send(flags, length, list(payload))
                sent += 1
            # QGC increments the sequence id once per chunk (per RTCMDataUpdate)
            self._sequence_id = (self._sequence_id + 1) & 0x1F
            self.sets_sent += 1
            self.bytes_sent += len(chunk)
        self.messages_sent += sent
        return sent

    def close(self):
        """Close the underlying MAVLink connection."""

        try:
            self._conn.close()
        except (OSError, AttributeError):
            pass


class RTKMonitor:
    """
    Read RTK status back off a MAVLink stream to validate correction delivery.

    Tracks the latest fix type and RTK health per GPS (GPS_RAW_INT / GPS2_RAW
    and GPS_RTK / GPS2_RTK), so a caller can confirm the rover actually reached
    RTK float/fixed while corrections are being injected.
    """

    def __init__(self, source: str):
        """
        Constructor.

        :param str source: pymavlink connection string for the INPUT link
            carrying vehicle telemetry, e.g. ``"udpin:0.0.0.0:14550"`` or the
            Herelink stream. Reading and injecting can use separate endpoints.
        """

        from pymavlink import mavutil  # pylint: disable=import-outside-toplevel

        self._conn = mavutil.mavlink_connection(source)
        # per-GPS latest state: {"GPS": {...}, "GPS2": {...}}
        self.state: dict[str, dict] = {}

    def poll(self, timeout: float = 1.0) -> dict | None:
        """
        Read one relevant message and update state.

        :param float timeout: seconds to block waiting for a message
        :return: updated per-GPS state dict, or None if nothing arrived
        :rtype: dict | None
        """

        msg = self._conn.recv_match(
            type=[
                "GPS_RAW_INT",
                "GPS2_RAW",
                "GPS_RTK",
                "GPS2_RTK",
            ],
            blocking=True,
            timeout=timeout,
        )
        if msg is None:
            return None

        mtype = msg.get_type()
        if mtype in ("GPS_RAW_INT", "GPS2_RAW"):
            key = "GPS" if mtype == "GPS_RAW_INT" else "GPS2"
            st = self.state.setdefault(key, {})
            st["fix_type"] = msg.fix_type
            st["sats"] = msg.satellites_visible
        else:  # GPS_RTK / GPS2_RTK
            key = "GPS" if mtype == "GPS_RTK" else "GPS2"
            st = self.state.setdefault(key, {})
            st["rtk_rate"] = msg.rtk_rate
            st["rtk_health"] = msg.rtk_health
            st["rtk_nsats"] = msg.nsats
            st["baseline_mm"] = getattr(msg, "baseline_a_mm", 0)
        return self.state

    def best_fix(self) -> int:
        """
        Return the highest fix type seen across all GPS units.

        :return: GPS_FIX_TYPE value (5 = RTK float, 6 = RTK fixed)
        :rtype: int
        """

        return max((s.get("fix_type", 0) for s in self.state.values()), default=0)

    def rtk_active(self) -> bool:
        """
        True if any GPS reports RTK float/fixed AND a non-zero injection rate.

        :return: whether corrections are demonstrably being consumed
        :rtype: bool
        """

        for s in self.state.values():
            if s.get("fix_type", 0) >= FIX_RTK_FLOAT and s.get("rtk_rate", 0) > 0:
                return True
        return False

    def close(self):
        """Close the underlying MAVLink connection."""

        try:
            self._conn.close()
        except (OSError, AttributeError):
            pass
