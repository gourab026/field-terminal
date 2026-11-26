"""Field Terminal - system sampling thread.

Once a second: battery, CPU (per core), temperatures, memory. Only when a screen needs them:
programs (every 2 s), disk space (every 15 s). In the background, once, later and cached:
the game library scan and the battery-log analysis. Nothing here writes to the system.
"""
import json
import os
import threading
import time
from collections import deque

import battery as bt
import sysinfo as si

N_CPU_HIST = 90          # 90 s of per-core history
N_BAT_V = 180            # 3 min of voltage
N_BAT_I = 120            # 2 min of current, for time left
N_MEM = 90


class SysMonitor(threading.Thread):
    def __init__(self, log, cache_path):
        super().__init__(daemon=True)
        self.log, self.cache_path = log, cache_path
        self.lock, self.stop_ev = threading.Lock(), threading.Event()
        self.want_procs = self.want_disk = False
        self.cpu_load = si.CpuLoad()
        self.procs_s = si.ProcSampler()
        self.cpus = si.cpu_ids()
        self.bdir = si.find_battery()
        self.started = time.time()
        # latest values
        self.bat = {"present": False}
        self.design = None
        self.policy = {}
        self.cpu_total, self.core_load, self.core_mhz = 0.0, {}, {}
        self.cpu_temp = self.gpu_temp = None
        self.freq = {}
        self.mem, self.zram = {"total": None}, None
        self.disks = []
        self.procs, self.nprocs = [], 0
        self.bright = None
        self.uptime = None
        # history
        self.core_hist = {c: deque([None] * N_CPU_HIST, maxlen=N_CPU_HIST) for c in self.cpus}
        self.mhz_hist = {c: deque([None] * N_CPU_HIST, maxlen=N_CPU_HIST) for c in self.cpus}
        self.load_hist = deque([None] * N_CPU_HIST, maxlen=N_CPU_HIST)
        self.temp_hist = deque([None] * N_CPU_HIST, maxlen=N_CPU_HIST)
        self.mem_hist = deque([None] * N_MEM, maxlen=N_MEM)
        self.v_hist = deque([None] * N_BAT_V, maxlen=N_BAT_V)
        self.i_hist = deque(maxlen=N_BAT_I)
        self.pct_hist = deque(maxlen=3600)          # (t, pct) for the drop rate, 1 h
        self.run_start = None                        # when the current discharge started (in-app)
        # slow jobs
        self.games = None                            # {"total", "systems": [...], "ports", "when"}
        self.games_busy = False
        self.analysis = None
        self.analysis_busy = False
        self._proc_fresh = True
        self._last_disk = self._last_proc = self._last_slow = self._last_analysis = 0.0
        self._status = None

    # ----------------------------------------------------------------- thread
    def run(self):
        threading.Thread(target=self._slow_jobs, daemon=True).start()
        while not self.stop_ev.is_set():
            t0 = time.time()
            try:
                self.tick(t0)
            except Exception as exc:          # a sensor glitch must never kill the app
                self.log.add(f"System sampling error: {exc}", "error")
            self.stop_ev.wait(max(0.05, 1.0 - (time.time() - t0)))

    def tick(self, now):
        self._battery(now)
        self._cpu()
        self._memory(now)
        if now - self._last_slow >= 5:
            self._last_slow = now
            self.policy = si.cpu_policy()
            self.freq = si.devfreq()
            self.bright = si.brightness_pct()
            self.uptime = si.uptime()
        if self.want_disk and now - self._last_disk >= 15:
            self._last_disk = now
            disks = si.storage()
            with self.lock:
                self.disks = disks
        if self.want_procs and now - self._last_proc >= 2:
            self._last_proc = now
            raw = self.procs_s.sample()
            if self._proc_fresh:             # the first sample has no "before": its % would be wrong
                self._proc_fresh = False
            else:
                merged = si.merge_procs(raw)
                with self.lock:
                    self.procs, self.nprocs = merged, len(raw)

    # ----------------------------------------------------------------- readers
    def _battery(self, now):
        b = si.battery(self.bdir)
        if self.design is None:
            self.design = b.get("design_mah") or si.DESIGN_MAH_DEFAULT
        st = b.get("status")
        with self.lock:
            self.bat = b
            self.v_hist.append(b.get("mv"))
            if b.get("ma") is not None:
                self.i_hist.append(b["ma"])
            if b.get("pct") is not None:
                self.pct_hist.append((now, b["pct"]))
        if st != self._status:
            with self.lock:                  # a new state: old current and % samples no longer apply
                self.i_hist.clear()
                self.pct_hist.clear()
                if b.get("pct") is not None:
                    self.pct_hist.append((now, b["pct"]))
            if st == "Discharging" and self._status is not None:     # a real unplug, not app start
                self.run_start = now
            if self._status is not None:
                self.log.add(f"Battery: {self._status} -> {st} at {b.get('pct')}%")
            self._status = st

    def _cpu(self):
        total, per = self.cpu_load.sample()
        mhz = {c: si.cpu_mhz(c) for c in self.cpus}
        zones = si.thermal_zones()
        ct, gt = si.cpu_gpu_temp(zones)
        with self.lock:
            self.cpu_total, self.core_load, self.core_mhz = total, per, mhz
            self.cpu_temp, self.gpu_temp = ct, gt
            self.load_hist.append(total)
            self.temp_hist.append(ct)
            for c in self.cpus:
                self.core_hist[c].append(per.get(c))
                self.mhz_hist[c].append(mhz.get(c))

    def _memory(self, now):
        m = si.memory()
        z = si.zram() if int(now) % 5 == 0 or self.zram is None else self.zram
        with self.lock:
            self.mem, self.zram = m, z
            self.mem_hist.append(100.0 * m["used"] / m["total"] if m.get("total") else None)

    def reset_procs(self):
        """Program list shown again after a while: start from two fresh samples."""
        self.procs_s.prev, self.procs_s.prev_total = {}, None
        self._proc_fresh = True
        self._last_proc = 0
        with self.lock:
            self.procs, self.nprocs = [], 0

    # ----------------------------------------------------------------- derived
    def ma_avg(self):
        """Average current of the last 2 minutes; None until a full minute has been measured."""
        with self.lock:
            v = list(self.i_hist)
        return sum(v) / len(v) if len(v) >= 60 else None

    def drop_per_hour(self):
        """% per hour over this discharge (needs at least 10 min and a 1 % drop)."""
        with self.lock:
            pts = list(self.pct_hist)
        if len(pts) < 2:
            return None
        (t0, p0), (t1, p1) = pts[0], pts[-1]
        if t1 - t0 < 600 or p0 - p1 < 1:
            return None
        return (p0 - p1) / ((t1 - t0) / 3600.0)

    def capacity(self):
        h = self.analysis.health if self.analysis else None
        return (h or {}).get("mah") or self.design

    def time_left(self):
        b = self.bat
        if b.get("status") != "Discharging":
            return None, ""
        return bt.time_left(b.get("pct"), self.ma_avg(), self.capacity(), self.drop_per_hour())

    def time_to_full(self):
        """Only below 80 %: above it the charger slows down and a straight estimate is far too short."""
        b = self.bat
        if b.get("status") != "Charging" or b.get("pct") is None or b["pct"] >= 80:
            return None
        return bt.time_to_full(b.get("pct"), self.ma_avg(), self.capacity())

    # ----------------------------------------------------------------- slow jobs
    def _slow_jobs(self):
        if self.stop_ev.wait(2.0):
            return
        self.refresh_analysis()              # battery first: it is what DASH and POWER show
        self.scan_games()
        while not self.stop_ev.is_set():
            if time.time() - self._last_analysis > 600:
                self.refresh_analysis()
            self.stop_ev.wait(30)

    def refresh_analysis(self):
        if self.analysis_busy:                # one at a time, however often POWER is opened
            return
        self.analysis_busy = True
        self._last_analysis = time.time()
        try:
            rows = bt.load(months=2)
            self.analysis = bt.Analysis(rows, self.design or si.DESIGN_MAH_DEFAULT)
        except Exception as exc:
            self.log.add(f"Battery history unreadable: {exc}", "warn")
        finally:
            self.analysis_busy = False

    def _load_cache(self):
        try:
            with open(self.cache_path) as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def _save_cache(self, data):
        try:
            tmp = self.cache_path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(data, f)
            os.replace(tmp, self.cache_path)
        except OSError:
            pass

    def scan_games(self, force=False):
        """Count games per system. A system is re-scanned only when one of its folders changed
        (or always with force). Nothing is saved or shown from a scan that was interrupted."""
        if self.games_busy:
            return
        self.games_busy = True
        try:
            systems = si.es_systems()
            known = systems is not None
            if not known:                # no EmulationStation list: sizes only, the count is unknown
                systems = [(n, n, f"{si.ROMS}/{n}", set()) for n in si.listdir(si.ROMS)
                           if os.path.isdir(si.P(f"{si.ROMS}/{n}"))]
            cache = {} if force else self._load_cache().get("games", {})
            out, new_cache, seen = [], {}, set()
            for name, full, folder, exts in systems:
                if folder in seen or not os.path.isdir(si.P(folder)):
                    continue
                seen.add(folder)
                sig = f"{si.dir_signature(folder)}|{','.join(sorted(exts))}"
                c = cache.get(folder)
                if c and c.get("sig") == sig:
                    games, size = c["games"], c["bytes"]
                else:
                    games, size = si.scan_system(folder, exts, is_ports=(name == "ports"), stop=self.stop_ev)
                if self.stop_ev.is_set():
                    return                   # interrupted: keep the old numbers, save nothing
                new_cache[folder] = {"sig": sig, "games": games, "bytes": size}
                if games or size > 1048576:
                    out.append({"name": name, "full": full, "games": games if known else None, "bytes": size})
            out.sort(key=lambda s: -s["bytes"])
            ports = next((s["games"] for s in out if s["name"] == "ports"), 0) if known else 0
            data = self._load_cache()
            data["games"] = new_cache
            self._save_cache(data)
            self.games = {"total": sum(s["games"] for s in out) if known else None, "systems": out,
                          "ports": ports, "when": time.time(),
                          "nsys": sum(1 for s in out if s["games"]) if known else len(out)}
        except Exception as exc:
            self.log.add(f"Game count failed: {exc}", "warn")
        finally:
            self.games_busy = False
