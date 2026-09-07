"""
rtcm_mavlink_cli.py

Command line entry point for the headless RTCM3 -> MAVLink GPS_RTCM_DATA
injector (see :mod:`pygpsclient.rtcm_mavlink`).

Reads RTCM3 from a base station (serial F9P, a TCP caster such as RTKBase or
``gnssserver``, or a recorded file) and injects it onto a MAVLink link as
GPS_RTCM_DATA - e.g. onto a Herelink Wi-Fi stream (``udpout:<ip>:14552``) so
the autopilot relays it to the rover. Optionally reads RTK status back to prove
the corrections are being consumed.

Examples::

    # Replay a recorded base stream to a local loopback listener (test)
    rtcm-mavlink --rtcm-file base.rtcm3 --dest udpout:127.0.0.1:14555

    # Live: read F9P base on USB, inject onto Herelink, watch RTK status
    rtcm-mavlink --rtcm-serial /dev/ttyACM0 --baud 115200 \
        --dest udpout:192.168.144.11:14552 --monitor udpin:0.0.0.0:14550

Created on 5 Sep 2026

:author: semuadmin (Steve Smith)
:copyright: 2020 semuadmin
:license: BSD 3-Clause
"""

from __future__ import annotations

import threading
from argparse import ArgumentParser
from time import monotonic, sleep

from pygpsclient.rtcm_mavlink import (
    FIX_RTK_FIXED,
    FIX_RTK_FLOAT,
    RTCMMAVLinkInjector,
    RTKMonitor,
)

FIX_NAMES = {
    0: "NO GPS",
    1: "NO FIX",
    2: "2D",
    3: "3D",
    4: "DGPS",
    5: "RTK FLOAT",
    6: "RTK FIXED",
}


def _open_rtcm_source(args):
    """
    Open the RTCM3 source and return a byte stream object with ``read``.

    :param args: parsed CLI args
    :return: file-like stream yielding RTCM3 bytes
    """

    if args.rtcm_file:
        return open(args.rtcm_file, "rb")
    if args.rtcm_serial:
        from serial import Serial  # pylint: disable=import-outside-toplevel

        return Serial(args.rtcm_serial, args.baud, timeout=1)
    if args.rtcm_tcp:
        import socket  # pylint: disable=import-outside-toplevel

        host, port = args.rtcm_tcp.rsplit(":", 1)
        sock = socket.create_connection((host, int(port)), timeout=10)
        return sock.makefile("rb")
    raise SystemExit("No RTCM source: use --rtcm-file, --rtcm-serial or --rtcm-tcp")


def _monitor_loop(monitor: RTKMonitor, stop: threading.Event, injector):
    """
    Background loop: poll RTK status and print a live one-line summary.

    :param RTKMonitor monitor: monitor instance
    :param threading.Event stop: stop flag
    :param RTCMMAVLinkInjector injector: injector (for counters)
    """

    reached_float = reached_fixed = False
    while not stop.is_set():
        monitor.poll(timeout=1.0)
        best = monitor.best_fix()
        if best >= FIX_RTK_FLOAT and not reached_float:
            reached_float = True
        if best >= FIX_RTK_FIXED and not reached_fixed:
            reached_fixed = True
        parts = []
        for key, st in sorted(monitor.state.items()):
            parts.append(
                f"{key} {FIX_NAMES.get(st.get('fix_type', 0), '?')}"
                f" sats={st.get('sats', 0)}"
                f" rtk_rate={st.get('rtk_rate', 0)}"
            )
        verdict = "RTK ACTIVE ✓" if monitor.rtk_active() else "waiting..."
        print(
            f"  [{verdict}] sets={injector.sets_sent} " + " | ".join(parts),
            flush=True,
        )
    if reached_fixed:
        print("VALIDATION: rover reached RTK FIXED — corrections confirmed ✓")
    elif reached_float:
        print("VALIDATION: rover reached RTK FLOAT — corrections reaching rover ✓")
    else:
        print("VALIDATION: rover did NOT reach RTK — check the correction path ✗")


def main():
    """CLI entry point."""

    ap = ArgumentParser(description="Inject RTCM3 corrections onto MAVLink")
    src = ap.add_argument_group("RTCM source (choose one)")
    src.add_argument("--rtcm-file", help="replay RTCM3 from a file")
    src.add_argument("--rtcm-serial", help="read RTCM3 from a serial base (e.g. F9P)")
    src.add_argument("--baud", type=int, default=115200, help="serial baud rate")
    src.add_argument("--rtcm-tcp", help="read RTCM3 from host:port (caster/gnssserver)")
    ap.add_argument(
        "--dest",
        required=True,
        help="MAVLink output, e.g. udpout:192.168.144.11:14552 (Herelink)",
    )
    ap.add_argument(
        "--monitor",
        help="MAVLink input to read RTK status back, e.g. udpin:0.0.0.0:14550",
    )
    ap.add_argument("--duration", type=float, default=0, help="stop after N s (0=run)")
    args = ap.parse_args()

    injector = RTCMMAVLinkInjector(args.dest)
    print(f"Injecting RTCM3 -> {args.dest}")

    stop = threading.Event()
    mon_thread = None
    if args.monitor:
        monitor = RTKMonitor(args.monitor)
        mon_thread = threading.Thread(
            target=_monitor_loop, args=(monitor, stop, injector), daemon=True
        )
        mon_thread.start()
        print(f"Monitoring RTK status <- {args.monitor}")

    # pylint: disable=import-outside-toplevel
    from pyrtcm import ERR_IGNORE, RTCMReader

    stream = _open_rtcm_source(args)
    start = monotonic()
    try:
        # frame-only: we forward raw RTCM3 bytes, so never let a decode error
        # of an unsupported/odd message stop the correction stream
        rdr = RTCMReader(stream, parsed=False, quitonerror=ERR_IGNORE)
        for raw, _parsed in rdr:
            if raw:
                injector.send_rtcm(raw)
            if args.duration and (monotonic() - start) >= args.duration:
                break
            if args.rtcm_file:
                sleep(0.05)  # pace file replay so it resembles a live 1 Hz-ish base
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        if mon_thread:
            mon_thread.join(timeout=3)
        injector.close()
        try:
            stream.close()
        except OSError:
            pass
        print(
            f"Done: {injector.sets_sent} sets, {injector.messages_sent} "
            f"GPS_RTCM_DATA msgs, {injector.bytes_sent} bytes"
        )


if __name__ == "__main__":
    main()
