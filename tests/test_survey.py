"""
test_survey.py

Unit tests for the Survey / GCP capture helper modules
(qgroundcontrol_config and gcp_handler).

Created on 2 Sep 2026

:author: semuadmin (Steve Smith)
:copyright: 2020 semuadmin
:license: BSD 3-Clause
"""

# pylint: disable=missing-docstring

import os
import unittest
from configparser import ConfigParser
from tempfile import TemporaryDirectory

from pygpsclient.gcp_handler import (
    GCP_CSV_FIELDS,
    append_gcp_csv,
    write_gcp_list,
)
from pygpsclient.qgroundcontrol_config import (
    KEY_ACC,
    KEY_ALT,
    KEY_LAT,
    KEY_LON,
    KEY_USEFIXED,
    RTK_GROUP,
    default_qgc_ini_path,
    write_base_position,
)

LAT = 53.450012345
LON = -2.312345678
HAE = 74.321
MSL = 26.789
ACC = 0.014


class TestQGCConfig(unittest.TestCase):

    def test_default_path(self):
        path = default_qgc_ini_path()
        self.assertTrue(str(path).endswith("QGroundControl.ini"))
        self.assertIn("QGroundControl.org", str(path))

    def test_write_new_file(self):
        with TemporaryDirectory() as tmp:
            ini = os.path.join(tmp, "sub", "QGroundControl.ini")
            write_base_position(ini, LAT, LON, HAE, ACC)
            cfg = ConfigParser(interpolation=None)
            cfg.optionxform = str
            cfg.read(ini, encoding="utf-8")
            self.assertEqual(cfg.get(RTK_GROUP, KEY_USEFIXED), "1")
            self.assertAlmostEqual(float(cfg.get(RTK_GROUP, KEY_LAT)), LAT, places=8)
            self.assertAlmostEqual(float(cfg.get(RTK_GROUP, KEY_LON)), LON, places=8)
            self.assertAlmostEqual(float(cfg.get(RTK_GROUP, KEY_ALT)), HAE, places=3)
            self.assertAlmostEqual(float(cfg.get(RTK_GROUP, KEY_ACC)), ACC, places=3)

    def test_preserves_other_settings(self):
        with TemporaryDirectory() as tmp:
            ini = os.path.join(tmp, "QGroundControl.ini")
            # simulate an existing QGC ini with unrelated settings, including
            # a value containing '%' which would break configparser
            # interpolation if not disabled
            with open(ini, "w", encoding="utf-8") as file:
                file.write("[General]\n")
                file.write("savePath=/home/user/%Y-data\n")
                file.write("[RTK]\n")
                file.write("surveyInAccuracyLimit=2\n")
            write_base_position(ini, LAT, LON, HAE, ACC)
            cfg = ConfigParser(interpolation=None)
            cfg.optionxform = str
            cfg.read(ini, encoding="utf-8")
            # unrelated settings survive
            self.assertEqual(cfg.get("General", "savePath"), "/home/user/%Y-data")
            self.assertEqual(cfg.get(RTK_GROUP, "surveyInAccuracyLimit"), "2")
            # new values applied
            self.assertEqual(cfg.get(RTK_GROUP, KEY_USEFIXED), "1")
            self.assertAlmostEqual(float(cfg.get(RTK_GROUP, KEY_LAT)), LAT, places=8)

    def test_no_space_delimiter(self):
        with TemporaryDirectory() as tmp:
            ini = os.path.join(tmp, "QGroundControl.ini")
            write_base_position(ini, LAT, LON, HAE, ACC)
            with open(ini, encoding="utf-8") as file:
                content = file.read()
            # Qt QSettings ini format uses key=value with no surrounding spaces
            self.assertIn(f"{KEY_USEFIXED}=1", content)
            self.assertNotIn(" = ", content)

    def test_invalid_lat(self):
        with TemporaryDirectory() as tmp:
            ini = os.path.join(tmp, "QGroundControl.ini")
            with self.assertRaises(ValueError):
                write_base_position(ini, 95.0, LON, HAE, ACC)

    def test_invalid_lon(self):
        with TemporaryDirectory() as tmp:
            ini = os.path.join(tmp, "QGroundControl.ini")
            with self.assertRaises(ValueError):
                write_base_position(ini, LAT, 190.0, HAE, ACC)


class TestGCPHandler(unittest.TestCase):

    def _gcp(self, name, lat=LAT, lon=LON):
        return {
            "name": name,
            "lat": lat,
            "lon": lon,
            "hae": HAE,
            "msl": MSL,
            "hacc": 0.012,
            "vacc": 0.018,
            "fix": "RTK FIXED",
            "sip": 32,
            "utc": "12:34:56",
            "datetime": "2026-09-02T12:34:56Z",
        }

    def test_csv_header_and_append(self):
        with TemporaryDirectory() as tmp:
            csvf = os.path.join(tmp, "survey.csv")
            append_gcp_csv(csvf, self._gcp("GCP1"))
            append_gcp_csv(csvf, self._gcp("GCP2"))
            with open(csvf, encoding="utf-8") as file:
                lines = [ln.rstrip("\n") for ln in file if ln.strip()]
            # one header + two data rows
            self.assertEqual(len(lines), 3)
            self.assertEqual(lines[0], ",".join(GCP_CSV_FIELDS))
            self.assertTrue(lines[1].startswith("GCP1,"))
            self.assertTrue(lines[2].startswith("GCP2,"))

    def test_gcp_list_format(self):
        with TemporaryDirectory() as tmp:
            txt = os.path.join(tmp, "gcp_list.txt")
            write_gcp_list(txt, [self._gcp("GCP1"), self._gcp("GCP 2")])
            with open(txt, encoding="utf-8") as file:
                lines = [ln.rstrip("\n") for ln in file if ln.strip()]
            self.assertEqual(lines[0], "EPSG:4326")
            # ODM geographic order is lon lat alt ...
            first = lines[1].split()
            self.assertAlmostEqual(float(first[0]), LON, places=8)
            self.assertAlmostEqual(float(first[1]), LAT, places=8)
            self.assertAlmostEqual(float(first[2]), HAE, places=3)
            # spaces in names are replaced so columns stay parseable
            self.assertTrue(lines[2].endswith("GCP_2"))

    def test_gcp_list_alt_key_msl(self):
        with TemporaryDirectory() as tmp:
            txt = os.path.join(tmp, "gcp_list.txt")
            write_gcp_list(txt, [self._gcp("GCP1")], alt_key="msl")
            with open(txt, encoding="utf-8") as file:
                lines = [ln.rstrip("\n") for ln in file if ln.strip()]
            self.assertAlmostEqual(float(lines[1].split()[2]), MSL, places=3)


if __name__ == "__main__":
    unittest.main()
