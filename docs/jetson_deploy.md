# Deploy on Jetson Xavier NX (everything on one board)

This runs the whole solution on a Jetson Xavier NX — no laptop:

- the **RTCM → MAVLink injector** as an always-on `systemd` service (headless), and
- the **Survey / GCP Capture GUI** and other tools when a display is attached.

The Jetson is a full Ubuntu (ARM64) machine, so there is no sandbox and full
USB/serial/network access — both the service and the GUI run natively.

## 1. Prerequisites

```bash
python3 --version         # must be >= 3.10 (JetPack 6 / Ubuntu 22.04 = 3.10 OK;
                          # on older JetPack install a newer Python into a venv)
sudo apt update
sudo apt install -y python3-venv python3-tk git   # python3-tk only needed for the GUI
```

## 2. Install the fork

```bash
sudo mkdir -p /opt/pygpsclient && sudo chown "$USER" /opt/pygpsclient
git clone https://github.com/atifhalim/PyGPSClient.git /opt/pygpsclient
cd /opt/pygpsclient
git checkout claude/pygpsclient-installation-tibd38
python3 -m venv venv
./venv/bin/python -m pip install --upgrade pip
./venv/bin/python -m pip install ".[mavlink]"     # includes pymavlink for the injector
```

Confirm it runs on ARM64 (no display needed for these):

```bash
./venv/bin/python -m pip install pytest
./venv/bin/python -m pytest tests/ -q -o addopts=""
```

## 3. Stable name for the base receiver

So the service always finds the base regardless of USB enumeration order,
install the udev rule (edit the ids first):

```bash
sudo cp packaging/udev/99-rtk-base.rules.example /etc/udev/rules.d/99-rtk-base.rules
sudoedit /etc/udev/rules.d/99-rtk-base.rules      # set idVendor/idProduct or serial
sudo udevadm control --reload-rules && sudo udevadm trigger
ls -l /dev/rtk-base                               # should point at the base's ttyACM*/ttyUSB*
```

## 4. Install the service

```bash
# service account with serial (dialout) access, and a writable state dir
sudo useradd --system --no-create-home --shell /usr/sbin/nologin -G dialout rtk || true
sudo mkdir -p /var/lib/rtcm-mavlink && sudo chown rtk:rtk /var/lib/rtcm-mavlink

# configuration
sudo cp packaging/systemd/rtcm-mavlink.env.example /etc/rtcm-mavlink.env
sudoedit /etc/rtcm-mavlink.env                    # set the base port, --dest, --monitor

# unit
sudo cp packaging/systemd/rtcm-mavlink.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now rtcm-mavlink.service
```

## 5. Watch it work (validation, via the journal)

```bash
journalctl -u rtcm-mavlink.service -f
```

With `--monitor` in the env file, the log prints a live RTK verdict, e.g.:

```
[RTK ACTIVE ✓] sets=1240 GPS RTK FIXED sats=32 rtk_rate=115
```

This is your Stage C/D check (see
[rtcm_mavlink_validation.md](rtcm_mavlink_validation.md)): the rover should
climb to RTK FLOAT/FIXED and `rtk_rate` should be non-zero. Do the **toggle
test** to prove causality:

```bash
sudo systemctl stop rtcm-mavlink.service     # rover fix should drop from RTK
sudo systemctl start rtcm-mavlink.service    # ... and climb back
```

## 6. Using the GUI on the Jetson (optional)

The Survey / GCP Capture dialog is tkinter, so it needs a display — an attached
HDMI monitor, or a VNC / remote-desktop session into the Jetson. With a display
available:

```bash
cd /opt/pygpsclient && ./venv/bin/pygpsclient
```

Then **Menu → Options → Survey / GCP Capture** for base survey and GCP capture.
The headless service and the GUI can both be present on the same board; you only
open the GUI when you actually need it.

## Notes

- **Serial vs the service:** only one program can hold the base's serial port.
  If you open the base in the GUI, stop the service first (`sudo systemctl stop
  rtcm-mavlink`) and vice-versa.
- **Herelink link:** the Jetson must be on the Herelink Wi-Fi network to reach
  its MAVLink stream (`--dest udpout:<herelink-ip>:14552`). Check reachability
  with `ping <herelink-ip>`.
- **Bandwidth:** keep the base RTCM message set lean (1005/1006 + MSM4
  1074/1084/1094/1124 + 1230) so it fits comfortably over the Herelink link.
