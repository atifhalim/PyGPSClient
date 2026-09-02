"""
survey_dialog.py

Survey / GCP Capture dialog.

Captures the current (ideally RTK FIXED) GNSS position and routes it to two
sinks without manual copy/paste:

1. **Ground Control Points** - append each captured point to a survey CSV and
   export a WebODM / OpenDroneMap ``gcp_list.txt`` starter for photogrammetry.

2. **QGroundControl base** - write the position into QGroundControl.ini as the
   RTK "Use Specified Base Position", so QGC streams corrections referenced to
   an accurate coordinate.

The coordinate used for both sinks lives in the editable Latitude / Longitude /
Ellipsoidal height (HAE) fields. These can be populated from the live receiver
with the Capture button, or typed in by hand - the latter supports the PPK /
no-internet workflow, where the precise base coordinate only becomes available
later from post-processing.

A RAW logging helper enables the u-blox RXM-RAWX / RXM-SFRBX messages needed to
record raw observations for PPK (combine with the Recorder and RINEX tools).

Created on 2 Sep 2026

:author: semuadmin (Steve Smith)
:copyright: 2020 semuadmin
:license: BSD 3-Clause
"""

# pylint: disable=unused-argument

from datetime import datetime, timezone
from os import path as os_path
from tkinter import (
    EW,
    NSEW,
    Button,
    Entry,
    Label,
    LabelFrame,
    StringVar,
    TclError,
    Tk,
    W,
    filedialog,
)

try:
    from pyubx2 import SET_LAYER_RAM, TXN_NONE, UBXMessage

    HAS_UBX = True
except ImportError:  # pragma: no cover
    HAS_UBX = False

from pygpsclient.gcp_handler import append_gcp_csv, write_gcp_list
from pygpsclient.globals import (
    BGCOL,
    CLICK_CURSOR,
    ERRCOL,
    FGCOL,
    HOME,
    INFOCOL,
    OKCOL,
    PNTCOL,
)
from pygpsclient.qgroundcontrol_config import (
    default_qgc_ini_path,
    write_base_position,
)
from pygpsclient.strings import DLGTSURVEY
from pygpsclient.toplevel_dialog import ToplevelDialog

FIXED = "RTK FIXED"
LIVE_INTERVAL = 1000  # live readout refresh interval (ms)
# RXM messages required for PPK raw observation logging, enabled on USB & UART1
RAW_KEYS = (
    "CFG_MSGOUT_UBX_RXM_RAWX_USB",
    "CFG_MSGOUT_UBX_RXM_SFRBX_USB",
    "CFG_MSGOUT_UBX_RXM_RAWX_UART1",
    "CFG_MSGOUT_UBX_RXM_SFRBX_UART1",
)


class SurveyDialog(ToplevelDialog):
    """
    Survey / GCP Capture dialog.
    """

    def __init__(self, app: Tk, *args, **kwargs):
        """
        Constructor.

        :param Tk app: reference to main tkinter application
        """

        self.__app = app
        super().__init__(app, DLGTSURVEY)

        self._lat = StringVar()
        self._lon = StringVar()
        self._hae = StringVar()
        self._acc = StringVar()
        self._gcpname = StringVar()
        self._gcpfolder = StringVar(value=str(HOME))
        self._qgcini = StringVar(value=str(default_qgc_ini_path()))
        self._geoz = StringVar(value="hae")
        self._live = {
            "fix": "-",
            "sip": 0,
            "hacc": 0.0,
            "vacc": 0.0,
            "lat": 0.0,
            "lon": 0.0,
            "hae": 0.0,
            "msl": 0.0,
        }
        self._gcps = []  # in-memory list of captured GCP records
        self._live_lbls = {}
        self._after_id = None

        self._body()
        self._do_layout()
        self._update_live()
        self._finalise()

    def _body(self):
        """
        Set up frame and widgets.
        """

        # --- Live receiver readout -------------------------------------
        self._frm_live = LabelFrame(
            self.container, text="Live receiver", bg=BGCOL, fg=FGCOL
        )
        for i, key in enumerate(
            ("fix", "sip", "hacc", "vacc", "lat", "lon", "hae", "msl")
        ):
            cap = {
                "fix": "Fix",
                "sip": "Sats",
                "hacc": "hAcc (m)",
                "vacc": "vAcc (m)",
                "lat": "Lat",
                "lon": "Lon",
                "hae": "HAE (m)",
                "msl": "MSL (m)",
            }[key]
            Label(self._frm_live, text=cap, bg=BGCOL, fg=FGCOL, anchor=W).grid(
                column=0, row=i, sticky=W, padx=3
            )
            lbl = Label(self._frm_live, text="-", bg=BGCOL, fg=PNTCOL, anchor=W)
            lbl.grid(column=1, row=i, sticky=EW, padx=3)
            self._live_lbls[key] = lbl
        self._btn_capture = Button(
            self._frm_live,
            text="Capture ↓",
            width=12,
            command=self._on_capture,
            cursor=CLICK_CURSOR,
        )
        self._btn_capture.grid(column=0, row=8, columnspan=2, pady=4)

        # --- Editable coordinate (used for all outputs) ----------------
        self._frm_coord = LabelFrame(
            self.container, text="Coordinate (used for outputs)", bg=BGCOL, fg=FGCOL
        )
        for i, (cap, var) in enumerate(
            (
                ("Latitude", self._lat),
                ("Longitude", self._lon),
                ("Ellipsoidal ht HAE (m)", self._hae),
                ("Accuracy (m)", self._acc),
            )
        ):
            Label(self._frm_coord, text=cap, bg=BGCOL, fg=FGCOL, anchor=W).grid(
                column=0, row=i, sticky=W, padx=3
            )
            Entry(self._frm_coord, textvariable=var, relief="sunken", width=18).grid(
                column=1, row=i, sticky=EW, padx=3, pady=1
            )

        # --- Ground Control Points -------------------------------------
        self._frm_gcp = LabelFrame(
            self.container, text="Ground Control Point", bg=BGCOL, fg=FGCOL
        )
        Label(self._frm_gcp, text="Name", bg=BGCOL, fg=FGCOL, anchor=W).grid(
            column=0, row=0, sticky=W, padx=3
        )
        Entry(self._frm_gcp, textvariable=self._gcpname, relief="sunken").grid(
            column=1, row=0, sticky=EW, padx=3
        )
        self._btn_addgcp = Button(
            self._frm_gcp,
            text="Add GCP",
            command=self._on_add_gcp,
            cursor=CLICK_CURSOR,
        )
        self._btn_addgcp.grid(column=2, row=0, padx=3)
        self._lbl_count = Label(self._frm_gcp, text="0 points", bg=BGCOL, fg=PNTCOL)
        self._lbl_count.grid(column=0, row=1, sticky=W, padx=3)
        Label(self._frm_gcp, text="Folder", bg=BGCOL, fg=FGCOL, anchor=W).grid(
            column=0, row=2, sticky=W, padx=3
        )
        Entry(self._frm_gcp, textvariable=self._gcpfolder, relief="sunken").grid(
            column=1, row=2, sticky=EW, padx=3
        )
        Button(self._frm_gcp, text="...", command=self._on_browse_folder, width=3).grid(
            column=2, row=2, padx=3
        )
        self._btn_export = Button(
            self._frm_gcp,
            text="Export gcp_list.txt",
            command=self._on_export_gcplist,
            cursor=CLICK_CURSOR,
        )
        self._btn_export.grid(column=1, row=3, sticky=W, padx=3, pady=2)

        # --- QGroundControl base ---------------------------------------
        self._frm_qgc = LabelFrame(
            self.container, text="QGroundControl base", bg=BGCOL, fg=FGCOL
        )
        Label(self._frm_qgc, text="QGC .ini", bg=BGCOL, fg=FGCOL, anchor=W).grid(
            column=0, row=0, sticky=W, padx=3
        )
        Entry(self._frm_qgc, textvariable=self._qgcini, relief="sunken").grid(
            column=1, row=0, sticky=EW, padx=3
        )
        Button(self._frm_qgc, text="...", command=self._on_browse_ini, width=3).grid(
            column=2, row=0, padx=3
        )
        self._btn_savebase = Button(
            self._frm_qgc,
            text="Save as QGC base",
            command=self._on_save_base,
            cursor=CLICK_CURSOR,
        )
        self._btn_savebase.grid(column=1, row=1, sticky=W, padx=3, pady=2)
        Label(
            self._frm_qgc,
            text="Close QGC before saving; restart it afterwards.",
            bg=BGCOL,
            fg=FGCOL,
            anchor=W,
        ).grid(column=0, row=2, columnspan=3, sticky=W, padx=3)

        # --- PPK / RAW logging -----------------------------------------
        self._frm_ppk = LabelFrame(
            self.container, text="PPK / RAW logging", bg=BGCOL, fg=FGCOL
        )
        self._btn_raw = Button(
            self._frm_ppk,
            text="Enable RXM RAW msgs",
            command=self._on_enable_raw,
            cursor=CLICK_CURSOR,
        )
        self._btn_raw.grid(column=0, row=0, padx=3, pady=2, sticky=W)
        Label(
            self._frm_ppk,
            text="Then Record the stream and convert to RINEX for post-processing.",
            bg=BGCOL,
            fg=FGCOL,
            anchor=W,
        ).grid(column=0, row=1, sticky=W, padx=3)

    def _do_layout(self):
        """
        Layout widgets.
        """

        self._frm_live.grid(column=0, row=0, sticky=NSEW, padx=3, pady=3)
        self._frm_coord.grid(column=1, row=0, sticky=NSEW, padx=3, pady=3)
        self._frm_gcp.grid(column=0, row=1, columnspan=2, sticky=EW, padx=3, pady=3)
        self._frm_qgc.grid(column=0, row=2, columnspan=2, sticky=EW, padx=3, pady=3)
        self._frm_ppk.grid(column=0, row=3, columnspan=2, sticky=EW, padx=3, pady=3)
        self._frm_coord.grid_columnconfigure(1, weight=1)
        self._frm_gcp.grid_columnconfigure(1, weight=1)
        self._frm_qgc.grid_columnconfigure(1, weight=1)
        self.container.grid_columnconfigure(0, weight=1)
        self.container.grid_columnconfigure(1, weight=1)

    def _update_live(self):
        """
        Poll gnss_status and refresh the live receiver readout.
        """

        try:
            if not self.winfo_exists():
                return
            gns = self.__app.gnss_status
            sep = gns.hae - gns.alt
            self._live.update(
                {
                    "fix": gns.fix,
                    "sip": gns.sip,
                    "hacc": gns.hacc,
                    "vacc": gns.vacc,
                    "lat": gns.lat,
                    "lon": gns.lon,
                    "hae": gns.hae,
                    "msl": gns.alt,
                    "sep": sep,
                    "diffage": gns.diff_age,
                }
            )
            fmt = {
                "fix": str,
                "sip": str,
                "hacc": lambda v: f"{v:.3f}",
                "vacc": lambda v: f"{v:.3f}",
                "lat": lambda v: f"{v:.9f}",
                "lon": lambda v: f"{v:.9f}",
                "hae": lambda v: f"{v:.3f}",
                "msl": lambda v: f"{v:.3f}",
            }
            for key, lbl in self._live_lbls.items():
                lbl["text"] = fmt[key](self._live[key])
            self._live_lbls["fix"]["fg"] = (
                OKCOL if self._live["fix"] == FIXED else ERRCOL
            )
            self._after_id = self.after(LIVE_INTERVAL, self._update_live)
        except TclError:  # dialog closed
            pass

    def _on_capture(self):
        """
        Copy the current live position into the editable coordinate fields.
        """

        self._lat.set(f"{self._live['lat']:.9f}")
        self._lon.set(f"{self._live['lon']:.9f}")
        self._hae.set(f"{self._live['hae']:.3f}")
        self._acc.set(f"{self._live['hacc']:.3f}")
        if self._live["fix"] == FIXED:
            self.set_status_label("Captured RTK FIXED position", OKCOL)
        else:
            self.set_status_label(
                f"Captured position but fix is '{self._live['fix']}', not RTK FIXED",
                ERRCOL,
            )

    def _current_record(self) -> dict:
        """
        Build a GCP/base record from the editable coordinate fields plus the
        latest live metadata.

        :return: record dict (see gcp_handler.GCP_CSV_FIELDS) or None on error
        :rtype: dict
        """

        try:
            lat = float(self._lat.get())
            lon = float(self._lon.get())
            hae = float(self._hae.get())
        except ValueError:
            self.set_status_label("Enter valid numeric lat/lon/HAE", ERRCOL)
            return None
        try:
            acc = float(self._acc.get())
        except ValueError:
            acc = self._live["hacc"]
        now = datetime.now(timezone.utc)
        return {
            "name": self._gcpname.get().strip() or f"GCP{len(self._gcps) + 1}",
            "lat": lat,
            "lon": lon,
            "hae": hae,
            "msl": self._live["msl"],
            "hacc": acc,
            "vacc": self._live["vacc"],
            "fix": self._live["fix"],
            "sip": self._live["sip"],
            "utc": now.strftime("%H:%M:%S"),
            "datetime": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }

    def _on_add_gcp(self):
        """
        Append the current coordinate as a GCP to the in-memory list and CSV.
        """

        rec = self._current_record()
        if rec is None:
            return
        csvfile = os_path.join(self._gcpfolder.get(), "gcp_survey.csv")
        try:
            append_gcp_csv(csvfile, rec)
        except OSError as err:
            self.set_status_label(f"ERROR writing CSV: {err}", ERRCOL)
            return
        self._gcps.append(rec)
        self._lbl_count["text"] = f"{len(self._gcps)} points"
        self._gcpname.set("")
        col = OKCOL if rec["fix"] == FIXED else ERRCOL
        self.set_status_label(f"Added {rec['name']} to {csvfile}", col)

    def _on_export_gcplist(self):
        """
        Export captured GCPs as a WebODM / ODM gcp_list.txt starter.
        """

        if not self._gcps:
            self.set_status_label("No GCPs captured yet", ERRCOL)
            return
        txtfile = os_path.join(self._gcpfolder.get(), "gcp_list.txt")
        try:
            write_gcp_list(txtfile, self._gcps, alt_key=self._geoz.get())
        except OSError as err:
            self.set_status_label(f"ERROR writing gcp_list: {err}", ERRCOL)
            return
        self.set_status_label(
            f"Exported {len(self._gcps)} GCPs to {txtfile} "
            "(complete pixel/image columns in WebODM)",
            OKCOL,
        )

    def _on_save_base(self):
        """
        Write the current coordinate into QGroundControl.ini as the RTK
        fixed base position.
        """

        rec = self._current_record()
        if rec is None:
            return
        if rec["fix"] != FIXED:
            self.set_status_label(
                f"WARNING: saving base while fix is '{rec['fix']}', not RTK FIXED",
                ERRCOL,
            )
        try:
            written = write_base_position(
                self._qgcini.get(), rec["lat"], rec["lon"], rec["hae"], rec["hacc"]
            )
        except (OSError, ValueError) as err:
            self.set_status_label(f"ERROR writing QGC ini: {err}", ERRCOL)
            return
        self.set_status_label(
            f"Base written to {written} - restart QGC to apply", OKCOL
        )

    def _on_enable_raw(self):
        """
        Enable u-blox RXM-RAWX / RXM-SFRBX raw observation messages for PPK.
        """

        if not HAS_UBX:  # pragma: no cover
            self.set_status_label("pyubx2 not available", ERRCOL)
            return
        try:
            cfgdata = [(key, 1) for key in RAW_KEYS]
            msg = UBXMessage.config_set(SET_LAYER_RAM, TXN_NONE, cfgdata)
            self.__app.send_to_device(msg.serialize())
            self.set_status_label(
                "Sent RXM-RAWX/SFRBX enable (RAM) - now Record the stream", INFOCOL
            )
        except Exception as err:  # pylint: disable=broad-exception-caught
            self.set_status_label(f"ERROR enabling RAW: {err}", ERRCOL)

    def _on_browse_folder(self):
        """
        Choose the GCP output folder.
        """

        folder = filedialog.askdirectory(
            parent=self.container, initialdir=self._gcpfolder.get(), mustexist=True
        )
        if folder not in ((), ""):
            self._gcpfolder.set(folder)

    def _on_browse_ini(self):
        """
        Choose the QGroundControl.ini file.
        """

        ini = filedialog.askopenfilename(
            parent=self.container,
            initialdir=os_path.dirname(self._qgcini.get()) or HOME,
            filetypes=(("ini files", "*.ini"), ("all files", "*.*")),
        )
        if ini not in ((), ""):
            self._qgcini.set(ini)

    def on_exit(self, *args, **kwargs):
        """
        Cancel the live poll then close.
        """

        if self._after_id is not None:
            try:
                self.after_cancel(self._after_id)
            except TclError:
                pass
        super().on_exit(*args, **kwargs)
