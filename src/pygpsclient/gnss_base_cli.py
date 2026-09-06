"""
gnss_base_cli.py

Command line entry point to configure a u-blox (F9P / Here 4) receiver as an
RTK base station - see :mod:`pygpsclient.gnss_base`.

Examples::

    # Survey-in base on USB, persist so it comes back configured after power-cycle
    gnss-base --port /dev/rtk-base --mode svin --svin-dur 60 --svin-acc 2.0 --persist

    # Fixed base at a known surveyed monument
    gnss-base --port /dev/rtk-base --mode fixed \
        --lat 53.450012345 --lon -2.312345678 --height 74.321 --persist

    # Back to rover
    gnss-base --port /dev/rtk-base --mode disable

Used both interactively and by the rtcm-base-setup systemd service.

Created on 6 Sep 2026

:author: semuadmin (Steve Smith)
:copyright: 2020 semuadmin
:license: BSD 3-Clause
"""

from __future__ import annotations

from argparse import ArgumentParser
from time import monotonic, sleep

from pygpsclient.gnss_base import build_base_config, configure_base


def _wait_svin(port: str, baud: int, timeout: float) -> bool:
    """
    Poll UBX-NAV-SVIN until the survey-in is valid or timeout, printing progress.

    :param str port: serial device
    :param int baud: baud rate
    :param float timeout: seconds to wait
    :return: True if survey-in became valid
    :rtype: bool
    """

    # pylint: disable=import-outside-toplevel
    from pyubx2 import POLL, UBXMessage, UBXReader
    from serial import Serial

    with Serial(port, baud, timeout=1) as stream:
        ubr = UBXReader(stream)
        deadline = monotonic() + timeout
        while monotonic() < deadline:
            stream.write(UBXMessage("NAV", "NAV-SVIN", POLL).serialize())
            inner = monotonic() + 2
            while monotonic() < inner:
                try:
                    _raw, parsed = ubr.read()
                except Exception:  # pylint: disable=broad-exception-caught
                    continue
                if getattr(parsed, "identity", "") == "NAV-SVIN":
                    print(
                        f"  survey-in dur={parsed.dur}s "
                        f"meanAcc={parsed.meanAcc * 0.0001:.2f}m valid={parsed.valid}",
                        flush=True,
                    )
                    if parsed.valid:
                        return True
                    break
            sleep(2)
    return False


def main():
    """CLI entry point."""

    ap = ArgumentParser(description="Configure a u-blox receiver as an RTK base")
    ap.add_argument("--port", default="/dev/rtk-base", help="serial device")
    ap.add_argument("--baud", type=int, default=115200, help="baud (USB ignores)")
    ap.add_argument("--mode", choices=("svin", "fixed", "disable"), default="svin")
    ap.add_argument("--svin-dur", type=int, default=60, help="survey-in min secs")
    ap.add_argument("--svin-acc", type=float, default=2.0, help="survey-in acc (m)")
    ap.add_argument("--lat", type=float, help="fixed mode latitude (deg)")
    ap.add_argument("--lon", type=float, help="fixed mode longitude (deg)")
    ap.add_argument("--height", type=float, help="fixed mode ellipsoidal height (m)")
    ap.add_argument("--fixed-acc", type=float, default=2.0, help="fixed acc (m)")
    ap.add_argument(
        "--ports", default="USB", help="comma list of RTCM out ports, e.g. USB,UART1"
    )
    ap.add_argument(
        "--persist", action="store_true", help="write to BBR+Flash (survive reboot)"
    )
    ap.add_argument(
        "--wait", type=float, default=0, help="poll survey-in until valid, up to N s"
    )
    args = ap.parse_args()

    ports = tuple(p.strip() for p in args.ports.split(",") if p.strip())
    cfg = build_base_config(
        mode=args.mode,
        svin_dur=args.svin_dur,
        svin_acc=args.svin_acc,
        lat=args.lat,
        lon=args.lon,
        height=args.height,
        fixed_acc=args.fixed_acc,
        ports=ports,
    )
    ack = configure_base(args.port, args.baud, cfg, persist=args.persist)
    print(f"base config ({args.mode}, ports={ports}, persist={args.persist}): {ack}")
    if ack != "ACK-ACK":
        raise SystemExit(1)

    if args.mode == "svin" and args.wait > 0:
        ok = _wait_svin(args.port, args.baud, args.wait)
        print("survey-in complete ✓" if ok else "survey-in not valid within timeout")
        raise SystemExit(0 if ok else 2)


if __name__ == "__main__":
    main()
