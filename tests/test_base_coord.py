"""Tests for base_coord (NTRIP capture -> fixed base env)."""

import os

import pygpsclient.base_coord as bc


def test_is_rtk_fixed():
    assert bc.is_rtk_fixed("RTK FIXED")
    assert not bc.is_rtk_fixed("RTK FLOAT")
    assert not bc.is_rtk_fixed("DGPS")
    assert not bc.is_rtk_fixed("")


def test_capture_quality_not_fixed():
    ok, msg = bc.capture_quality("DGPS", 0.02)
    assert ok is False
    assert "RTK FIXED" in msg


def test_capture_quality_fixed_tight():
    ok, msg = bc.capture_quality("RTK FIXED", 0.014)
    assert ok is True
    assert msg == "RTK FIXED"


def test_capture_quality_fixed_loose():
    ok, msg = bc.capture_quality("RTK FIXED", 0.20)
    assert ok is True
    assert "loose" in msg


def test_format_fixed_base_args():
    line = bc.format_fixed_base_args(53.450012345, -2.312345678, 74.321, port="/dev/ttyACM0")
    assert "--mode fixed" in line
    assert "--lat 53.450012345" in line
    assert "--lon -2.312345678" in line
    assert "--height 74.321" in line
    assert "--port /dev/ttyACM0" in line
    assert line.endswith("--persist")


def test_existing_port_reads_from_file(tmp_path):
    p = tmp_path / "rtcm-base.env"
    p.write_text("GNSS_BASE_ARGS=--port /dev/rtk-base --mode svin --persist\n")
    assert bc.existing_port(str(p)) == "/dev/rtk-base"


def test_existing_port_default_when_missing(tmp_path):
    assert bc.existing_port(str(tmp_path / "nope.env")) == bc.DEFAULT_PORT


def test_write_fixed_base_env_preserves_port(tmp_path):
    p = tmp_path / "sub" / "rtcm-base.env"
    p.parent.mkdir()
    p.write_text("GNSS_BASE_ARGS=--port /dev/rtk-base --mode svin --svin-dur 60 --persist\n")
    pt = bc.CapturedPoint(
        lat=53.1, lon=-2.2, hae=70.5, fix="RTK FIXED", hacc=0.012, vacc=0.02, siv=30, utc="12:00:00"
    )
    args = bc.write_fixed_base_env(pt, path=str(p))
    assert "--port /dev/rtk-base" in args
    assert "--mode fixed" in args
    written = p.read_text()
    assert "GNSS_BASE_ARGS=" in written
    assert "--lat 53.100000000" in written
    assert "captured at 12:00:00" in written


def test_write_fixed_base_env_creates_parent(tmp_path):
    p = tmp_path / "a" / "b" / "rtcm-base.env"
    pt = bc.CapturedPoint(lat=1.0, lon=2.0, hae=3.0, fix="RTK FIXED")
    bc.write_fixed_base_env(pt, path=str(p))
    assert os.path.exists(p)
