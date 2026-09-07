"""
gnss_base.py

Configure a u-blox (F9P / Here 4) receiver as an RTK **base station** and enable
the RTCM3 message set it needs to broadcast.

Two modes:

* **survey-in** (``svin``) - the receiver self-surveys its position for a minimum
  duration/accuracy, then starts transmitting corrections. Use when the base
  location changes between deployments.
* **fixed** - the receiver is told a known Lat/Lon/Height (e.g. a surveyed
  monument) and transmits immediately. Uses u-blox high-precision position
  components for full accuracy.

``disable`` returns the receiver to rover mode.

Config can be written to the volatile **RAM** layer (reverts on power-cycle) or
**persisted** to the BBR + Flash layers so the base comes back up configured.

:func:`build_base_config` is pure (no hardware) and unit tested;
:func:`configure_base` sends the config over a serial port and checks the ACK.

Created on 6 Sep 2026

:author: semuadmin (Steve Smith)
:copyright: 2020 semuadmin
:license: BSD 3-Clause
"""

from __future__ import annotations

# The RTCM3 messages a u-blox base needs: 1005 (station ARP), MSM4 observations
# for GPS/GLONASS/Galileo/BeiDou, and 1230 (GLONASS code-phase biases).
RTCM_BASE_MSGS = ("1005", "1074", "1084", "1094", "1124", "1230")
DEFAULT_PORTS = ("USB",)


def _hp_split(value: float, main_scale: float) -> tuple[int, int]:
    """
    Split a value into u-blox standard + high-precision integer components.

    Truncation is toward zero so the two parts always share the value's sign,
    as u-blox requires (the HP part is constrained to -99..99).

    :param float value: value in base units (degrees, or metres)
    :param float main_scale: scale of the main component (1e7 for deg -> 1e-7
        deg; 100 for metres -> cm)
    :return: (main, hp) integer components
    :rtype: tuple[int, int]
    """

    scaled = value * main_scale
    main = int(scaled)  # truncate toward zero
    hp = round((scaled - main) * 100)
    if hp > 99:
        main += 1
        hp -= 100
    elif hp < -99:
        main -= 1
        hp += 100
    return main, hp


def _rtcm_key(msgid: str, port: str) -> str:
    """Config-DB key to enable an RTCM3 message on a port."""

    return f"CFG_MSGOUT_RTCM_3X_TYPE{msgid}_{port}"


def build_base_config(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    mode: str = "svin",
    svin_dur: int = 60,
    svin_acc: float = 2.0,
    lat: float | None = None,
    lon: float | None = None,
    height: float | None = None,
    fixed_acc: float = 2.0,
    ports: tuple = DEFAULT_PORTS,
    rtcm_rate: int = 1,
) -> list[tuple[str, object]]:
    """
    Build the list of (config-key, value) pairs to configure a base station.

    :param str mode: "svin", "fixed" or "disable"
    :param int svin_dur: survey-in minimum duration (s), svin mode
    :param float svin_acc: survey-in accuracy limit (m), svin mode
    :param float lat: base latitude (deg), fixed mode
    :param float lon: base longitude (deg), fixed mode
    :param float height: base ellipsoidal height (m), fixed mode
    :param float fixed_acc: claimed accuracy (m), fixed mode
    :param tuple ports: receiver ports to emit RTCM on, e.g. ("USB", "UART1")
    :param int rtcm_rate: message rate (every Nth epoch); 0 disables the message
    :return: list of (key, value) pairs for UBXMessage.config_set
    :rtype: list[tuple[str, object]]
    :raises ValueError: on unknown mode, or fixed mode without a full position
    """

    cfg: list[tuple[str, object]] = []

    if mode == "disable":
        cfg.append(("CFG_TMODE_MODE", 0))
        for port in ports:
            for msg in RTCM_BASE_MSGS:
                cfg.append((_rtcm_key(msg, port), 0))
        return cfg

    if mode == "svin":
        cfg += [
            ("CFG_TMODE_MODE", 1),
            ("CFG_TMODE_SVIN_MIN_DUR", int(svin_dur)),
            ("CFG_TMODE_SVIN_ACC_LIMIT", int(round(svin_acc * 10000))),  # 0.1 mm
        ]
    elif mode == "fixed":
        if None in (lat, lon, height):
            raise ValueError("fixed mode requires lat, lon and height")
        lat_i, lat_hp = _hp_split(lat, 1e7)
        lon_i, lon_hp = _hp_split(lon, 1e7)
        h_i, h_hp = _hp_split(height, 100)
        cfg += [
            ("CFG_TMODE_MODE", 2),
            ("CFG_TMODE_POS_TYPE", 1),  # 1 = LLH
            ("CFG_TMODE_LAT", lat_i),
            ("CFG_TMODE_LAT_HP", lat_hp),
            ("CFG_TMODE_LON", lon_i),
            ("CFG_TMODE_LON_HP", lon_hp),
            ("CFG_TMODE_HEIGHT", h_i),
            ("CFG_TMODE_HEIGHT_HP", h_hp),
            ("CFG_TMODE_FIXED_POS_ACC", int(round(fixed_acc * 10000))),  # 0.1 mm
        ]
    else:
        raise ValueError(f"unknown mode '{mode}' (use svin, fixed or disable)")

    for port in ports:
        for msg in RTCM_BASE_MSGS:
            cfg.append((_rtcm_key(msg, port), int(rtcm_rate)))
    return cfg


def configure_base(
    port: str,
    baud: int,
    cfg: list,
    persist: bool = False,
    timeout: float = 5.0,
) -> str | None:
    """
    Send a base configuration to a receiver over serial and await the ACK.

    :param str port: serial device, e.g. "/dev/ttyACM0"
    :param int baud: baud rate (ignored for USB CDC)
    :param list cfg: (key, value) pairs from :func:`build_base_config`
    :param bool persist: also write to BBR + Flash so it survives a power-cycle
    :param float timeout: seconds to wait for the ACK
    :return: "ACK-ACK", "ACK-NAK" or None (no ack seen)
    :rtype: str | None
    """

    # pylint: disable=import-outside-toplevel
    from pyubx2 import (
        SET_LAYER_BBR,
        SET_LAYER_FLASH,
        SET_LAYER_RAM,
        TXN_NONE,
        UBXMessage,
        UBXReader,
    )
    from serial import Serial

    layers = SET_LAYER_RAM
    if persist:
        layers |= SET_LAYER_BBR | SET_LAYER_FLASH
    msg = UBXMessage.config_set(layers, TXN_NONE, cfg)

    import time  # local import keeps module import light

    with Serial(port, baud, timeout=1) as stream:
        stream.write(msg.serialize())
        ubr = UBXReader(stream)
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                _raw, parsed = ubr.read()
            except Exception:  # pylint: disable=broad-exception-caught
                continue
            if getattr(parsed, "identity", "") in ("ACK-ACK", "ACK-NAK"):
                return parsed.identity
    return None
