# Field Terminal: how the code works

Field Terminal is about 5,500 lines of Python plus one bash service, and it uses only Python's standard library and pygame. Both already come with Knulli, so nothing has to be installed on the handheld.

## 1. How it starts

```
Knulli Ports menu
   └─ "Field Terminal.sh"            (runs as root, like every port)
        ├─ loads PortMaster's control.txt → gets this handheld's controller mapping
        ├─ cd /userdata/roms/ports/fieldterminal
        └─ python3 main.py > log.txt  (any crash is written to log.txt)
              └─ App() → App.run()    (the main loop until MENU+START)
```

It runs as root because Ports launch as root on Knulli. Packet capture, the ARP sweep and traceroute need raw network sockets, and only root can open those.

## 2. The architecture: four layers

```
 ┌─────────────────────────── main.py (App) ───────────────────────────┐
 │ main loop: buttons → current screen → draw at 640x480 → display     │
 └───────┬──────────────────────────────┬──────────────────────────────┘
         │ draws from                   │ asks to change something
 ┌───────▼─────────────┐        ┌───────▼──────────┐
 │ SCREENS             │        │ actions.py       │ ← the ONLY module
 │ screens_sys.py (7)  │        │ CPU mode, ZRAM,  │   that writes to the
 │ screens_net.py (5)  │        │ battery logger   │   system (allowlisted)
 └───────┬─────────────┘        └──────────────────┘
         │ read the latest values from
 ┌───────▼──────────────────────────────────────────────────────┐
 │ BACKGROUND THREADS (sample all the time; the UI never waits) │
 │ sysmon.SysMonitor   netmon.Monitor + Jobs   capture.Capture  │
 └───────┬──────────────────────────────────────────────────────┘
         │ call
 ┌───────▼──────────────────────────────────────────────────────┐
 │ READERS (pure functions, read only, never raise)             │
 │ sysinfo.py (/sys, /proc)  netcore.py (sockets, iw)           │
 │ battery.py (CSV math)     capture.decode (packet parsing)    │
 └──────────────────────────────────────────────────────────────┘
```

Two design rules run through the code:

- **Everything is read-only except `actions.py`.**
- **One bad value can't close the app.** Every thread and every page draw is wrapped in try/except. If a page fails, you get an error tile and an entry in the LOG tab, and the other tabs keep working.

## 3. File by file

All app files are in `ports/fieldterminal/` unless another folder is shown.

| File | Lines | What it does |
|---|---|---|
| `main.py` | 524 | The `App` class: startup, main loop, header/tabs/footer, toasts, confirm dialogs, first-run button setup |
| `inputs.py` | 135 | Turns controller and keyboard events into action names (`"a"`, `"l1"`, `"left"`…) and handles d-pad key repeat |
| `theme.py` | 450 | Look and drawing: VT323/Silkscreen pixel fonts, green/amber/red palette, glow, scanlines, bars, graphs, number formatting |
| `sysinfo.py` | 614 | Reads battery, CPU, temperatures, memory, ZRAM, disks, processes and the game library from `/sys` and `/proc` |
| `sysmon.py` | 269 | `SysMonitor` thread that calls sysinfo once a second and keeps the history behind the graphs |
| `battery.py` | 230 | Parses the battery log CSVs and works out capacity, health and time left |
| `service/fieldterminal_log` | 169 | Bash boot service that logs the battery once a minute, even when the app is closed |
| `netcore.py` | 595 | Network functions: routes, Wi-Fi link/scan, ping, ARP sweep, port scan, banners, DNS |
| `netmon.py` | 452 | Network threads: live `Monitor`, plus the Wi-Fi scan, discovery, port scan and tools jobs, and the event log |
| `capture.py` | 369 | Live packet capture, protocol decoding, `.pcap` export |
| `actions.py` | 216 | The only code that changes anything: CPU governor, ZRAM, installing the logger |
| `screens_sys.py` | 759 | DASH, POWER, CPU, MEMORY, DISK, PROCS, LOG |
| `screens_net.py` | 633 | NET tab with 5 pages: Overview, Wi-Fi, Devices, Traffic, Tools |
| `demo.py` | 541 | Fake network and battery data for `--demo` and the preview screenshots |
| `tools/deploy.py` | 332 | Installs the app from the PC over SSH or SMB |
| `tools/make_art.py` | 68 | Makes the cover and screenshot images for the Ports menu |
| `tests/` | ~850 | pytest tests that run the readers against a fake `/sys` tree |

## 4. The main loop (`main.py`, `App.run`)

It runs 30 times a second:

1. **Events → actions.** `inputs.Input` turns raw SDL controller events into names like `"a"` or `"r1"`.
2. **`App.handle(action)`** checks these in order:
   - MENU+START held together → quit.
   - A confirm dialog is open → A runs the change, B cancels.
   - L1/R1 → switch tab.
   - Otherwise the current page's `handle()` gets the action. If the page doesn't use left/right, they switch tabs too.
3. **Redraw only when needed.** It redraws when something changed (`dirty`) or a timer runs out: every 1 s on most tabs, 0.5 s on NET, 0.1 s while a scan is running. This saves battery.
4. **Draw:** everything is drawn on a fixed 640×480 canvas in this order: header (SSID, signal, clock, battery %), tab bar, the page, scanlines, footer hints, toast, dialog. The canvas is then scaled to the real screen.

**Screen lifecycle.** Each page is a small class with `enter()`, `leave()`, `handle()` and `draw()`. `enter`/`leave` switch the expensive data sources on and off:

- DASH and PROCS turn on process sampling, and DASH and DISK turn on disk sampling. They turn them off again when you leave.
- NET starts packet capture when you open it and stops it when you leave.
- The Wi-Fi scan and the device sweep only start the first time you open their page.

**First run.** The app asks you to press A once. Some handhelds report the button labelled A as SDL's "B" (Nintendo layout), so it saves `layout: nintendo` or `layout: xbox` to `config.json`.

## 5. How each feature works

### System numbers (`sysinfo.py` + `sysmon.py`)

- **CPU load:** reads `/proc/stat` twice and compares the counters. Load is the share of the time between the two readings that wasn't idle, per core.
- **Per-process CPU:** the same comparison using `/proc/<pid>/stat`. The first sample is thrown away because it has nothing to compare against.
- **Temperatures:** come from `/sys/class/thermal`.
- **Timing:** battery, CPU and memory are read every 1 s, the governor and brightness every 5 s, disks every 15 s, processes every 2 s.
- **Game counts:** a slow background job reads EmulationStation's system list and counts files with each system's extensions. Files listed inside a `.cue`, `.m3u` or `.gdi` belong to one game, so they're folded into it. Results are cached in `cache.json` and a folder is only recounted when its modification time changes.

### Battery health and time left (`battery.py` + the logger service)

- The bash service reads voltage, current, %, CPU temperature and brightness once a minute. It keeps the lines in memory and writes them every 10 minutes to `/userdata/system/fieldterminal/battery-YYYYMM.csv`. Batching the writes saves SD card wear.
- **Capacity:** the log is split into continuous discharge runs. For each run it adds up current × time (trapezoid rule), then divides by the % used. Only runs that used at least 30% over at least 30 minutes count.
- **Health** = median of the last 5 capacities ÷ 3200 mAh (the design capacity).
- **Time left** = % left × capacity ÷ average current over the last 2 minutes.

### Live network status (`netmon.Monitor`)

Once a second it reads the byte counters in `/proc/net/dev` to get the speed, runs `iw dev wlan0 link` for the signal, and pings the gateway and 1.1.1.1. Every 20 s it also checks DNS. When the NET tab isn't on screen it pings less often.

### Device discovery (`netcore.arp_sweep`)

- It builds raw ARP "who has 192.168.0.X?" frames by hand: one broadcast per address in your subnet, sent through an `AF_PACKET` raw socket.
- Every device that replies reveals its MAC address. It also flags randomized MACs (phones use these for privacy).
- Names come from reverse DNS. Without root, it falls back to a parallel ping sweep plus the kernel's ARP cache.

### Port scan (`netcore.port_scan`)

- It's a TCP connect() scan: up to 48 non-blocking connections at a time, with `select()` waiting for them. A connection that succeeds means the port is open.
- It then grabs banners: it reads the greeting of SSH, FTP and SMTP, and sends a HEAD request to HTTP servers.
- **It refuses any non-private IP**, so it can't scan anything on the internet.

### Packet capture (`capture.py`)

- It opens an `AF_PACKET`/`ETH_P_ALL` raw socket on the Wi-Fi interface and decodes Ethernet → IPv4/IPv6 → TCP/UDP/ICMP/ARP by hand with `struct`.
- **Hostnames:** it learns them from DNS answers and from the TLS ClientHello SNI field. That's the site name a browser sends unencrypted at the start of HTTPS.
- It keeps the last 6,000 raw frames in a ring buffer. Y writes them as a classic libpcap file you can open in Wireshark.
- The Wi-Fi chip (rtl8821cs) has no monitor mode, so you only see the handheld's own traffic plus broadcasts.

### Changing settings (`actions.py`)

- Every file write is checked against `ALLOWED_WRITES` regexes: only the CPU governor files and the logger's service file. Every command is checked against `ALLOWED_CMDS`: only `knulli-services` for `zramswap` and `fieldterminal_log`, and `knulli-settings-set system.cpu.governor`. Anything else raises `Refused`.
- **CPU modes:**
  - Saver = `conservative`/`powersave`
  - Normal = `schedutil`/`ondemand`
  - Maximum = `performance`, and it asks for confirmation first
- **ZRAM:** turning it off is refused if free RAM can't take back what's stored in it.

## 6. The development tools

- **`--demo`** runs the whole UI on fake data. The fake traffic is made of real packet bytes that go through the real decoder, so demo mode also tests the parser.
- **`--screens DIR`** (with `SDL_VIDEODRIVER=dummy`) renders every screen to a PNG. That's how `previews/` was made.
- **Tests:** the `FT_SYSROOT` environment variable puts a prefix in front of every `/sys` and `/proc` path, so pytest can feed the readers a fake sensor tree from `conftest.py`.
- **`tools/deploy.py HOST install`** does the install in this order:
  1. Backs up the gamelist and the old Net Analyzer to `card-backups/`.
  2. Unpacks the app into a temporary folder and checks every `.py` compiles.
  3. Carries over your `config.json`, captures and logs.
  4. Swaps the folders.
  5. Writes `Field Terminal.sh`.
  6. Updates `gamelist.xml`.
  7. Deletes exactly the two Net Analyzer paths.
  8. Asks EmulationStation to reload its game lists.

## 7. What it creates on the card

Paths are relative to `/userdata` on the handheld.

| Path | What it is |
|---|---|
| `roms/ports/fieldterminal/config.json` | button layout, logger on/off |
| `roms/ports/fieldterminal/logs/` | event logs, `sensors.txt` |
| `roms/ports/fieldterminal/captures/*.pcap` | saved captures |
| `roms/ports/fieldterminal/log.txt` | last run's Python output, for crash reports |
| `system/services/fieldterminal_log` | the boot logger service |
| `system/fieldterminal/battery-*.csv` | battery history (last 6 months kept) |

## Where to start reading

A good order is `main.py` first, then one screen class such as `Cpu` in `screens_sys.py`, then the thread it reads from (`sysmon.py`), then the reader under it (`sysinfo.py`). That follows one number from the sensor file to the screen.
