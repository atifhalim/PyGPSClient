# RTCM → MAVLink injection: validation plan

How to **conclusively** prove that RTCM corrections injected onto the MAVLink
link (e.g. a Herelink Wi-Fi stream) reach the rover GNSS and drive it to an RTK
fix. The injector and monitor live in
[`pygpsclient.rtcm_mavlink`](../src/pygpsclient/rtcm_mavlink.py); the CLI is
`rtcm-mavlink`.

Install the MAVLink extra: `pip install ".[mavlink]"`.

## What "conclusive" means — the signals to watch

Do **not** rely on the QGC RTK icon alone. Watch the vehicle's own telemetry,
which the autopilot reports back on the same MAVLink stream:

| Signal (MAVLink message.field) | Meaning | Conclusive value |
|---|---|---|
| `GPS_RAW_INT.fix_type` / `GPS2_RAW.fix_type` | rover fix quality | **5 = RTK FLOAT**, **6 = RTK FIXED** |
| `GPS_RTK.rtk_rate` / `GPS2_RTK.rtk_rate` | observed correction rate at the autopilot | **> 0** = RTCM is arriving |
| `GPS_RTK.rtk_health`, `.nsats` | RTK engine health / sats used | healthy, nsats climbing |

**The proof is causal, not static:** a rover only reaches fix_type 5/6 when it
is consuming RTCM. If injection is the *only* correction source and the rover
reaches RTK, the path works. The **toggle test** (below) removes all doubt.

The `rtcm-mavlink --monitor` flag reads these back and prints a live verdict.
Its `rtk_active()` check keys off **`fix_type` ≥ 5** (RTK float/fixed) — the
authoritative signal. `GPS_RTK.rtk_rate` is shown as extra confirmation *when
available*, but is **not required**: many ArduPilot/PX4 configs don't emit
`GPS_RTK` at all (a rover can reach RTK FLOAT/FIXED with no `GPS_RTK` message).

## Where to observe it

- **Scripted (best):** `rtcm-mavlink --monitor udpin:0.0.0.0:14550` — prints
  per-GPS fix + rtk_rate each second and a PASS/FAIL verdict. Fully automatable.
- **QGC MAVLink Inspector** (Analyze Tools → MAVLink Inspector): watch
  `GPS_RAW_INT.fix_type` and `GPS_RTK` update live. (The MAVLink *Console* is
  **not** the tool for this — use the Inspector / message stream.)
- **Permanent record:** ArduPilot dataflash `.bin` (`GPS.Status`, RTK fields) or
  PX4 ULog — analyse after the run to show fix_type over time.

## Staged plan — validate early, cheaply, then for real

### Stage A — Loopback (no hardware) ✅ automated
Proves the injector emits byte-exact, correctly-fragmented `GPS_RTCM_DATA`.
Covered by `tests/test_rtcm_mavlink.py::TestInjectorLoopback` (round-trips real
MAVLink over UDP and reassembles to the original bytes). Run:
```
python -m unittest tests.test_rtcm_mavlink
```

### Stage B — SITL (message acceptance)
Point the injector at ArduPilot/PX4 SITL and confirm the autopilot **accepts**
the stream without errors and `GPS_RTK.rtk_rate` goes non-zero:
```
rtcm-mavlink --rtcm-file base.rtcm3 --dest udpout:127.0.0.1:14555 \
             --monitor udpin:0.0.0.0:14550
```
Note: SITL's GPS is simulated, so fix_type won't necessarily reach RTK — Stage B
validates **plumbing and rate**, not the RTK solution.

### Stage C — Bench HITL (the conclusive test) ⭐
Real autopilot (Cube) + real **Here 4 rover** with sky view, on the bench —
**no flight**. Feed live RTCM from the surveyed base through the injector
directly to the flight controller's telemetry/USB first (isolates the injector
from Herelink):
```
rtcm-mavlink --rtcm-serial /dev/ttyACM0 --baud 115200 \
             --dest <fc-mavlink-conn> --monitor <fc-mavlink-conn>
```
**PASS:** rover `fix_type` climbs 3 → 5 → 6 and `rtk_rate > 0` within a minute.

### Stage D — Through Herelink (make-or-break) ⭐
Identical to Stage C, but inject onto the **Herelink Wi-Fi stream** so the
Herelink RF datalink carries `GPS_RTCM_DATA` end-to-end:
```
rtcm-mavlink --rtcm-serial /dev/ttyACM0 --baud 115200 \
             --dest udpout:<herelink-ip>:14552 \
             --monitor udpin:0.0.0.0:14550
```
Comparing D against C isolates whether the Herelink link itself is the
bottleneck (bandwidth/rate). **PASS = same RTK result as Stage C.**

### The toggle test (do this in Stage C and D)
1. With injection running, confirm rover is **RTK FIXED** (fix_type 6).
2. **Stop** `rtcm-mavlink` → within seconds fix_type must **drop** to 4/3.
3. **Restart** it → fix_type must climb back to 5/6.

A fix that rises with injection, falls without it, and rises again is
**conclusive proof** the injected corrections — and nothing else — are driving
the rover's RTK solution.

## Watch-items
- **Rate/bandwidth:** keep the base RTCM message set lean (1005/1006 + MSM4
  1074/1084/1094/1124 + 1230) so it fits comfortably over the Herelink link;
  `GPS_RTK.rtk_rate` and the injector's `sets/s` should track the base output.
- **Base position:** absolute accuracy comes from the base coordinate embedded
  in RTCM 1005/1006 — survey it (NTRIP/PPK) so the rover is globally accurate,
  not just precise relative to an unknown point.
- **One GPS vs two:** if the rover is GPS2 on the vehicle, watch `GPS2_RAW` /
  `GPS2_RTK` (the monitor tracks both).
