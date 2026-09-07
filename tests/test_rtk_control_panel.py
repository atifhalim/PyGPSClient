"""Tests for the RTK control-panel status parsing/helpers (no GUI needed)."""

from unittest import mock

import pygpsclient.rtk_control_panel as cp


def test_parse_waiting_dgps_line():
    line = "[waiting...] sets=470 GPS DGPS sats=32 rtk_rate=0"
    s = cp.parse_status_line(line)
    assert s["active"] is False
    assert s["sets"] == 470
    assert s["fix"] == "DGPS"
    assert s["sats"] == 32


def test_parse_rtk_active_fixed_line():
    line = "[RTK ACTIVE] sets=1240 GPS RTK FIXED sats=28"
    s = cp.parse_status_line(line)
    assert s["active"] is True
    assert s["sets"] == 1240
    assert s["fix"] == "RTK FIXED"
    assert s["sats"] == 28


def test_parse_empty_line():
    s = cp.parse_status_line("")
    assert s == {"active": False, "sets": None, "fix": None, "sats": None, "raw": ""}


def test_fix_color_paused_when_injector_down():
    assert cp.fix_color("RTK FIXED", injector_running=False) == cp.COLOR_PAUSED


def test_fix_color_by_state():
    assert cp.fix_color("RTK FIXED", True) == cp.COLOR_FIXED
    assert cp.fix_color("RTK FLOAT", True) == cp.COLOR_FLOAT
    assert cp.fix_color("DGPS", True) == cp.COLOR_DGPS
    assert cp.fix_color("3D", True) == cp.COLOR_3D
    assert cp.fix_color("NO FIX", True) == cp.COLOR_NONE


def test_fix_label_paused():
    assert cp.fix_label("DGPS", injector_running=False) == "INJECTION PAUSED"
    assert cp.fix_label("DGPS", injector_running=True) == "DGPS"
    assert cp.fix_label(None, injector_running=True) == "NO DATA"


def test_service_state_reads_systemctl():
    fake = mock.Mock(stdout="active\n")
    with mock.patch.object(cp.subprocess, "run", return_value=fake):
        assert cp.service_state("rtcm-mavlink.service") == "active"


def test_service_state_missing_binary():
    with mock.patch.object(cp.subprocess, "run", side_effect=FileNotFoundError):
        assert cp.service_state("x.service") == "unknown"


def test_journal_tail_missing_binary():
    with mock.patch.object(cp.subprocess, "run", side_effect=FileNotFoundError):
        assert cp.journal_tail() == ""
