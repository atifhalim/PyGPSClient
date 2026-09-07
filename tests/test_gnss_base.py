"""
test_gnss_base.py

Unit tests for the base-station config builder (pygpsclient.gnss_base). Pure
logic - no hardware. Also checks the config actually serialises via pyubx2.

Created on 6 Sep 2026

:author: semuadmin (Steve Smith)
:copyright: 2020 semuadmin
:license: BSD 3-Clause
"""

# pylint: disable=missing-docstring

import unittest

from pygpsclient.gnss_base import (
    RTCM_BASE_MSGS,
    _hp_split,
    build_base_config,
)


class TestHPSplit(unittest.TestCase):

    def test_positive_degrees(self):
        # 53.450012345 deg -> 1e-7 main + 1e-9 hp, recombine to same value
        main, hp = _hp_split(53.450012345, 1e7)
        self.assertEqual(main, 534500123)
        self.assertTrue(-99 <= hp <= 99)
        self.assertAlmostEqual(main / 1e7 + hp / 1e9, 53.450012345, places=9)

    def test_negative_degrees_same_sign(self):
        # negative values: main and hp must share sign (u-blox requirement)
        main, hp = _hp_split(-2.312345678, 1e7)
        self.assertLess(main, 0)
        self.assertLessEqual(hp, 0)
        self.assertAlmostEqual(main / 1e7 + hp / 1e9, -2.312345678, places=9)

    def test_height_cm_split(self):
        # 74.321 m -> cm main + 0.1mm hp
        main, hp = _hp_split(74.321, 100)
        self.assertEqual(main, 7432)
        self.assertTrue(-99 <= hp <= 99)
        # main is cm (/100 -> m); hp is 0.1 mm (/10000 -> m)
        self.assertAlmostEqual(main / 100 + hp / 10000, 74.321, places=5)


class TestBuildBaseConfig(unittest.TestCase):

    def _keys(self, cfg):
        return [k for k, _v in cfg]

    def test_svin(self):
        cfg = build_base_config(mode="svin", svin_dur=90, svin_acc=1.5)
        d = dict(cfg)
        self.assertEqual(d["CFG_TMODE_MODE"], 1)
        self.assertEqual(d["CFG_TMODE_SVIN_MIN_DUR"], 90)
        self.assertEqual(d["CFG_TMODE_SVIN_ACC_LIMIT"], 15000)  # 1.5 m in 0.1mm
        # RTCM messages enabled on USB by default
        for msg in RTCM_BASE_MSGS:
            self.assertEqual(d[f"CFG_MSGOUT_RTCM_3X_TYPE{msg}_USB"], 1)

    def test_fixed_requires_position(self):
        with self.assertRaises(ValueError):
            build_base_config(mode="fixed", lat=1.0, lon=2.0)  # no height

    def test_fixed(self):
        cfg = build_base_config(
            mode="fixed", lat=53.450012345, lon=-2.312345678, height=74.321
        )
        d = dict(cfg)
        self.assertEqual(d["CFG_TMODE_MODE"], 2)
        self.assertEqual(d["CFG_TMODE_POS_TYPE"], 1)
        self.assertEqual(d["CFG_TMODE_LAT"], 534500123)
        self.assertEqual(d["CFG_TMODE_HEIGHT"], 7432)
        self.assertIn("CFG_TMODE_LAT_HP", d)
        self.assertIn("CFG_TMODE_FIXED_POS_ACC", d)

    def test_disable(self):
        cfg = build_base_config(mode="disable")
        d = dict(cfg)
        self.assertEqual(d["CFG_TMODE_MODE"], 0)
        for msg in RTCM_BASE_MSGS:
            self.assertEqual(d[f"CFG_MSGOUT_RTCM_3X_TYPE{msg}_USB"], 0)

    def test_multiple_ports(self):
        cfg = build_base_config(mode="svin", ports=("USB", "UART1"))
        d = dict(cfg)
        self.assertEqual(d["CFG_MSGOUT_RTCM_3X_TYPE1005_USB"], 1)
        self.assertEqual(d["CFG_MSGOUT_RTCM_3X_TYPE1005_UART1"], 1)

    def test_unknown_mode(self):
        with self.assertRaises(ValueError):
            build_base_config(mode="bogus")

    def test_serialises_via_pyubx2(self):
        # the built config must be accepted by UBXMessage.config_set
        try:
            from pyubx2 import SET_LAYER_RAM, TXN_NONE, UBXMessage
        except ImportError:
            self.skipTest("pyubx2 not installed")
        for mode_kwargs in (
            {"mode": "svin"},
            {"mode": "fixed", "lat": 1.23, "lon": 4.56, "height": 100.0},
            {"mode": "disable"},
        ):
            cfg = build_base_config(**mode_kwargs)
            msg = UBXMessage.config_set(SET_LAYER_RAM, TXN_NONE, cfg)
            self.assertGreater(len(msg.serialize()), 8)


if __name__ == "__main__":
    unittest.main()
