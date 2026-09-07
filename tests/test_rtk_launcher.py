"""Tests for the pygpsclient-rtk GUI launcher (rtk_launcher)."""

from unittest import mock

import pygpsclient.rtk_launcher as rl


def test_start_services_orders_port_handoff():
    # Stop the port-holders, run the one-shot base config while the port is
    # free, then bring the relay + injector back up.
    with mock.patch.object(rl.subprocess, "call", return_value=0) as call:
        assert rl.start_services() == 0
    assert call.call_args_list == [
        mock.call(["systemctl", "--user", "stop", rl.INJECTOR_UNIT, rl.RELAY_UNIT]),
        mock.call(["systemctl", "--user", "restart", rl.BASE_SETUP_UNIT]),
        mock.call(
            ["systemctl", "--user", "restart", rl.RELAY_UNIT, rl.INJECTOR_UNIT]
        ),
    ]


def test_start_services_propagates_failure():
    # Non-zero from the base-setup restart is surfaced.
    with mock.patch.object(rl.subprocess, "call", side_effect=[0, 1, 0]):
        assert rl.start_services() == 1


def test_stop_services():
    with mock.patch.object(rl.subprocess, "call", return_value=0) as call:
        assert rl.stop_services() == 0
    call.assert_called_once_with(["systemctl", "--user", "stop", *rl.RTK_UNITS])


def test_systemctl_missing_binary():
    with mock.patch.object(rl.subprocess, "call", side_effect=FileNotFoundError):
        assert rl._systemctl("restart") == 127


def test_main_stop_does_not_launch_gui():
    with mock.patch.object(rl.sys, "argv", ["pygpsclient-rtk", "stop"]), mock.patch.object(
        rl, "stop_services", return_value=0
    ) as stop, mock.patch.object(rl, "launch_gui") as gui:
        assert rl.main() == 0
    stop.assert_called_once()
    gui.assert_not_called()


def test_main_status_does_not_launch_gui():
    with mock.patch.object(rl.sys, "argv", ["pygpsclient-rtk", "status"]), mock.patch.object(
        rl, "status_services", return_value=0
    ) as status, mock.patch.object(rl, "launch_gui") as gui:
        assert rl.main() == 0
    status.assert_called_once()
    gui.assert_not_called()


def test_main_starts_services_then_launches_gui():
    with mock.patch.object(rl.sys, "argv", ["pygpsclient-rtk"]), mock.patch.object(
        rl, "start_services", return_value=0
    ) as start, mock.patch.object(rl, "launch_gui", return_value=0) as gui:
        assert rl.main() == 0
    start.assert_called_once()
    gui.assert_called_once_with([])


def test_main_launches_gui_even_if_services_fail():
    with mock.patch.object(rl.sys, "argv", ["pygpsclient-rtk"]), mock.patch.object(
        rl, "start_services", return_value=1
    ), mock.patch.object(rl, "launch_gui", return_value=0) as gui:
        assert rl.main() == 0
    gui.assert_called_once()


def test_main_strips_explicit_start_subcommand():
    with mock.patch.object(
        rl.sys, "argv", ["pygpsclient-rtk", "start", "--foo"]
    ), mock.patch.object(rl, "start_services", return_value=0), mock.patch.object(
        rl, "launch_gui", return_value=0
    ) as gui:
        assert rl.main() == 0
    gui.assert_called_once_with(["--foo"])


def test_pause_injection_stops_only_injector():
    with mock.patch.object(rl.subprocess, "call", return_value=0) as call, mock.patch.object(
        rl, "_notify"
    ):
        assert rl.pause_injection() == 0
    call.assert_called_once_with(["systemctl", "--user", "stop", rl.INJECTOR_UNIT])


def test_resume_injection_starts_only_injector():
    with mock.patch.object(rl.subprocess, "call", return_value=0) as call, mock.patch.object(
        rl, "_notify"
    ):
        assert rl.resume_injection() == 0
    call.assert_called_once_with(["systemctl", "--user", "start", rl.INJECTOR_UNIT])


def test_toggle_pauses_when_active():
    with mock.patch.object(rl, "injection_active", return_value=True), mock.patch.object(
        rl, "pause_injection", return_value=0
    ) as pause, mock.patch.object(rl, "resume_injection") as resume:
        assert rl.toggle_injection() == 0
    pause.assert_called_once()
    resume.assert_not_called()


def test_toggle_resumes_when_inactive():
    with mock.patch.object(rl, "injection_active", return_value=False), mock.patch.object(
        rl, "resume_injection", return_value=0
    ) as resume, mock.patch.object(rl, "pause_injection") as pause:
        assert rl.toggle_injection() == 0
    resume.assert_called_once()
    pause.assert_not_called()


def test_main_pause_does_not_launch_gui():
    with mock.patch.object(rl.sys, "argv", ["pygpsclient-rtk", "pause"]), mock.patch.object(
        rl, "pause_injection", return_value=0
    ) as pause, mock.patch.object(rl, "launch_gui") as gui:
        assert rl.main() == 0
    pause.assert_called_once()
    gui.assert_not_called()


def test_notify_missing_binary_is_silent():
    with mock.patch.object(rl.subprocess, "call", side_effect=FileNotFoundError):
        rl._notify("x", "y")  # must not raise


def test_launch_gui_execs_console_script():
    with mock.patch.object(rl.shutil, "which", return_value="/usr/bin/pygpsclient"), mock.patch.object(
        rl.os, "execvp"
    ) as execvp:
        rl.launch_gui(["--arg"])
    execvp.assert_called_once_with(
        "/usr/bin/pygpsclient", ["/usr/bin/pygpsclient", "--arg"]
    )
