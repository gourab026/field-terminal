# Field Terminal

A pocket system monitor and network lab for Knulli handhelds (640x480, Python + pygame, nothing to install).
Replaces Net Analyzer: every network feature is in the NET tab.

## Tabs (L1 / R1, or left / right)

| Tab | Answers | Shows |
|---|---|---|
| DASH | Is everything OK? | battery and time left, CPU, memory, storage and game count, network, top programs |
| NET | Is my network OK, what is on it? | five pages (left / right): Overview, Wi-Fi, Devices (A = port scan), Traffic (live capture, Y = save `.pcap`), Tools (ping, traceroute, DNS, button setup) |
| POWER | How long will it last, is the battery wearing out? | %, V, mA, time left, voltage (3 min), session / since charge / last full times, health, last full discharge curve. A = logger on/off |
| CPU | Why is it slow or hot? | speed and load per core (90 s), CPU/GPU temperature, CPU mode Saver / Normal / Maximum (A applies) |
| MEMORY | Will this game run smoothly? | RAM in use (90 s), ZRAM. A = ZRAM at boot, X = start/stop now |
| DISK | Do I have room for more games? | each card, games and size per system (X counts again) |
| PROCS | What is using the CPU? | top programs by CPU and memory (read only) |
| LOG | What happened? | events, also in `logs/fieldterminal-YYYYMMDD.log` |

Quit with **MENU + START**. The first start asks you to press **A** once to learn the button layout.

## Battery logger

Field Terminal installs a small Knulli service, `fieldterminal_log` (in `/userdata/system/services/`).
It runs at boot, also when this app is closed, and once a minute reads battery voltage, current,
percentage, CPU temperature and brightness. Lines are kept in memory and written every 10 minutes
(and at shutdown) to `/userdata/system/fieldterminal/battery-YYYYMM.csv`. The last 6 months are kept.

* Turn it off: POWER tab, press A (stops it, removes it from boot; history stays).
  Or: `knulli-services disable fieldterminal_log`.
* **Health** = usable capacity / design capacity (3200 mAh unless the battery reports its own).
  Usable capacity = charge drawn (current x time) / share of the battery used, from discharges that
  used at least 30 % over at least 30 minutes; median of the last five.
* **Time left** = % left x usable capacity / average current of the last 2 minutes
  (without a current sensor: % left / % lost per hour).

## What it may change on the handheld (and nothing else)

* CPU mode: the kernel's own governors only, saved with `knulli-settings-set system.cpu.governor`
  (the same setting as the Knulli menu). Speed limits, overclock and voltages are never touched.
* ZRAM: Knulli's own `zramswap` service (on/off at boot, start/stop now). Stopping is refused when RAM
  could not take back what ZRAM holds.
* The battery logger service file above.

Everything else is read only. Port scans are limited to private (local) addresses.
In normal (managed) mode a Wi-Fi card only passes this handheld's own traffic plus broadcasts, so the
capture cannot see other devices' private traffic. Only scan or capture networks you own or have
permission to test.

## Files

* `captures/*.pcap`: saved captures, open them in Wireshark (network share: `share/roms/ports/fieldterminal/captures`).
* `logs/`: event logs. `log.txt`: the last run's Python output (for crash reports).
* `cache.json`: game counts per system (recounted when a folder changes). `config.json`: button layout, logger choice.

Fonts: VT323 and Silkscreen, SIL Open Font License (`fonts/LICENSE-*.txt`).
