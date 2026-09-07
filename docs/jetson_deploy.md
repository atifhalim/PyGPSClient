# Deploy on Jetson Xavier NX (everything on one board)

This runs the whole solution on a Jetson Xavier NX — no laptop — with an
attached 10" touchscreen so you keep full manual control:

- You **open the application yourself** (a desktop icon, `pygpsclient-rtk`).
- Launching it **starts the RTK services automatically**: the base-setup
  (configures the u-blox base) and the **RTCM → MAVLink injector**.
- systemd then **keeps those services alive** — a crash, a USB dropout, or a
  dropped Herelink link is restarted on its own (`Restart=always`).
- **Nothing starts at boot.** The services come up only when you launch the
  app, and you can stop them any time.

They run as **systemd `--user` services**, so there is no `sudo`, no system
service account, and no boot enablement — everything is tied to your login
session and under your control.

## 1. Prerequisites

```bash
python3 --version         # must be >= 3.10
sudo apt update
sudo apt install -y python3-tk git   # python3-tk for the GUI
```

### Python on the Xavier NX (JetPack 5 ships Python 3.8)

PyGPSClient needs Python >= 3.10, and the modern SPDX `license` field in
`pyproject.toml` needs `setuptools >= 77` (Python >= 3.9). On JetPack 5 (Python
3.8) the install fails at build time with
``project.license` must be valid exactly by one definition`` — that is the old
setuptools, not a bug.

**The Jetson Xavier NX cannot run JetPack 6** (JetPack 6 supports only the Orin
series; Xavier tops out at JetPack 5.1.x), so upgrading the OS is not an option
here — you **add** a newer Python on top of JetPack 5. The most reliable aarch64
route is **Miniforge** (prebuilt, no compiling):

```bash
wget https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Linux-aarch64.sh
bash Miniforge3-Linux-aarch64.sh -b -p $HOME/miniforge3
$HOME/miniforge3/bin/conda create -y -n rtk python=3.11
source $HOME/miniforge3/bin/activate rtk
python --version          # 3.11.x
```

The rest of this guide assumes the `rtk` env lives at
`$HOME/miniforge3/envs/rtk` (so its programs are at
`$HOME/miniforge3/envs/rtk/bin/…`). If you use a plain venv instead, substitute
your venv's `bin` path everywhere below (and in the two `.service` files).

## 2. Install the fork

```bash
git clone https://github.com/atifhalim/PyGPSClient.git ~/PyGPSClient
cd ~/PyGPSClient
git checkout claude/pygpsclient-installation-tibd38
# with the 'rtk' env active:
pip install --upgrade pip setuptools wheel
pip install ".[mavlink]"          # includes pymavlink for the injector
```

This installs the console scripts into the env's `bin`: `pygpsclient`,
`pygpsclient-rtk` (the launcher), `gnss-base`, and `rtcm-mavlink`.

Confirm it runs on ARM64 (no display needed):

```bash
pip install pytest
pytest tests/ -q -o addopts=""
```

## 3. Serial access + a stable name for the base receiver

Your **login user** needs serial access — add it to `dialout` once, then log
out/in (or reboot):

```bash
sudo usermod -aG dialout "$USER"
```

So the services always find the base regardless of USB enumeration order,
install the udev rule (edit the ids first):

```bash
sudo cp packaging/udev/99-rtk-base.rules.example /etc/udev/rules.d/99-rtk-base.rules
sudoedit /etc/udev/rules.d/99-rtk-base.rules      # set idVendor/idProduct or serial
sudo udevadm control --reload-rules && sudo udevadm trigger
ls -l /dev/rtk-base                               # should point at the base's ttyACM*/ttyUSB*
```

(If you skip the udev rule, use the raw device path such as `/dev/ttyACM0` in
the env files below.)

## 4. Configure the correction services (env files)

The services read their arguments from two **user-owned** env files under
`~/.config` (no `sudo`). Copy the examples and edit them:

```bash
mkdir -p ~/.config
cp packaging/systemd/rtcm-base.env.example    ~/.config/rtcm-base.env
cp packaging/systemd/gnss-server.env.example  ~/.config/gnss-server.env
cp packaging/systemd/rtcm-mavlink.env.example ~/.config/rtcm-mavlink.env
$EDITOR ~/.config/rtcm-base.env               # base mode / port
$EDITOR ~/.config/gnss-server.env             # base port -> local TCP relay
$EDITOR ~/.config/rtcm-mavlink.env            # --dest (Herelink), --monitor
```

There are **three** services: `rtcm-base-setup` (one-shot, configures the
base), `gnss-server` (holds the USB serial port and re-serves the stream on
local TCP `50010`), and `rtcm-mavlink` (reads that TCP relay and injects RTCM
onto the Herelink link). Serving over TCP is what lets the injector **and** the
PyGPSClient GUI use the one receiver at the same time.

`~/.config/rtcm-base.env` — survey-in base (location changes each deployment):

```
GNSS_BASE_ARGS=--port /dev/rtk-base --mode svin --svin-dur 60 --svin-acc 2.0 --persist
```

`~/.config/rtcm-mavlink.env` — inject to the Herelink stream and self-monitor:

```
RTCM_MAVLINK_ARGS=--rtcm-serial /dev/ttyACM0 --baud 115200 --dest udpout:192.168.43.1:14550 --monitor udpin:0.0.0.0:14550
```

> **Finding the Herelink endpoint.** The Herelink controller broadcasts MAVLink
> over its own Wi-Fi hotspot. On the tested unit that is **`192.168.43.1:14550`**
> (the Android hotspot gateway, on the standard MAVLink/QGC UDP port 14550 — not
> 14552). If yours differs, connect the Jetson to the Herelink Wi-Fi and find
> where MAVLink actually arrives:
> ```
> sudo tcpdump -n -i wlan0 udp portrange 14550-14555   # source ip:port = the endpoint
> ```
> then use that IP/port for both `--dest` and `--monitor`. The Herelink must
> also be **sharing MAVLink over Wi-Fi** and the drone must be linked (its
> telemetry visible in the controller's QGC) or nothing is forwarded.

You can also configure the base by hand any time (the GUI or the launcher must
not hold the same serial port simultaneously):

```bash
# survey-in (location changes each deployment); --wait polls to completion:
gnss-base --port /dev/rtk-base --mode svin --svin-dur 60 --svin-acc 2.0 --wait 180
# ...or a fixed base at a known surveyed monument (cm-accurate, instant):
gnss-base --port /dev/rtk-base --mode fixed --lat 53.450012345 --lon -2.312345678 --height 74.321 --persist
# ...back to rover:
gnss-base --port /dev/rtk-base --mode disable
```

## 5. Install the user services (once)

The two units live under `~/.config/systemd/user/` and run as you. **They are
installed, not enabled** — nothing starts at boot; the launcher starts them.

```bash
mkdir -p ~/.config/systemd/user
cp packaging/systemd/user/rtcm-base-setup.service ~/.config/systemd/user/
cp packaging/systemd/user/gnss-server.service     ~/.config/systemd/user/
cp packaging/systemd/user/rtcm-mavlink.service    ~/.config/systemd/user/
systemctl --user daemon-reload
```

> The units point `ExecStart=` at `%h/miniforge3/envs/rtk/bin/…` (`%h` = your
> home). If you installed into a venv, edit the two files to your venv's `bin`.

Do **not** `systemctl --user enable` them — leaving them disabled is what keeps
them off at boot. (If you ever want them to survive a full logout without the
GUI open, that would need `loginctl enable-linger`; by design we don't.)

## 6. Launch it (the everyday flow)

Open the application yourself — the launcher starts both services, then opens
the GUI:

```bash
pygpsclient-rtk
```

For the touchscreen, install the desktop icon so you can just tap it:

```bash
mkdir -p ~/.local/share/applications
cp packaging/xdg/pygpsclient-rtk.desktop ~/.local/share/applications/
# edit Exec= to the full path, e.g. /home/atif/miniforge3/envs/rtk/bin/pygpsclient-rtk
$EDITOR ~/.local/share/applications/pygpsclient-rtk.desktop
```

Manual control, any time:

```bash
pygpsclient-rtk status     # status of all RTK services
pygpsclient-rtk stop       # stop all RTK services (relay + injector)
pygpsclient-rtk            # start them again + reopen the GUI

# pause ONLY corrections to the drone; relay + GUI/base feed stay up:
pygpsclient-rtk pause      # stop the injector only
pygpsclient-rtk resume     # start the injector only
pygpsclient-rtk toggle     # flip the injector on/off (used by the toggle icon)
```

`pause`/`resume`/`toggle` touch **only the injector** (`rtcm-mavlink`) — the
relay keeps running, so the GUI's base view and GCP capture stay live while
corrections to the drone are paused. For a touchscreen button, install the
toggle icon:

```bash
cp packaging/xdg/pygpsclient-rtk-toggle.desktop ~/.local/share/applications/
sed -i 's|^Exec=.*|Exec=/home/atif/miniforge3/envs/rtk/bin/pygpsclient-rtk toggle|' \
    ~/.local/share/applications/pygpsclient-rtk-toggle.desktop
cp ~/.local/share/applications/pygpsclient-rtk-toggle.desktop ~/Desktop/
chmod +x ~/Desktop/pygpsclient-rtk-toggle.desktop
```

(GNOME: right-click it → **Allow Launching** the first time. It shows a
notification with the new state on each tap.)

Because the injector is a supervised service (not a child of the GUI), closing
the GUI window does **not** interrupt corrections in flight — `pygpsclient-rtk
pause` (or `stop`) when you want them to actually stop.

## 7. Watch it work (validation, via the journal)

```bash
journalctl _SYSTEMD_USER_UNIT=rtcm-mavlink.service -f
```

> On JetPack's older systemd, user-unit logs land in the **system** journal, so
> `journalctl --user -u …` reports "No journal files were found" even while the
> service runs. Use the `_SYSTEMD_USER_UNIT=` form above (add `sudo` if it says
> permission denied), or just watch the status live:
> `watch -n 1 'systemctl --user status rtcm-mavlink.service --no-pager | tail -n 8'`.

With `--monitor` in the env file, the log prints a live RTK verdict, e.g.:

```
[RTK ACTIVE ✓] sets=1240 GPS RTK FIXED sats=32
```

This is your Stage C/D check (see
[rtcm_mavlink_validation.md](rtcm_mavlink_validation.md)): the rover should
climb to RTK FLOAT/FIXED. `rtk_rate` may stay 0 if the autopilot does not emit
the optional `GPS_RTK` message — key off the `GPS <fix>` word instead.

> **Open sky is required for RTK.** With the base antenna **indoors** the rover
> tops out at **`GPS DGPS`** (fix type 4) — that already proves corrections are
> flowing and being applied, but carrier-phase RTK (FLOAT 5 / FIXED 6) needs the
> base *and* rover antennas to have a clear view of the sky. An indoor DGPS
> result is a successful end-to-end test; move both antennas outside for FIXED.

Prove causality with the **toggle test**:

```bash
systemctl --user stop rtcm-mavlink.service     # rover fix should drop from RTK
systemctl --user start rtcm-mavlink.service    # ... and climb back
```

## 8. Using the Survey / GCP GUI (while injecting)

The GUI opens with `pygpsclient-rtk`. Because `gnss-server` re-serves the
receiver over local TCP, the GUI can watch the **same** receiver the injector
is using - no need to stop anything. In the GUI:

1. Set the connection to **TCP**: `Server: localhost`, `Port: 50010`, protocol
   `TCP IPv4` (these are the defaults), then click the **TCP/UDP** button.
2. The position/fix/sats fields populate from the base receiver.
3. **Menu → Options → Survey / GCP Capture** for base survey and GCP capture.

Do **not** click **USB/UART** on `/dev/ttyACM0` - that would try to open the
serial port directly and collide with `gnss-server`, which is holding it. Always
connect the GUI via **TCP `localhost:50010`** instead.

## Notes

- **One holder of the serial port:** `gnss-server` owns `/dev/ttyACM0` and
  fans the stream out over TCP `50010`; the injector and the GUI are both TCP
  clients of it. Nothing else should open the serial port directly while the
  services run (`pygpsclient-rtk stop` first if you need to).
- **Herelink link:** the Jetson must be on the Herelink Wi-Fi network to reach
  its MAVLink stream (`--dest udpout:192.168.43.1:14550`). Check reachability
  with `ping 192.168.43.1`.
- **Bandwidth:** keep the base RTCM message set lean (1005/1006 + MSM4
  1074/1084/1094/1124 + 1230) so it fits comfortably over the Herelink link.
- **No boot autostart by design:** the services are disabled user units. They
  start only when you launch the app and stop when you tell them to.
