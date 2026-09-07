"""
base_coord.py

Capture an NTRIP/RTK-corrected position and turn it into a fixed base-station
coordinate for the RTK pipeline.

Workflow (driven from the NTRIP client dialog):

1. The receiver is fed NTRIP corrections and reaches RTK FIXED, so the app's
   live GNSS status holds a cm-accurate absolute position.
2. "Capture base position" snapshots that position into a CapturedPoint.
3. "Use as fixed base" writes it into the base-setup env file
   (``~/.config/rtcm-base.env``) as ``--mode fixed``, so the next time the RTK
   pipeline starts the base broadcasts from that exact surveyed point - no
   NTRIP/internet needed at deployment.

Heights are height-above-ellipsoid (HAE), which is exactly what u-blox
TMODE3 fixed mode expects - so capture -> fixed is a clean pass-through.

Created by semuconsulting fork (atifhalim/PyGPSClient).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

DEFAULT_PORT = "/dev/ttyACM0"
DEFAULT_FIXED_ACC = 0.10  # metres - declared accuracy of the fixed position
DEFAULT_ENV_PATH = "~/.config/rtcm-base.env"
# hAcc at/under this (metres) is a good survey; above it we still allow but warn.
GOOD_HACC = 0.05


@dataclass
class CapturedPoint:
    """A snapshot of an RTK-corrected position for use as a fixed base."""

    lat: float
    lon: float
    hae: float  # height above ellipsoid, metres
    fix: str
    hacc: float = 0.0  # horizontal accuracy, metres
    vacc: float = 0.0  # vertical accuracy, metres
    siv: int = 0  # satellites in view
    utc: str = ""


def is_rtk_fixed(fix: str) -> bool:
    """True if the fix string denotes an RTK FIXED solution."""
    return "FIXED" in (fix or "").upper()


def capture_quality(fix: str, hacc: float, good_hacc: float = GOOD_HACC) -> tuple[bool, str]:
    """Assess whether a fix is good enough to capture as a fixed base.

    Returns (ok, message). ok is True only at RTK FIXED; the message flags a
    loose horizontal accuracy even when fixed.
    """
    if not is_rtk_fixed(fix):
        return False, f"fix is {fix or 'NO FIX'} - wait for RTK FIXED before capturing"
    if hacc and hacc > good_hacc:
        return True, f"RTK FIXED, but hAcc {hacc:.3f} m is loose (> {good_hacc:.3f} m)"
    return True, "RTK FIXED"


def default_env_path() -> str:
    """Absolute path of the base-setup env file."""
    return os.path.expanduser(DEFAULT_ENV_PATH)


def existing_port(path: str, default: str = DEFAULT_PORT) -> str:
    """Read the ``--port`` from an existing env file, or return the default."""
    try:
        with open(path, "r", encoding="utf-8") as fhd:
            text = fhd.read()
    except OSError:
        return default
    match = re.search(r"--port\s+(\S+)", text)
    return match.group(1) if match else default


def format_fixed_base_args(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    lat: float,
    lon: float,
    hae: float,
    port: str = DEFAULT_PORT,
    fixed_acc: float = DEFAULT_FIXED_ACC,
    persist: bool = True,
) -> str:
    """Build the GNSS_BASE_ARGS value for a fixed base at the given coordinate."""
    line = (
        f"--port {port} --mode fixed "
        f"--lat {lat:.9f} --lon {lon:.9f} --height {hae:.3f} "
        f"--fixed-acc {fixed_acc:g}"
    )
    if persist:
        line += " --persist"
    return line


def write_fixed_base_env(
    point: CapturedPoint,
    path: str | None = None,
    fixed_acc: float = DEFAULT_FIXED_ACC,
) -> str:
    """Write the captured point into the base-setup env file as fixed mode.

    Preserves the receiver ``--port`` already configured in the file. Returns
    the GNSS_BASE_ARGS line written.
    """
    path = path or default_env_path()
    port = existing_port(path)
    args = format_fixed_base_args(point.lat, point.lon, point.hae, port, fixed_acc)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fhd:
        fhd.write("# Fixed base written by PyGPSClient NTRIP base capture\n")
        fhd.write(
            f"# captured at {point.utc or 'n/a'} - {point.fix}, "
            f"hAcc {point.hacc:.3f} m, vAcc {point.vacc:.3f} m, {point.siv} sats\n"
        )
        fhd.write(f"GNSS_BASE_ARGS={args}\n")
    return args
