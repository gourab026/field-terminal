"""Field Terminal - read-only system readers (battery, CPU, temperature, memory, storage,
processes, game library). Standard library only.

Every function here only *reads* /sys, /proc and the games folder. A missing file never raises:
the value comes back as None and the screen shows "n/a" or hides that line.

FT_SYSROOT (environment) prefixes every absolute path, so tests can point the readers at a
fake tree of sensor files.
"""
import os
import re
import xml.etree.ElementTree as ET

ROOT = os.environ.get("FT_SYSROOT", "")
PAGE = os.sysconf("SC_PAGE_SIZE") if hasattr(os, "sysconf") else 4096
try:
    HZ = os.sysconf("SC_CLK_TCK")
except (AttributeError, ValueError, OSError):
    HZ = 100

DESIGN_MAH_DEFAULT = 3200          # RG40XXH battery, used only when the gauge does not say


def P(path):
    return ROOT + path


def read(path, default=None):
    try:
        with open(P(path)) as f:
            return f.read().strip()
    except (OSError, UnicodeDecodeError):
        return default


def read_int(path, default=None):
    v = read(path)
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def listdir(path):
    try:
        return sorted(os.listdir(P(path)))
    except OSError:
        return []


# --------------------------------------------------------------------------- battery

def find_battery():
    """Path (without ROOT) of the battery in /sys/class/power_supply, or None."""
    base = "/sys/class/power_supply"
    found = []
    for name in listdir(base):
        d = f"{base}/{name}"
        # scope=Device marks batteries of paired gamepads, mice and headsets: never the handheld's own
        if read(d + "/type") == "Battery" and read(d + "/scope") != "Device":
            found.append((0 if "bat" in name.lower() else 1, d))
    return min(found)[1] if found else None


def find_charger():
    base = "/sys/class/power_supply"
    return [f"{base}/{n}" for n in listdir(base) if read(f"{base}/{n}/type") in ("USB", "Mains", "AC")]


def battery(bdir=None):
    """Snapshot of the battery. Units: mV, mA (positive number, direction from 'status'),
    mAh, deg C. pct = Knulli's own learned percentage (batteryplus) when it exists."""
    bdir = bdir or find_battery()
    out = {"present": bool(bdir), "status": None, "pct": None, "kpct": None, "mv": None, "ma": None,
           "design_mah": None, "full_mah": None, "cycles": None, "temp": None, "plugged": None}
    if not bdir:
        return out
    out["status"] = read(bdir + "/status")
    kp = read_int(bdir + "/capacity")
    out["kpct"] = kp
    bp = read_int("/tmp/battery.percent")          # batteryplus, what the Knulli menu shows
    if bp is not None and not batteryplus_alive():
        bp = None                                  # its daemon has stopped: the file is no longer updated
    out["pct"] = bp if bp is not None and 0 <= bp <= 100 else kp
    uv = read_int(bdir + "/voltage_now")
    micro = uv is not None and abs(uv) > 100000
    if uv is not None:
        out["mv"] = uv / 1000.0 if micro else float(uv)
    ua = read_int(bdir + "/current_now")
    if ua is not None:
        out["ma"] = current_ma(ua, micro, out["status"])
    for key, name in (("design_mah", "charge_full_design"), ("full_mah", "charge_full")):
        v = read_int(f"{bdir}/{name}")
        if v and v > 0:
            out[key] = v / 1000.0 if v > 100000 else float(v)
    out["cycles"] = read_int(bdir + "/cycle_count")
    t = read_int(bdir + "/temp")
    if t is not None:
        out["temp"] = t / 10.0 if abs(t) > 200 else float(t)
    chargers = find_charger()
    if chargers:
        out["plugged"] = any(read_int(c + "/online", 0) == 1 for c in chargers)
    return out


def current_ma(raw, volts_in_micro, status):
    """Current in mA from current_now, whose unit drivers do not agree on.
    More than 5000 can only be uA (this handheld never draws 5 A). While charging or discharging
    the draw is at least tens of mA, so a smaller number is mA. When full or idle it is a trickle:
    follow the voltage's unit (uV means uA, the kernel convention)."""
    a = abs(raw)
    if a > 5000:
        return a / 1000.0
    if status in ("Charging", "Discharging"):
        return float(a)
    return a / 1000.0 if volts_in_micro else float(a)


def batteryplus_alive():
    """False only when Knulli's battery daemon is known to have stopped (pidfile with a dead pid)."""
    pid = read("/var/run/batteryplus.pid")
    if not pid or not pid.strip().isdigit():
        return True
    return os.path.isdir(P(f"/proc/{pid.strip()}"))


def brightness_pct():
    for name in listdir("/sys/class/backlight"):
        d = f"/sys/class/backlight/{name}"
        b, m = read_int(d + "/brightness"), read_int(d + "/max_brightness")
        if b is not None and m:
            return round(100 * b / m)
    return None


def uptime():
    v = read("/proc/uptime")
    try:
        return float(v.split()[0])
    except (AttributeError, ValueError, IndexError):
        return None


# --------------------------------------------------------------------------- CPU

def cpu_ids():
    """CPU numbers present, from /sys/devices/system/cpu/present ('0-3')."""
    spec = read("/sys/devices/system/cpu/present") or read("/sys/devices/system/cpu/online") or "0"
    ids = []
    for part in spec.split(","):
        if "-" in part:
            a, b = part.split("-", 1)
            ids += list(range(int(a), int(b) + 1))
        elif part.strip().isdigit():
            ids.append(int(part))
    return ids or [0]


def cpu_online():
    spec = read("/sys/devices/system/cpu/online")
    if not spec:
        return len(cpu_ids())
    n = 0
    for part in spec.split(","):
        if "-" in part:
            a, b = part.split("-", 1)
            n += int(b) - int(a) + 1
        elif part.strip().isdigit():
            n += 1
    return n


def cpu_mhz(cpu):
    v = read_int(f"/sys/devices/system/cpu/cpu{cpu}/cpufreq/scaling_cur_freq")
    return None if v is None else v / 1000.0


def cpu_policy():
    """Governor and limits of the first cpufreq policy."""
    base = "/sys/devices/system/cpu/cpufreq/policy0"
    if not os.path.isdir(P(base)):
        base = "/sys/devices/system/cpu/cpu0/cpufreq"
    avail = (read(base + "/scaling_available_governors") or "").split()
    mx = read_int(base + "/scaling_max_freq")
    hw = read_int(base + "/cpuinfo_max_freq")
    mn = read_int(base + "/cpuinfo_min_freq")
    return {"governor": read(base + "/scaling_governor"), "available": avail,
            "max_mhz": mx / 1000.0 if mx else None, "hw_max_mhz": hw / 1000.0 if hw else None,
            "min_mhz": mn / 1000.0 if mn else None}


class CpuLoad:
    """Busy % per core and in total from /proc/stat deltas."""

    def __init__(self):
        self.prev = None

    @staticmethod
    def _read():
        rows = {}
        txt = read("/proc/stat") or ""
        for line in txt.splitlines():
            if not line.startswith("cpu"):
                continue
            p = line.split()
            try:
                v = [int(x) for x in p[1:9]]
            except ValueError:
                continue
            idle = v[3] + v[4]               # idle + iowait
            rows[p[0]] = (sum(v), idle)
        return rows

    def sample(self):
        """-> (total %, {cpu index: %}) since the previous call (first call: zeros)."""
        cur = self._read()
        prev, self.prev = self.prev, cur
        if not prev:
            return 0.0, {}
        per = {}
        total = 0.0
        for key, (t, idle) in cur.items():
            if key not in prev:
                continue
            dt, di = t - prev[key][0], idle - prev[key][1]
            pct = 100.0 * (dt - di) / dt if dt > 0 else 0.0
            pct = max(0.0, min(100.0, pct))
            if key == "cpu":
                total = pct
            else:
                try:
                    per[int(key[3:])] = pct
                except ValueError:
                    pass
        return total, per

    def total_jiffies(self):
        cur = self.prev or self._read()
        return cur.get("cpu", (0, 0))[0]


# --------------------------------------------------------------------------- temperature, GPU

def thermal_zones():
    """[(type, deg C)] for every thermal zone."""
    out = []
    for name in listdir("/sys/class/thermal"):
        if not name.startswith("thermal_zone"):
            continue
        d = f"/sys/class/thermal/{name}"
        t = read_int(d + "/temp")
        if t is None:
            continue
        out.append((read(d + "/type") or name, t / 1000.0 if abs(t) > 200 else float(t)))
    return out


def cpu_gpu_temp(zones=None):
    """(cpu C, gpu C). Matches zone names first; Knulli's own scripts use zone0 = CPU, zone1 = GPU."""
    zones = zones if zones is not None else thermal_zones()
    cpu = next((t for n, t in zones if "cpu" in n.lower()), None)
    gpu = next((t for n, t in zones if "gpu" in n.lower()), None)
    if cpu is None and zones:
        cpu = zones[0][1]
    if gpu is None and len(zones) > 1 and not any("gpu" in n.lower() for n, _ in zones):
        gpu = zones[1][1]
    return cpu, gpu


def devfreq():
    """{'gpu': MHz, 'mem': MHz} from /sys/class/devfreq when the kernel exposes them."""
    out = {}
    for name in listdir("/sys/class/devfreq"):
        d = f"/sys/class/devfreq/{name}"
        hz = read_int(d + "/cur_freq")
        if not hz:
            continue
        mhz = hz / 1e6 if hz > 100000 else float(hz)
        low = name.lower()
        if "gpu" in low or "mali" in low:
            out["gpu"] = mhz
        elif "dmc" in low or "ddr" in low or "dram" in low:
            out["mem"] = mhz
    return out


# --------------------------------------------------------------------------- memory

def meminfo():
    """/proc/meminfo in bytes."""
    out = {}
    for line in (read("/proc/meminfo") or "").splitlines():
        m = re.match(r"(\w+):\s+(\d+)", line)
        if m:
            out[m.group(1)] = int(m.group(2)) * 1024
    return out


def memory():
    mi = meminfo()
    total = mi.get("MemTotal")
    if not total:
        return {"total": None}
    avail = mi.get("MemAvailable")
    if avail is None:
        avail = mi.get("MemFree", 0) + mi.get("Buffers", 0) + mi.get("Cached", 0)
    return {"total": total, "available": avail, "used": total - avail, "free": mi.get("MemFree", 0),
            "cache": mi.get("Cached", 0) + mi.get("Buffers", 0),
            "swap_total": mi.get("SwapTotal", 0), "swap_free": mi.get("SwapFree", 0)}


def zram():
    """Compressed swap in RAM. None when the zram device does not exist."""
    base = "/sys/block/zram0"
    if not os.path.isdir(P(base)):
        return None
    size = read_int(base + "/disksize", 0) or 0
    mm = (read(base + "/mm_stat") or "").split()
    try:
        orig, compr, used = int(mm[0]), int(mm[1]), int(mm[2])
    except (IndexError, ValueError):
        orig = compr = used = 0
    active = any(line.split()[0].endswith("zram0") for line in (read("/proc/swaps") or "").splitlines()[1:]
                 if line.split())
    algo = read(base + "/comp_algorithm") or ""
    m = re.search(r"\[(\w+)\]", algo)
    return {"size": size, "stored": orig, "compressed": compr, "in_ram": used, "active": active and size > 0,
            "gained": max(0, orig - used), "algo": m.group(1) if m else ""}


# --------------------------------------------------------------------------- storage

LABELS = {"/userdata": "Games", "/boot": "System"}
SKIP_FS = {"squashfs", "tmpfs", "devtmpfs", "proc", "sysfs", "overlay", "cgroup", "cgroup2", "devpts",
           "debugfs", "configfs", "securityfs", "pstore", "tracefs", "fusectl", "mqueue", "ramfs", "autofs"}


def storage():
    """Real disks mounted (SD cards, USB): [{label, mount, dev, total, free, used}]."""
    seen, out = set(), []
    for line in (read("/proc/mounts") or "").splitlines():
        p = line.split()
        if len(p) < 3:
            continue
        dev, mnt, fs = p[0], p[1].replace("\\040", " "), p[2]
        if fs in SKIP_FS or not dev.startswith("/dev/") or dev in seen:
            continue
        if mnt.startswith(("/userdata/", "/var/", "/run/")) and mnt not in LABELS:
            continue                      # bind mounts of the same card
        try:
            st = os.statvfs(P(mnt))
        except OSError:
            continue
        total = st.f_blocks * st.f_frsize
        if total <= 0:
            continue
        seen.add(dev)
        free = st.f_bavail * st.f_frsize
        name = LABELS.get(mnt) or os.path.basename(mnt.rstrip("/")) or mnt
        out.append({"label": name, "mount": mnt, "dev": dev, "fs": fs, "total": total, "free": free,
                    "used": total - st.f_bfree * st.f_frsize})
    order = {"Games": 0, "System": 1}
    out.sort(key=lambda d: (order.get(d["label"], 2), d["label"]))
    return out


# --------------------------------------------------------------------------- processes

FRIENDLY = {
    "emulationstation": "Menu", "retroarch": "Game (RetroArch)", "smbd": "Samba", "nmbd": "Samba names",
    "connmand": "Network", "wpa_supplicant": "Wi-Fi", "dropbear": "SSH", "sshd": "SSH",
    "batteryplus": "Battery tracker", "syslogd": "System log", "klogd": "Kernel log", "dbus-daemon": "System bus",
    "udevd": "Devices", "avahi-daemon": "Network names", "ntpd": "Clock sync", "chronyd": "Clock sync",
    "gptokeyb": "Controls", "gptokeyb2": "Controls", "thd": "Hotkeys", "triggerhappy": "Hotkeys",
    "rngd": "Random numbers", "bluetoothd": "Bluetooth", "sixad": "PS3 pads", "dnsmasq": "Local DNS",
    "pipewire": "Audio", "wireplumber": "Audio", "pulseaudio": "Audio", "syncthing": "Syncthing",
    "idlewatcher": "Idle watcher", "init": "System (init)", "crond": "Scheduler", "nfsd": "NFS",
    "PortMaster": "PortMaster", "sway": "Display", "labwc": "Display", "weston": "Display",
}


def friendly(comm, cmd):
    if "fieldterminal_log" in cmd:
        return "Battery logger"
    if "silky" in cmd:
        return "LED colours"
    if comm in FRIENDLY:
        return FRIENDLY[comm]
    if comm.startswith(("python", "bash", "sh")) and cmd:
        base = os.path.basename(cmd.split()[1]) if len(cmd.split()) > 1 else comm
        return base[:24] or comm
    return comm


class ProcSampler:
    """CPU % (of the whole CPU, all cores) and memory per program, from /proc deltas."""

    def __init__(self, cpu=None):
        self.prev = {}
        self.prev_total = None
        self.cpu = cpu or CpuLoad()
        self.me = os.getpid()

    @staticmethod
    def _total():
        line = (read("/proc/stat") or "").split("\n", 1)[0].split()
        try:
            return sum(int(x) for x in line[1:9])
        except ValueError:
            return 0

    def sample(self):
        total = self._total()
        cur, out = {}, []
        for name in listdir("/proc"):
            if not name.isdigit():
                continue
            pid = int(name)
            st = read(f"/proc/{pid}/stat")
            if not st or ")" not in st:
                continue
            comm = st[st.find("(") + 1:st.rfind(")")]
            f = st[st.rfind(")") + 2:].split()
            try:
                ppid, ticks = int(f[1]), int(f[11]) + int(f[12])
            except (IndexError, ValueError):
                continue
            if pid == 2 or ppid == 2:
                continue                                  # kernel threads
            cmd = (read(f"/proc/{pid}/cmdline") or "").replace("\0", " ").strip()
            if not cmd:
                continue
            statm = (read(f"/proc/{pid}/statm") or "0 0").split()
            try:
                rss = int(statm[1]) * PAGE
            except (IndexError, ValueError):
                rss = 0
            cur[pid] = ticks
            pct = 0.0
            if self.prev_total is not None and pid in self.prev and total > self.prev_total:
                pct = 100.0 * (ticks - self.prev[pid]) / (total - self.prev_total)
            name_f = "This app" if pid == self.me else friendly(comm, cmd)
            out.append({"pid": pid, "comm": comm, "name": name_f, "cpu": max(0.0, pct), "rss": rss,
                        "me": pid == self.me})
        self.prev, self.prev_total = cur, total
        out.sort(key=lambda p: (-p["cpu"], -p["rss"]))
        return out


def merge_procs(procs):
    """Fold programs with the same friendly name together (e.g. several Samba workers)."""
    m = {}
    for p in procs:
        key = (p["name"], p["me"])
        if key in m:
            m[key]["cpu"] += p["cpu"]
            m[key]["rss"] += p["rss"]
            m[key]["count"] += 1
        else:
            m[key] = dict(p, count=1)
    out = list(m.values())
    out.sort(key=lambda p: (not p["me"], -p["cpu"], -p["rss"]))
    return out


# --------------------------------------------------------------------------- game library

ROMS = "/userdata/roms"
ES_SYSTEMS = ("/usr/share/emulationstation/es_systems.cfg", "/etc/emulationstation/es_systems.cfg")
MEDIA_DIRS = {"images", "videos", "media", "manuals", "downloaded_images", "downloaded_videos", "boxart",
              "snap", "wheel", "marquee", "thumbnails", "titles"}
NOT_GAMES_PORTS = {"portmaster.sh", "field terminal.sh"}


def es_systems():
    """[(name, full name, folder, {extensions})] from EmulationStation's system list."""
    for path in ES_SYSTEMS:
        txt = read(path)
        if not txt:
            continue
        try:
            root = ET.fromstring(txt)
        except ET.ParseError:
            continue
        out = []
        for s in root.iter("system"):
            name = (s.findtext("name") or "").strip()
            full = (s.findtext("fullname") or name).strip()
            folder = (s.findtext("path") or "").strip()
            exts = {e.lower() for e in (s.findtext("extension") or "").split() if e.startswith(".")}
            if not name or not folder:
                continue
            folder = folder.replace("%ROMPATH%", ROMS)
            if folder.startswith("~"):
                folder = ROMS + "/" + os.path.basename(folder)
            out.append((name, full, folder, exts))
        if out:
            return out
    return None


def _referenced(folder, files):
    """Files listed inside .cue / .m3u / .gdi in this folder: they belong to one game."""
    refs = set()
    for f in files:
        low = f.lower()
        if not low.endswith((".cue", ".m3u", ".gdi")):
            continue
        try:
            with open(os.path.join(folder, f), errors="ignore") as fh:
                for line in fh.read(65536).splitlines():
                    line = line.strip()
                    m = re.search(r'FILE\s+"([^"]+)"', line, re.I)
                    name = m.group(1) if m else (line if low.endswith(".m3u") and line and not line.startswith("#")
                                                 else None)
                    if low.endswith(".gdi"):
                        parts = line.split()
                        name = parts[4].strip('"') if len(parts) >= 5 else None
                    if name:
                        refs.add(os.path.basename(name.replace("\\", "/")).lower())
        except OSError:
            pass
    return refs


def scan_system(folder, exts, is_ports=False, stop=None):
    """-> (games, bytes). Counts files with the system's extensions in the folder and its
    sub-folders (like EmulationStation), minus disc files referenced by a .cue/.m3u/.gdi,
    so a two-disc game with an .m3u counts once. Ports: every launcher except PortMaster and this app."""
    games = size = 0
    base = P(folder)
    for dirpath, dirnames, filenames in os.walk(base):
        if stop is not None and stop.is_set():
            break
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and d.lower() not in MEDIA_DIRS]
        refs = _referenced(dirpath, filenames)
        for f in filenames:
            try:
                size += os.path.getsize(os.path.join(dirpath, f))
            except OSError:
                pass
            low = f.lower()
            if f.startswith("."):
                continue
            if is_ports:
                if dirpath == base and low.endswith((".sh", ".py")) and low not in NOT_GAMES_PORTS:
                    games += 1
                continue
            ext = os.path.splitext(low)[1]
            if ext in exts and low not in refs:
                games += 1
    return games, size


def dir_signature(folder):
    """Change detector: newest mtime of the folder and every sub-folder (adding or removing a file
    changes its folder's mtime), plus the number of folders. Only stats folders, never files."""
    base = P(folder)
    try:
        sig, n = os.stat(base).st_mtime, 0
    except OSError:
        return None
    for dirpath, dirnames, _files in os.walk(base):
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and d.lower() not in MEDIA_DIRS]
        for d in dirnames:
            try:
                sig = max(sig, os.stat(os.path.join(dirpath, d)).st_mtime)
                n += 1
            except OSError:
                pass
    return f"{sig:.1f}/{n}"


# --------------------------------------------------------------------------- first-start report

def report():
    """Plain-text dump of what the readers see (read only), written to logs/sensors.txt so the
    sensor names and units on a new handheld can be checked from a PC over the network share."""
    import sys
    lines = [f"python {sys.version.split()[0]}"]
    try:
        import pygame
        lines.append(f"pygame {pygame.version.ver}")
    except Exception as exc:
        lines.append(f"pygame: {exc}")
    for base in ("/sys/class/power_supply", "/sys/class/thermal", "/sys/class/devfreq", "/sys/class/backlight"):
        for name in listdir(base):
            d = f"{base}/{name}"
            vals = []
            for k in ("type", "status", "capacity", "voltage_now", "current_now", "charge_now", "charge_full",
                      "charge_full_design", "cycle_count", "online", "temp", "cur_freq", "brightness",
                      "max_brightness"):
                v = read(f"{d}/{k}")
                if v is not None:
                    vals.append(f"{k}={v}")
            lines.append(f"{d}: {' '.join(vals)}")
    lines.append(f"/tmp/battery.percent: {read('/tmp/battery.percent')}")
    for k in ("scaling_governor", "scaling_available_governors", "scaling_max_freq", "cpuinfo_max_freq",
              "cpuinfo_min_freq"):
        lines.append(f"policy0 {k}: {read('/sys/devices/system/cpu/cpufreq/policy0/' + k)}")
    lines.append(f"cpus present {read('/sys/devices/system/cpu/present')} online {read('/sys/devices/system/cpu/online')}")
    lines.append(f"zram: {zram()}")
    lines.append(f"swaps: {read('/proc/swaps')}")
    lines.append(f"mounts: {[l for l in (read('/proc/mounts') or '').splitlines() if l.startswith('/dev/')]}")
    lines.append("")
    lines.append(f"battery(): {battery()}")
    lines.append(f"cpu_policy(): {cpu_policy()}")
    lines.append(f"cpu_gpu_temp(): {cpu_gpu_temp()}  zones: {thermal_zones()}")
    lines.append(f"devfreq(): {devfreq()}")
    lines.append(f"memory(): {memory()}")
    lines.append(f"storage(): {[(d['label'], d['mount'], d['fs'], d['total']) for d in storage()]}")
    es = es_systems()
    lines.append(f"es_systems: {len(es) if es else None}")
    return "\n".join(lines) + "\n"
