"""
RTK Correction Pipeline - control panel GUI.

A small, touchscreen-friendly window that shows the live state of the RTK
pipeline and gives you separate Start / Pause buttons - so you don't need a
terminal or `journalctl` to run it.

It shows:

* a big, colour-coded fix state (NO FIX / 3D / DGPS / RTK FLOAT / RTK FIXED),
  plus satellites and the running RTCM "sets" counter, parsed from the
  injector's live log;
* the three service indicators (base config, relay, injector);
* a rolling tail of the injector log (what `journalctl -f` showed);
* buttons: Start pipeline, Pause / Resume injection, Stop pipeline.

The buttons reuse the same systemd user services the launcher manages, so this
panel and `pygpsclient-rtk` stay perfectly in sync.

Created by semuconsulting fork (atifhalim/PyGPSClient).
"""

from __future__ import annotations

import re
import subprocess
import tkinter as tk
from tkinter import font as tkfont

from pygpsclient.rtk_launcher import (
    BASE_SETUP_UNIT,
    INJECTOR_UNIT,
    RELAY_UNIT,
    pause_injection,
    resume_injection,
    start_services,
    stop_services,
)

# Colours for each fix state (dark-panel friendly).
COLOR_FIXED = "#2ea043"  # RTK FIXED - green
COLOR_FLOAT = "#3fb950"  # RTK FLOAT - light green
COLOR_DGPS = "#d29922"  # DGPS - amber
COLOR_3D = "#58a6ff"  # 3D - blue
COLOR_NONE = "#f85149"  # no fix / no data - red
COLOR_PAUSED = "#8b949e"  # injection paused - grey
COLOR_BG = "#0d1117"
COLOR_PANEL = "#161b22"
COLOR_TEXT = "#c9d1d9"

_SETS_RE = re.compile(r"sets=(\d+)")
_GPS_RE = re.compile(r"GPS\s+(.+?)\s+sats=(\d+)")
_SATS_RE = re.compile(r"sats=(\d+)")


def parse_status_line(line: str) -> dict:
    """Parse one injector monitor line into a status dict.

    Handles lines like::

        [waiting...] sets=470 GPS DGPS sats=32 rtk_rate=0
        [RTK ACTIVE #] sets=1240 GPS RTK FIXED sats=32

    Returns a dict with keys: active (bool), sets (int|None),
    fix (str|None), sats (int|None), raw (str).
    """
    line = (line or "").strip()
    sets_m = _SETS_RE.search(line)
    gps_m = _GPS_RE.search(line)
    sats_m = _SATS_RE.search(line)
    fix = gps_m.group(1).strip() if gps_m else None
    return {
        "active": "RTK ACTIVE" in line,
        "sets": int(sets_m.group(1)) if sets_m else None,
        "fix": fix,
        "sats": int(gps_m.group(2)) if gps_m else (int(sats_m.group(1)) if sats_m else None),
        "raw": line,
    }


def fix_color(fix: str | None, injector_running: bool) -> str:
    """Pick the display colour for a fix state."""
    if not injector_running:
        return COLOR_PAUSED
    text = (fix or "").upper()
    if "FIXED" in text:
        return COLOR_FIXED
    if "FLOAT" in text:
        return COLOR_FLOAT
    if "DGPS" in text or "DIFF" in text:
        return COLOR_DGPS
    if "3D" in text:
        return COLOR_3D
    return COLOR_NONE


def fix_label(fix: str | None, injector_running: bool) -> str:
    """Human label for the big status line."""
    if not injector_running:
        return "INJECTION PAUSED"
    return (fix or "NO DATA").upper()


def service_state(unit: str) -> str:
    """Return systemctl --user is-active for a unit ('active'/'inactive'/...)."""
    try:
        out = subprocess.run(
            ["systemctl", "--user", "is-active", unit],
            capture_output=True,
            text=True,
            check=False,
        )
        return out.stdout.strip() or "unknown"
    except FileNotFoundError:
        return "unknown"


def journal_tail(unit: str = INJECTOR_UNIT, lines: int = 12) -> str:
    """Return the last N log lines of a user unit (message text only)."""
    try:
        out = subprocess.run(
            [
                "journalctl",
                f"_SYSTEMD_USER_UNIT={unit}",
                "-n",
                str(lines),
                "--no-pager",
                "-o",
                "cat",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        return out.stdout.strip()
    except FileNotFoundError:
        return ""


class RTKControlPanel(tk.Tk):
    """The control-panel window."""

    REFRESH_MS = 1000

    def __init__(self) -> None:
        super().__init__()
        self.title("RTK Correction Pipeline")
        self.configure(bg=COLOR_BG)
        self.geometry("720x520")
        self.minsize(560, 440)
        self._build()
        self._refresh()

    def _build(self) -> None:
        big = tkfont.Font(family="DejaVu Sans", size=30, weight="bold")
        mid = tkfont.Font(family="DejaVu Sans", size=13)
        btnf = tkfont.Font(family="DejaVu Sans", size=13, weight="bold")
        mono = tkfont.Font(family="DejaVu Sans Mono", size=10)

        # --- big fix state ---
        self.state_lbl = tk.Label(
            self, text="…", font=big, bg=COLOR_BG, fg=COLOR_PAUSED, pady=10
        )
        self.state_lbl.pack(fill="x", padx=12, pady=(12, 0))

        self.metrics_lbl = tk.Label(
            self, text="sats – · sets –", font=mid, bg=COLOR_BG, fg=COLOR_TEXT
        )
        self.metrics_lbl.pack(fill="x")

        # --- service indicators ---
        svc = tk.Frame(self, bg=COLOR_BG)
        svc.pack(fill="x", padx=12, pady=8)
        self.svc_lbls: dict[str, tk.Label] = {}
        for key, name in (
            (BASE_SETUP_UNIT, "Base"),
            (RELAY_UNIT, "Relay"),
            (INJECTOR_UNIT, "Injector"),
        ):
            lbl = tk.Label(
                svc, text=f"{name}: …", font=mid, bg=COLOR_PANEL, fg=COLOR_TEXT,
                padx=10, pady=6, width=16,
            )
            lbl.pack(side="left", expand=True, fill="x", padx=4)
            self.svc_lbls[key] = lbl

        # --- buttons ---
        btns = tk.Frame(self, bg=COLOR_BG)
        btns.pack(fill="x", padx=12, pady=6)
        self.start_btn = tk.Button(
            btns, text="▶ Start pipeline", font=btnf, bg=COLOR_FIXED, fg="white",
            activebackground=COLOR_FLOAT, relief="flat", pady=12,
            command=self._start,
        )
        self.pause_btn = tk.Button(
            btns, text="⏸ Pause injection", font=btnf, bg=COLOR_DGPS, fg="white",
            activebackground="#b07d18", relief="flat", pady=12,
            command=self._pause,
        )
        self.resume_btn = tk.Button(
            btns, text="⏵ Resume injection", font=btnf, bg=COLOR_3D, fg="white",
            activebackground="#3b82f6", relief="flat", pady=12,
            command=self._resume,
        )
        self.stop_btn = tk.Button(
            btns, text="■ Stop pipeline", font=btnf, bg=COLOR_NONE, fg="white",
            activebackground="#b62324", relief="flat", pady=12,
            command=self._stop,
        )
        for b in (self.start_btn, self.pause_btn, self.resume_btn, self.stop_btn):
            b.pack(side="left", expand=True, fill="x", padx=4)

        # --- live log ---
        tk.Label(
            self, text="Live pipeline log", font=mid, bg=COLOR_BG, fg=COLOR_TEXT,
            anchor="w",
        ).pack(fill="x", padx=12, pady=(6, 0))
        self.log = tk.Text(
            self, height=10, bg="#010409", fg="#7ee787", font=mono,
            relief="flat", state="disabled", wrap="none",
        )
        self.log.pack(fill="both", expand=True, padx=12, pady=(0, 12))

    # --- button actions ---
    def _start(self) -> None:
        start_services()

    def _pause(self) -> None:
        pause_injection()

    def _resume(self) -> None:
        resume_injection()

    def _stop(self) -> None:
        stop_services()

    # --- refresh loop ---
    def _refresh(self) -> None:
        try:
            self._update()
        finally:
            self.after(self.REFRESH_MS, self._refresh)

    def _update(self) -> None:
        states = {u: service_state(u) for u in self.svc_lbls}
        injector_running = states.get(INJECTOR_UNIT) == "active"

        for unit, lbl in self.svc_lbls.items():
            st = states[unit]
            ok = st == "active"
            name = lbl.cget("text").split(":", 1)[0]
            lbl.configure(
                text=f"{name}: {'● up' if ok else '○ ' + st}",
                fg=COLOR_FIXED if ok else COLOR_NONE,
            )

        tail = journal_tail()
        last = tail.splitlines()[-1] if tail else ""
        status = parse_status_line(last)
        self.state_lbl.configure(
            text=fix_label(status["fix"], injector_running),
            fg=fix_color(status["fix"], injector_running),
        )
        sats = status["sats"]
        sets_ = status["sets"]
        self.metrics_lbl.configure(
            text=f"sats {sats if sats is not None else '–'} · "
            f"sets {sets_ if sets_ is not None else '–'}"
        )

        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.insert("end", tail)
        self.log.see("end")
        self.log.configure(state="disabled")


def main() -> int:
    """Entry point for ``pygpsclient-rtk-panel``."""
    RTKControlPanel().mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
