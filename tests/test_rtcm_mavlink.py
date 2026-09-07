"""
test_rtcm_mavlink.py

Unit tests for the RTCM3 -> MAVLink GPS_RTCM_DATA fragmenter and injector
(pygpsclient.rtcm_mavlink).

The fragmentation tests are pure. The loopback test round-trips real
GPS_RTCM_DATA messages through pymavlink over UDP and reassembles them, proving
the injector emits a byte-exact, correctly fragmented stream (Stage-A of the
field validation plan) - it is skipped if pymavlink is unavailable.

Created on 5 Sep 2026

:author: semuadmin (Steve Smith)
:copyright: 2020 semuadmin
:license: BSD 3-Clause
"""

# pylint: disable=missing-docstring

import os
import unittest

from pygpsclient.rtcm_mavlink import (
    MAX_FRAGMENT,
    MAX_SET_BYTES,
    fragment_rtcm,
    split_chunks,
)

try:
    from pymavlink import mavutil  # noqa: F401

    HAS_MAVLINK = True
except ImportError:
    HAS_MAVLINK = False


def reassemble(fragments):
    """Reassemble (flags, length, data180) fragments of one set into bytes."""

    return b"".join(data[:length] for _flags, length, data in fragments)


class TestFragmentRTCM(unittest.TestCase):

    def test_short_single_message(self):
        data = os.urandom(100)
        frags = fragment_rtcm(data, 3)
        self.assertEqual(len(frags), 1)
        flags, length, payload = frags[0]
        self.assertEqual(flags & 0x01, 0)  # NOT fragmented
        self.assertEqual(flags >> 3, 3)  # sequence id in high bits
        self.assertEqual(length, 100)
        self.assertEqual(len(payload), MAX_FRAGMENT)
        self.assertEqual(payload[:100], data)
        self.assertEqual(reassemble(frags), data)

    def test_exactly_180_is_fragmented(self):
        # QGC uses the fragmented path for len >= 180 (single 180-byte fragment)
        data = os.urandom(180)
        frags = fragment_rtcm(data, 0)
        self.assertEqual(len(frags), 1)
        flags, length, _ = frags[0]
        self.assertEqual(flags & 0x01, 1)  # fragmented
        self.assertEqual((flags >> 1) & 0x03, 0)  # fragment id 0
        self.assertEqual(length, 180)
        self.assertEqual(reassemble(frags), data)

    def test_two_fragments(self):
        data = os.urandom(250)
        frags = fragment_rtcm(data, 5)
        self.assertEqual(len(frags), 2)
        self.assertEqual([f[1] for f in frags], [180, 70])
        for i, (flags, _l, _d) in enumerate(frags):
            self.assertEqual(flags & 0x01, 1)
            self.assertEqual((flags >> 1) & 0x03, i)  # fragment id increments
            self.assertEqual(flags >> 3, 5)  # same sequence id
        self.assertEqual(reassemble(frags), data)

    def test_exact_multiple_360(self):
        data = os.urandom(360)
        frags = fragment_rtcm(data, 1)
        self.assertEqual([f[1] for f in frags], [180, 180])
        self.assertEqual(reassemble(frags), data)

    def test_max_720_four_fragments(self):
        data = os.urandom(MAX_SET_BYTES)
        frags = fragment_rtcm(data, 1)
        self.assertEqual(len(frags), 4)
        self.assertEqual([f[1] for f in frags], [180, 180, 180, 180])
        self.assertEqual(reassemble(frags), data)

    def test_over_max_raises(self):
        with self.assertRaises(ValueError):
            fragment_rtcm(os.urandom(MAX_SET_BYTES + 1), 0)

    def test_sequence_id_masked(self):
        frags = fragment_rtcm(os.urandom(10), 33)  # 33 & 0x1F == 1
        self.assertEqual(frags[0][0] >> 3, 1)

    def test_split_chunks(self):
        data = os.urandom(1000)
        chunks = list(split_chunks(data))
        self.assertEqual([len(c) for c in chunks], [720, 280])
        self.assertEqual(b"".join(chunks), data)


@unittest.skipUnless(HAS_MAVLINK, "pymavlink not installed")
class TestInjectorLoopback(unittest.TestCase):

    def test_roundtrip_over_udp(self):
        from pygpsclient.rtcm_mavlink import RTCMMAVLinkInjector

        # receiver first so the socket is bound before we send
        rx = mavutil.mavlink_connection("udpin:127.0.0.1:14599")
        injector = RTCMMAVLinkInjector("udpout:127.0.0.1:14599")
        try:
            payloads = [os.urandom(90), os.urandom(180), os.urandom(400)]
            for p in payloads:
                injector.send_rtcm(p)

            # collect GPS_RTCM_DATA messages and regroup into sets by sequence id
            received = []
            deadline_msgs = 1 + 1 + 3  # 90->1, 180->1, 400->3 fragments
            for _ in range(deadline_msgs * 3):
                msg = rx.recv_match(type="GPS_RTCM_DATA", blocking=True, timeout=2.0)
                if msg is None:
                    break
                received.append(msg)
                if len(received) >= deadline_msgs:
                    break

            self.assertEqual(len(received), deadline_msgs)

            # group by sequence id (bits 3..7), order by fragment id (bits 1..2)
            sets: dict[int, list] = {}
            for m in received:
                seq = m.flags >> 3
                frag_id = (m.flags >> 1) & 0x03 if (m.flags & 0x01) else 0
                sets.setdefault(seq, []).append(
                    (frag_id, m.len, bytes(m.data[: m.len]))
                )
            rebuilt = []
            for seq in sorted(sets):
                frags = sorted(sets[seq], key=lambda t: t[0])
                rebuilt.append(b"".join(f[2] for f in frags))

            self.assertEqual(rebuilt, payloads)
        finally:
            injector.close()
            rx.close()


if __name__ == "__main__":
    unittest.main()
