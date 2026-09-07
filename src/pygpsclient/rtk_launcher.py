"""
GUI launcher that also brings up the RTK correction services.

This is the entry point you open on the Jetson (its desktop icon runs it).
It (re)starts the RTK base-setup + RTCM->MAVLink injector as systemd *user*
services and then launches the PyGPSClient GUI.

Design (per the turnkey-but-manual requirement):

* Nothing is enabled at boot. The services come up only when you launch this,
  so you always open the application yourself.
* When the GUI starts, both services start automatically.
* systemd supervises them with ``Restart=always``, so a crash or a dropped
  Herelink link is recovered on its own.
* You keep full manual control from the attached touchscreen:
  ``pygpsclient-rtk stop`` / ``status``, or plain ``systemctl --user``.

Usage::

    pygpsclient-rtk            # start the services, then open the GUI
    pygpsclient-rtk stop       # stop all RTK services
    pygpsclient-rtk status     # show service status
    pygpsclient-rtk pause      # stop ONLY the injector (GUI + base feed stay up)
    pygpsclient-rtk resume     # start the injector again

Created by semuconsulting fork (atifhalim/PyGPSClient).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Sequence

# The RTK user services, in start order.
#  * base-setup (one-shot) configures the receiver as a base and releases the port
#  * gnss-server holds the serial port and re-serves the stream over local TCP
#  * rtcm-mavlink reads that TCP stream and injects RTCM onto the MAVLink link
# Serving over TCP lets the injector AND the PyGPSClient GUI share one receiver.
BASE_SETUP_UNIT = "rtcm-base-setup.service"
RELAY_UNIT = "gnss-server.service"
INJECTOR_UNIT = "rtcm-mavlink.service"
RTK_UNITS: tuple[str, ...] = (BASE_SETUP_UNIT, RELAY_UNIT, INJECTOR_UNIT)


def _systemctl(*args: str) -> int:
    """Run ``systemctl --user <args>``; return its exit code (127 if missing)."""
    try:
        return subprocess.call(["systemctl", "--user", *args])
    except FileNotFoundError:
        print("systemctl not found - is this a systemd user session?", file=sys.stderr)
        return 127


def start_services() -> int:
    """(Re)start the RTK user services in the right order.

    Only one process may hold the receiver's serial port, so the port-holders
    are stopped first, the one-shot base config runs while the port is free
    (it blocks to completion), then the relay and injector come back up.
    Idempotent - safe to call on every GUI launch.
    """
    _systemctl("stop", INJECTOR_UNIT, RELAY_UNIT)
    rc_base = _systemctl("restart", BASE_SETUP_UNIT)
    rc_run = _systemctl("restart", RELAY_UNIT, INJECTOR_UNIT)
    return rc_base or rc_run


def stop_services(units: Sequence[str] = RTK_UNITS) -> int:
    """Stop the RTK user services (manual control)."""
    return _systemctl("stop", *units)


def status_services(units: Sequence[str] = RTK_UNITS) -> int:
    """Print the status of the RTK user services."""
    return _systemctl("--no-pager", "status", *units)


def _notify(title: str, body: str = "") -> None:
    """Best-effort desktop notification (no-op if notify-send is absent)."""
    try:
        subprocess.call(["notify-send", title, body])
    except FileNotFoundError:
        pass


def pause_injection() -> int:
    """Stop ONLY the injector. The relay (and so the GUI's base feed and GCP
    capture) stays up - this just pauses corrections to the drone."""
    rc = _systemctl("stop", INJECTOR_UNIT)
    _notify("RTK injection paused", "Corrections to the drone are stopped.")
    return rc


def resume_injection() -> int:
    """Start the injector again (relay is left untouched)."""
    rc = _systemctl("start", INJECTOR_UNIT)
    _notify("RTK injection resumed", "Corrections to the drone are flowing.")
    return rc


def launch_gui(argv: Sequence[str]) -> int:
    """Launch the PyGPSClient GUI.

    Prefers exec-ing the installed ``pygpsclient`` console script (so this
    launcher process is replaced by the GUI). Falls back to calling the GUI
    entry point in-process if the script is not on PATH.
    """
    exe = shutil.which("pygpsclient") or os.environ.get("PYGPSCLIENT_BIN")
    if exe:
        # Replaces the current process; does not return on success. The
        # trailing return is reached only under test (where execvp is mocked).
        os.execvp(exe, [exe, *argv])
        return 0
    # Fallback: run the module's main() in this process.
    from pygpsclient.__main__ import main as gui_main

    sys.argv = ["pygpsclient", *argv]
    return int(gui_main() or 0)


def main() -> int:
    """CLI entry point for ``pygpsclient-rtk``."""
    argv = list(sys.argv[1:])

    if argv and argv[0] == "stop":
        return stop_services()
    if argv and argv[0] == "status":
        return status_services()
    if argv and argv[0] == "pause":
        return pause_injection()
    if argv and argv[0] == "resume":
        return resume_injection()
    if argv and argv[0] == "start":
        argv = argv[1:]  # explicit 'start' subcommand; rest are GUI args

    rc = start_services()
    if rc != 0:
        print(
            "warning: could not start the RTK services via 'systemctl --user' "
            f"(exit {rc}). Opening the GUI anyway - check "
            "'pygpsclient-rtk status'.",
            file=sys.stderr,
        )
    return launch_gui(argv)


if __name__ == "__main__":
    raise SystemExit(main())
