"""Field Terminal - battery history (written by the boot logger) and the estimates built on it.

Log format, one line a minute, files battery-YYYYMM.csv in DATA_DIR:
    epoch,uptime_s,status,mV,mA,pct,kernel_pct,cpu_C,brightness_pct
Empty fields mean "not available". Lines starting with # are comments.

Estimates (all shown with how they were made, never as a bare guess):

* Usable capacity, per discharge run of the log:
      C = sum(I * dt) / (pct_start - pct_end)        [mAh / fraction of the battery used]
  Only runs that used >= 30 % over >= 30 min count; outliers (< 30 % or > 150 % of design) are dropped.
* Health = median of the last 5 capacities / design capacity.
* Time left = pct * C / average current of the last 2 minutes.
  Without a current sensor: pct / (drop in % per hour over the current run).
"""
import os
import statistics
import time
from collections import namedtuple

import sysinfo as si

DATA_DIR = "/userdata/system/fieldterminal"
GAP = 180                    # seconds between lines: more means the handheld was off or asleep
MIN_DSOC, MIN_RUN = 30, 1800
Row = namedtuple("Row", "t up status mv ma pct kpct temp bright")


def _num(s, cast=float):
    try:
        return cast(s) if s not in ("", None) else None
    except ValueError:
        return None


def _rng(v, lo, hi):
    return v if v is not None and lo <= v <= hi else None


def parse_line(line, now=None):
    """One log line -> Row, or None. Values outside physical limits become None, and lines with an
    impossible time (clock not set, a torn write gluing two lines) are dropped."""
    if not line or line.startswith("#"):
        return None
    p = line.rstrip("\n").split(",")
    if len(p) < 9:
        return None
    t = _num(p[0], int)
    if t is None or not 1600000000 <= t <= (now or time.time()) + 2 * 86400:
        return None
    status = p[2] if p[2] in ("Charging", "Discharging", "Full", "Not charging", "Unknown") else None
    return Row(t, _rng(_num(p[1]), 0, 10 ** 8), status, _rng(_num(p[3]), 2500, 5000), _rng(_num(p[4]), 0, 10000),
               _rng(_num(p[5], int), 0, 100), _rng(_num(p[6], int), 0, 100), _rng(_num(p[7]), -40, 150),
               _rng(_num(p[8], int), 0, 100))


def log_files(data_dir=None):
    d = si.P(data_dir or DATA_DIR)
    try:
        names = sorted(n for n in os.listdir(d) if n.startswith("battery-") and n.endswith(".csv"))
    except OSError:
        return []
    return [os.path.join(d, n) for n in names]


def load(data_dir=None, months=3):
    rows = []
    for path in log_files(data_dir)[-months:]:
        try:
            with open(path, errors="ignore") as f:
                for line in f:
                    r = parse_line(line)
                    if r:
                        rows.append(r)
        except OSError:
            continue
    rows.sort(key=lambda r: r.t)
    return rows


def _discharging(r):
    return r.status == "Discharging"


def runs(rows):
    """Split into stretches of continuous discharging (no gap, no reboot)."""
    out, cur = [], []
    for r in rows:
        if cur:
            prev = cur[-1]
            broken = (r.t - prev.t > GAP or (r.up is not None and prev.up is not None and r.up < prev.up)
                      or not _discharging(r))
            if broken:
                if len(cur) >= 2:
                    out.append(cur)
                cur = []
        if _discharging(r):
            cur.append(r)
    if len(cur) >= 2:
        out.append(cur)
    return out


def used_mah(run):
    """Charge drawn over a run, trapezoid rule on the current samples."""
    q = 0.0
    for a, b in zip(run, run[1:]):
        if a.ma is None or b.ma is None:
            continue
        dt = b.t - a.t
        if 0 < dt <= GAP:
            q += (a.ma + b.ma) / 2.0 * dt / 3600.0
    return q


def capacity_samples(rows, design):
    out = []
    for run in runs(rows):
        if run[0].pct is None or run[-1].pct is None:
            continue
        dsoc = run[0].pct - run[-1].pct
        dur = run[-1].t - run[0].t
        if dsoc < MIN_DSOC or dur < MIN_RUN:
            continue
        if sum(1 for r in run if r.ma is not None) < len(run) * 0.8:
            continue
        cap = used_mah(run) / (dsoc / 100.0)
        if design and not (0.3 * design <= cap <= 1.2 * design):
            continue
        out.append({"end": run[-1].t, "mah": cap, "dsoc": dsoc, "hours": dur / 3600.0})
    return out


def health(rows, design):
    """{'pct', 'mah', 'samples'} or {'learning': progress 0..1} while there is not enough data."""
    samples = capacity_samples(rows, design)
    if not samples:
        best = 0.0
        for run in runs(rows):
            if run[0].pct is not None and run[-1].pct is not None:
                best = max(best, min(1.0, (run[0].pct - run[-1].pct) / MIN_DSOC,
                                     (run[-1].t - run[0].t) / MIN_RUN))
        return {"learning": best, "samples": 0}
    caps = [s["mah"] for s in samples[-5:]]
    mah = statistics.median(caps)
    return {"pct": 100.0 * mah / design if design else None, "mah": mah, "samples": len(samples)}


def last_full_discharge(rows):
    """Most recent run from >= 90 % down to <= 10 % or to an automatic low-battery shutdown."""
    best = None
    for run in runs(rows):
        a, b = run[0], run[-1]
        if a.pct is None or a.pct < 90:
            continue
        low_end = (b.pct is not None and b.pct <= 10) or (b.mv is not None and b.mv <= 3450)
        if low_end:
            best = run
    if not best:
        return None
    pts = [(r.t, r.mv) for r in best if r.mv is not None]
    return {"start": best[0].t, "end": best[-1].t, "dur": best[-1].t - best[0].t,
            "v_start": best[0].mv, "v_end": best[-1].mv, "points": pts}


PLUGGED = ("Charging", "Full", "Not charging")


def since_charge(rows):
    """Time the handheld last came off the charger, or None. Two signs of a charge:
    the status goes from charging/full to discharging, or the battery is 10 points fuller after
    the handheld was off or asleep (charged while switched off)."""
    t = None
    for a, b in zip(rows, rows[1:]):
        if a.status in PLUGGED and _discharging(b):
            t = b.t
        elif a.pct is not None and b.pct is not None and b.pct - a.pct >= 10 and _discharging(b):
            gap = b.t - a.t > GAP or (a.up is not None and b.up is not None and b.up < a.up)
            if gap:
                t = b.t
    return t


def last_shutdown(rows):
    """(time, mV) of the last line written before the handheld was switched off."""
    found = None
    for a, b in zip(rows, rows[1:]):
        if a.up is not None and b.up is not None and b.up < a.up:
            found = (a.t, a.mv)
    return found


def last_line_age(data_dir=None, now=None):
    files = log_files(data_dir)
    if not files:
        return None
    try:
        return (now or time.time()) - os.path.getmtime(files[-1])
    except OSError:
        return None


class Analysis:
    """Everything the screens need from the log, recomputed in the background now and then."""

    def __init__(self, rows, design):
        self.rows = len(rows)
        self.first = rows[0].t if rows else None
        self.health = health(rows, design)
        self.full = last_full_discharge(rows)
        self.since_charge = since_charge(rows)
        self.shutdown = last_shutdown(rows)
        self.when = time.time()


def time_left(pct, ma_avg, cap_mah, pct_per_h=None):
    """Seconds until empty and how it was worked out, or (None, reason)."""
    if pct is None:
        return None, "no battery reading"
    if ma_avg and ma_avg > 5 and cap_mah:
        return pct / 100.0 * cap_mah / ma_avg * 3600.0, "by current draw"
    if pct_per_h and pct_per_h > 0.2:
        return pct / pct_per_h * 3600.0, "by % drop"
    return None, "measuring"


def time_to_full(pct, ma_avg, cap_mah):
    if pct is None or not ma_avg or ma_avg < 30 or not cap_mah:
        return None
    return (100 - pct) / 100.0 * cap_mah / ma_avg * 3600.0
