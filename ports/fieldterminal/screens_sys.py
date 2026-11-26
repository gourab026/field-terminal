"""Field Terminal - system screens: DASH, POWER, CPU, MEMORY, DISK, PROCS, LOG.

Each screen answers one question and shows only what helps answer it.
A screen is a small class: enter() / leave() switch its data sources on and off,
handle(action) reacts to buttons, draw(surface) returns the footer hints.
"""
import os
import time

import pygame

import actions
import netcore as nc
import theme as th
from theme import C, TOP, BOTTOM, W

PAD, GAP = 6, 6


def grid(cols, rows, y0=TOP + PAD, y1=BOTTOM - PAD, x0=PAD, x1=W - PAD):
    """Equal tiles: [[rect per column] per row]."""
    tw = (x1 - x0 - GAP * (cols - 1)) / cols
    thh = (y1 - y0 - GAP * (rows - 1)) / rows
    return [[pygame.Rect(round(x0 + c * (tw + GAP)), round(y0 + r * (thh + GAP)), round(tw), round(thh))
             for c in range(cols)] for r in range(rows)]


def temp_color(t):
    return C["red"] if t is not None and t >= 75 else C["amb"]


def trend(vals):
    v = [x for x in vals if x is not None]
    if len(v) < 10:
        return "measuring"
    a, b = sum(v[:10]) / 10, sum(v[-10:]) / 10
    return "rising" if b - a > 3 else "falling" if a - b > 3 else "steady"


def since_charge(sm):
    """Latest of: the logged unplug (or charge while off), and an unplug seen while the app was open."""
    an = sm.analysis
    times = [t for t in ((an.since_charge if an else None), sm.run_start) if t]
    return max(times) if times else None


def health_text(an):
    """('94%', mAh) / ('learning', progress) / None while the history is still being read."""
    if an is None:
        return None
    h = an.health
    if h.get("pct"):
        return f"{min(100.0, h['pct']):.0f}%", h
    return "learning", h


def mode_name(gov):
    m = actions.mode_of(gov)
    return actions.MODES[m][0] if m is not None else (gov or "n/a")


class Screen:
    name = ""

    def enter(self, app):
        pass

    def leave(self, app):
        pass

    def handle(self, app, a):
        return False

    def draw(self, app, s):
        return []


# =========================================================================== DASH

class Dash(Screen):
    """Is everything OK right now?"""
    name = "DASH"

    def enter(self, app):
        app.sm.reset_procs()
        app.sm.want_procs = app.sm.want_disk = True

    def leave(self, app):
        app.sm.want_procs = app.sm.want_disk = False

    def draw(self, app, s):
        (r1, r2, r3), (r4, r5, r6) = grid(3, 2)
        self.battery(app, s, r1)
        self.cpu(app, s, r2)
        self.memory(app, s, r3)
        self.storage(app, s, r4)
        self.network(app, s, r5)
        self.top(app, s, r6)
        return [("L1 R1", "tabs")]

    # -- tiles
    def battery(self, app, s, r):
        f, sm = app.fonts, app.sm
        b, an = sm.bat, sm.analysis
        ht = health_text(an)
        th.tile(s, f, r, "BATTERY", f"health {ht[0]}" if ht else "")
        x, w = r.x + 8, r.w - 16
        if not b.get("present"):
            th.text(s, f, f.m, "no battery found", C["t3"], (x, r.y + 30))
            return
        pct = b.get("pct")
        st = (b.get("status") or "unknown").lower()
        th.battery_icon(s, (x, r.y + 24, 60, 28), (pct or 0) / 100, charging=st == "charging")
        th.text(s, f, f.xxl, f"{pct}%" if pct is not None else "--", C["t1"], (x + 74, r.y + 18), glow=True)
        y = r.y + 62
        vi = []
        if b.get("mv"):
            vi.append(f"{b['mv'] / 1000:.2f} V")
        if b.get("ma") is not None:
            vi.append(f"{b['ma']:.0f} mA")
        th.text(s, f, f.m, " · ".join(vi) or "voltage n/a", C["t2"], (x, y))
        y += 22
        rate = sm.drop_per_hour()
        th.kv(s, f, f.m, x, y, w, st, f"-{rate:.0f}%/h" if rate else "", C["t1"], C["t2"])
        y += 24
        if st == "discharging":
            secs, _how = sm.time_left()
            main = (f"{th.fmt_short(secs)} left", C["amb"]) if secs else ("measuring…", C["t3"])
        elif st == "charging":
            ttf = sm.time_to_full()
            main = (f"full in {th.fmt_short(ttf)}", C["amb"]) if ttf else \
                ("nearly full" if (pct or 0) >= 80 else "charging", C["amb"])
        else:
            main = (st, C["t1"])
        th.text(s, f, f.l, main[0], main[1], (x, y), glow=True)
        y += 28
        since = since_charge(sm)
        if st == "discharging":
            th.text(s, f, f.m, f"since charge: {th.fmt_short(time.time() - since)}" if since else
                    "since charge: not seen yet", C["t2"] if since else C["t3"], (x, y))
        else:
            plugged = "on charger" if b.get("plugged") or st in ("charging", "full") else "unplugged"
            th.text(s, f, f.m, plugged, C["t2"], (x, y))
        y += 22
        if sm.uptime:
            th.text(s, f, f.m, f"on for {th.fmt_short(sm.uptime)}", C["t3"], (x, y))

    def cpu(self, app, s, r):
        f, sm = app.fonts, app.sm
        th.tile(s, f, r, "CPU", "mode " + mode_name(sm.policy.get("governor")))
        x, w = r.x + 8, r.w - 16
        with sm.lock:
            loads = [sm.core_load.get(c) for c in sm.cpus]
            mhz = [v for v in sm.core_mhz.values() if v]
            hist = list(sm.load_hist)[-60:]
        for i, ld in enumerate(loads[:4]):
            th.vbar(s, (x + i * 20, r.y + 24, 15, 64), (ld or 0) / 100)
        bx = x + min(4, len(loads)) * 20 + 8
        th.text(s, f, f.xl, f"{max(mhz):.0f}" if mhz else "--", C["t1"], (bx, r.y + 22), glow=True)
        th.text(s, f, f.m, "MHz now", C["t2"], (bx, r.y + 52))
        mx = sm.policy.get("max_mhz")
        if mx:
            th.text(s, f, f.m, f"max {mx:.0f}", C["t3"], (bx, r.y + 70))
        y = r.y + 96
        t = sm.cpu_temp
        th.text(s, f, f.l, f"{t:.0f}°C" if t is not None else "--°C", temp_color(t), (x, y), glow=True)
        if sm.gpu_temp is not None:
            th.text(s, f, f.m, f"gpu {sm.gpu_temp:.0f}°C", C["t2"], (x + w, y + 3), "topright")
        y += 28
        th.kv(s, f, f.m, x, y, w, f"load {sm.cpu_total:.0f}%", "60 s", C["t1"], C["t3"])
        th.line_graph(s, (x, r.bottom - 36, w, 28), [(hist, C["t1"])], vmin=0, vmax=100)

    def memory(self, app, s, r):
        f, sm = app.fonts, app.sm
        m, z = sm.mem, sm.zram
        total = m.get("total")
        th.tile(s, f, r, "MEMORY", th.fmt_mb(total) if total else "")
        x, w = r.x + 8, r.w - 16
        if not total:
            th.text(s, f, f.m, "n/a", C["t3"], (x, r.y + 30))
            return
        pct = 100.0 * m["used"] / total
        th.text(s, f, f.xxl, f"{pct:.0f}%", C["amb"] if pct >= 90 else C["t1"], (x, r.y + 18), glow=True)
        th.text(s, f, f.m, "in use", C["t2"], (x + w, r.y + 34), "topright")
        th.seg_bar(s, (x, r.y + 62, w, 10), pct / 100, C["amb"] if pct >= 90 else None)
        y = r.y + 80
        th.kv(s, f, f.m, x, y, w, f"{th.fmt_mb(m['available'])} free", f"{m['used'] / 1048576:.0f} used",
              C["t1"], C["t2"])
        y += 22
        if z is None:
            zs = "not set up"
        elif not z["active"]:
            zs = "off"
        elif z["stored"] == 0:
            zs = "on, unused"
        else:
            zs = f"+{z['gained'] / 1048576:.0f} MB"
        th.kv(s, f, f.m, x, y, w, "zram", zs, C["t1"], C["t2"])
        y += 22
        with sm.lock:
            hist = list(sm.mem_hist)
        th.kv(s, f, f.m, x, y, w, "90 s", trend(hist), C["t2"], C["t3"])
        th.line_graph(s, (x, r.bottom - 36, w, 28), [(hist, C["t1"])], vmin=0, vmax=100)

    def storage(self, app, s, r):
        f, sm = app.fonts, app.sm
        g = sm.games
        th.tile(s, f, r, "STORAGE", f"{g['nsys']} systems" if g and g["total"] is not None else "")
        x, w = r.x + 8, r.w - 16
        with sm.lock:
            disks = list(sm.disks)
        y = r.y + 22
        for d in disks[:2]:
            used = 100.0 * d["used"] / d["total"]
            warn = used >= 80
            th.kv(s, f, f.m, x, y, w, d["label"] + (" SD" if d["label"] == "Games" else ""), f"{used:.0f}%",
                  C["t1"], C["amb"] if warn else C["t2"])
            th.seg_bar(s, (x, y + 21, w, 10), used / 100, C["amb"] if warn else None)
            th.text(s, f, f.m, f"{th.fmt_gb(d['free'])} free of {th.fmt_gb(d['total'])}", C["t2"], (x, y + 34))
            y += 62
        if not disks:
            th.text(s, f, f.m, "reading…", C["t3"], (x, y))
        pygame.draw.line(s, C["line"], (x, r.bottom - 40), (x + w, r.bottom - 40))
        if g and g["total"] is not None:
            th.text(s, f, f.xl, f"{g['total']}", C["t1"], (x, r.bottom - 36), glow=True)
            th.text(s, f, f.m, "games", C["t2"], (x + w, r.bottom - 26), "topright")
        elif g:
            th.text(s, f, f.m, "game count n/a", C["t3"], (x, r.bottom - 28))
        else:
            th.text(s, f, f.m, "counting games…", C["t3"], (x, r.bottom - 28))

    def network(self, app, s, r):
        f, mon = app.fonts, app.mon
        info, link = mon.info, mon.link
        il = mon.inet.last()
        online = il is not None and il >= 0
        state = "online" if online else ("offline" if mon.inet.streak >= 3 else "checking")
        th.tile(s, f, r, "NETWORK", state, C["t3"] if online else C["amb"])
        x, w = r.x + 8, r.w - 16
        y = r.y + 22
        if info.get("wireless"):
            sig = link.get("signal")
            th.signal_bars(s, x, y + 22, nc.signal_quality(sig) if sig is not None else 0)
            th.text(s, f, f.m, link.get("ssid") or "not connected", C["t1"], (x + 44, y + 4), maxw=w - 44)
            y += 28
            th.text(s, f, f.m, f"{link.get('band', '?')} · {sig} dBm" if sig is not None else "no Wi-Fi link",
                    C["t2"], (x, y))
        else:
            th.text(s, f, f.m, f"{mon.iface or 'no network'}, wired", C["t1"], (x, y + 4))
            y += 28
            th.text(s, f, f.m, mon.gateway or "no gateway", C["t2"], (x, y))
        y += 22
        th.text(s, f, f.m, info.get("ip") or "no IP address", C["amb"], (x, y))
        y += 22
        th.kv(s, f, f.m, x, y, w, "ping", f"{il:.0f} ms" if online else "-", C["t2"], C["t1"])
        y += 22
        with mon.lock:
            rx, tx = list(mon.rx_hist)[-60:], list(mon.tx_hist)[-60:]
            rr, tr = mon.rx_rate, mon.tx_rate
        th.arrow(s, x, y + 6, "down", C["t1"])
        th.text(s, f, f.m, th.fmt_rate(rr), C["t1"], (x + 10, y))
        tr_r = th.text(s, f, f.m, th.fmt_rate(tr), C["t2"], (x + w, y), "topright")
        th.arrow(s, tr_r.x - 10, y + 6, "up", C["t2"])
        th.line_graph(s, (x, r.bottom - 36, w, 28), [(tx, C["t3"]), (rx, C["t1"])], vmin=0)

    def top(self, app, s, r):
        f, sm = app.fonts, app.sm
        with sm.lock:
            procs, n = list(sm.procs), sm.nprocs
        th.tile(s, f, r, "TOP USAGE", f"{n} running" if n else "")
        x, w = r.x + 8, r.w - 16
        if not procs:
            th.text(s, f, f.m, "reading…", C["t3"], (x, r.y + 26))
            return
        top = [p for p in procs if p["me"]][:1] + [p for p in procs if not p["me"]][:4]
        step = (r.h - 30) / 5
        for i, p in enumerate(top):
            y = round(r.y + 22 + i * step)
            th.kv(s, f, f.m, x, y, w, p["name"].lower() if not p["me"] else "this app", f"{p['cpu']:.0f}%",
                  C["t1"], C["amb"] if p["me"] else C["t2"])
            th.thin_bar(s, (x, y + 21, w, 4), min(1.0, p["cpu"] / 25))


# =========================================================================== POWER

class Power(Screen):
    """How long will it last, and is the battery wearing out?"""
    name = "POWER"

    def __init__(self):
        self._cache = (None, None)

    def _curve(self, an, full, n):
        """Voltage over the discharge, resampled to n points (cached per analysis)."""
        if self._cache[0] is an:
            return self._cache[1]
        pts = full["points"]
        t0, t1 = pts[0][0], pts[-1][0]
        vals = [None] * n
        for t, mv in pts:
            vals[min(n - 1, int((t - t0) / max(1, t1 - t0) * (n - 1)))] = mv / 1000
        last = None
        for i in range(n):               # fill the gaps between minutes
            if vals[i] is None:
                vals[i] = last
            last = vals[i]
        self._cache = (an, vals)
        return vals

    def enter(self, app):
        if app.sm.analysis is None or time.time() - app.sm.analysis.when > 120:
            app.bg(app.sm.refresh_analysis)

    def handle(self, app, a):
        if a == "a":
            app.toggle_logger()
            return True
        return False

    def draw(self, app, s):
        f, sm = app.fonts, app.sm
        b, an = sm.bat, sm.analysis
        st = (b.get("status") or "unknown").lower()
        # NOW
        r = th.tile(s, f, (6, TOP + 6, 310, 170), "NOW")
        x, w = r.x + 10, r.w - 20
        pct = b.get("pct")
        th.battery_icon(s, (x, r.y + 26, 100, 44), (pct or 0) / 100, charging=st == "charging")
        th.text(s, f, f.xxl, f"{pct}%" if pct is not None else "--", C["t1"], (x + 120, r.y + 22), glow=True)
        kp = b.get("kpct")
        if kp is not None and pct is not None and abs(kp - pct) >= 5:
            th.text(s, f, f.m, f"gauge: {kp}%", C["t3"], (x + 120, r.y + 58))
        vi = [f"{b['mv'] / 1000:.3f} V" if b.get("mv") else None,
              f"{b['ma']:.0f} mA" if b.get("ma") is not None else None, st]
        th.text(s, f, f.m, " · ".join(v for v in vi if v), C["t2"], (x, r.y + 80), maxw=w)
        if st == "discharging":
            secs, how = sm.time_left()
            th.text(s, f, f.l, f"{th.fmt_span(secs)} left" if secs else "measuring…",
                    C["amb"] if secs else C["t3"], (x, r.y + 102), glow=True)
            th.text(s, f, f.s, how + (", learned capacity" if how == "by current draw" and an and an.health.get("mah")
                                      else ""), C["t3"], (x, r.y + 128))
        elif st == "charging":
            ttf = sm.time_to_full()
            th.text(s, f, f.l, f"full in {th.fmt_span(ttf)}" if ttf else
                    ("nearly full" if (pct or 0) >= 80 else "charging"), C["amb"], (x, r.y + 102), glow=True)
            if not ttf and (pct or 0) >= 80:
                th.text(s, f, f.s, "the charger slows down above 80%", C["t3"], (x, r.y + 128))
        else:
            th.text(s, f, f.l, st, C["t1"], (x, r.y + 102), glow=True)
        bits = []
        if an and an.shutdown and an.shutdown[1]:
            bits.append(f"last shutdown at {an.shutdown[1] / 1000:.2f} V")
        if sm.bright is not None:
            bits.append(f"brightness {sm.bright}%")
        th.text(s, f, f.s, " · ".join(bits), C["t3"], (x, r.y + 146), maxw=w)

        # VOLTAGE last 3 min
        r = th.tile(s, f, (322, TOP + 6, 312, 170), "VOLTAGE · LAST 3 MIN",
                    f"{b['mv'] / 1000:.2f} V" if b.get("mv") else "")
        with sm.lock:
            vh = [v / 1000 if v else None for v in sm.v_hist]
        g = pygame.Rect(r.x + 44, r.y + 24, r.w - 54, r.h - 34)
        th.line_graph(s, g, [(vh, C["t1"])], vmin=3.4, vmax=4.2, refs=[(4.2, C["t3"]), (3.4, C["red"])])
        th.text(s, f, f.s, "4.2 V", C["t3"], (r.x + 8, g.y - 4))
        th.text(s, f, f.s, "3.4 V", C["red"], (r.x + 8, g.bottom - 14))

        # HEALTH
        r = th.tile(s, f, (6, TOP + 182, 200, 100), "HEALTH")
        x, w = r.x + 10, r.w - 20
        h = an.health if an else None
        if an is None:
            th.text(s, f, f.m, "reading history…", C["t3"], (x, r.y + 28))
        elif b.get("ma") is None and b.get("present"):
            th.text(s, f, f.m, "n/a: this battery", C["t3"], (x, r.y + 28))
            th.text(s, f, f.m, "reports no current", C["t3"], (x, r.y + 48))
        elif h and h.get("pct"):
            col = C["t1"] if h["pct"] >= 80 else C["amb"]
            th.text(s, f, f.xl, f"{min(100.0, h['pct']):.0f}%", col, (x, r.y + 20), glow=True)
            th.text(s, f, f.m, f"{h['mah']:.0f} of {sm.design:.0f} mAh", C["t2"], (x, r.y + 54))
            th.text(s, f, f.s, f"from {h['samples']} discharge{'s' if h['samples'] != 1 else ''}", C["t3"],
                    (x, r.y + 76))
        else:
            prog = (h or {}).get("learning", 0.0)
            th.text(s, f, f.l, "learning", C["amb"], (x, r.y + 22), glow=True)
            th.seg_bar(s, (x, r.y + 52, w, 8), prog)
            th.text(s, f, f.s, "needs one 30% discharge", C["t3"], (x, r.y + 66))
            th.text(s, f, f.s, "while the logger is on", C["t3"], (x, r.y + 80))

        # TIMES
        logging = app.logger_state()
        aside = {"on": "• logging every minute", "off": "logger off: A turns on", "demo": "• logging every minute",
                 "n/a": "logger needs the handheld"}[logging]
        r = th.tile(s, f, (212, TOP + 182, 422, 100), "TIMES", aside, C["t1"] if logging in ("on", "demo") else C["amb"])
        cw = (r.w - 20) / 3
        cols = []
        up = sm.uptime
        cols.append(("THIS SESSION", th.fmt_short(up) if up else "-",
                     "on since " + time.strftime("%H:%M", time.localtime(time.time() - up)) if up else ""))
        since = since_charge(sm)
        cols.append(("SINCE CHARGE", th.fmt_short(time.time() - since) if since else "-",
                     "since " + time.strftime("%d/%m %H:%M", time.localtime(since)) if since else "not seen yet"))
        full = an.full if an else None
        cols.append(("LAST FULL", th.fmt_short(full["dur"]) if full else "-",
                     f"{full['v_start'] / 1000:.2f} to {full['v_end'] / 1000:.2f} V" if full and full["v_start"]
                     else "none recorded yet"))
        for i, (lab, val, sub) in enumerate(cols):
            cx = round(r.x + 10 + i * cw)
            th.label(s, f, lab, C["t2"], (cx, r.y + 24))
            th.text(s, f, f.l, val, C["amb"] if i == 1 else C["t1"], (cx, r.y + 38), glow=True)
            th.text(s, f, f.s, sub, C["t3"], (cx, r.y + 66), maxw=round(cw) - 8)

        # LAST FULL DISCHARGE
        r = th.tile(s, f, (6, TOP + 288, 628, 108), "LAST FULL DISCHARGE",
                    th.fmt_span(full["dur"]) + time.strftime(" · ended %d/%m %H:%M", time.localtime(full["end"]))
                    if full else "")
        g = pygame.Rect(r.x + 44, r.y + 24, r.w - 54, r.h - 32)
        if full and len(full["points"]) >= 2:
            vals = self._curve(an, full, 240)
            th.line_graph(s, g, [(vals, C["t1"])], vmin=3.3, vmax=4.25, refs=[(4.2, C["t3"]), (3.4, C["red"])])
            th.text(s, f, f.s, "4.2 V", C["t3"], (r.x + 8, g.y - 2))
            th.text(s, f, f.s, "3.4 V", C["red"], (r.x + 8, g.bottom - 22))
        else:
            th.text(s, f, f.m, "No full discharge recorded yet.", C["t2"], (r.x + 10, r.y + 34))
            th.text(s, f, f.s, "The logger records one when the battery runs from full to empty.", C["t3"],
                    (r.x + 10, r.y + 58))
        return [("A", "logger on/off"), ("L1 R1", "tabs")]


# =========================================================================== CPU

class Cpu(Screen):
    """Why is it slow or hot?"""
    name = "CPU"

    def __init__(self):
        self.sel = None

    def enter(self, app):
        self.sel = None

    def handle(self, app, a):
        cur = actions.mode_of(app.sm.policy.get("governor"))
        if self.sel is None:
            self.sel = cur if cur is not None else 1
        if a == "up":
            self.sel = (self.sel - 1) % len(actions.MODES)
            return True
        if a == "down":
            self.sel = (self.sel + 1) % len(actions.MODES)
            return True
        if a == "a":
            app.set_cpu_mode(self.sel)
            return True
        return False

    def draw(self, app, s):
        f, sm = app.fonts, app.sm
        r = th.tile(s, f, (6, TOP + 6, 628, 226), "CORES · LAST 90 S")
        lx = r.right - 120
        pygame.draw.rect(s, C["t2"], (lx, r.y + 8, 8, 8))
        th.label(s, f, "load", C["t2"], (lx + 12, r.y + 7))
        pygame.draw.line(s, C["amb"], (lx + 50, r.y + 11), (lx + 62, r.y + 11), 2)
        th.label(s, f, "MHz", C["amb"], (lx + 66, r.y + 7))
        hw = sm.policy.get("hw_max_mhz") or sm.policy.get("max_mhz") or 1500
        n = max(1, len(sm.cpus))
        cw = (r.w - 20 - 8 * (n - 1)) / n
        with sm.lock:
            data = [(c, sm.core_mhz.get(c), sm.core_load.get(c), list(sm.core_hist[c]), list(sm.mhz_hist[c]))
                    for c in sm.cpus]
        for i, (c, mhz, ld, lh, mh) in enumerate(data):
            x = round(r.x + 10 + i * (cw + 8))
            th.text(s, f, f.m, f"Core {c + 1}", C["t1"], (x, r.y + 24))
            th.text(s, f, f.xl, f"{mhz:.0f}" if mhz else "off", C["t1"] if mhz else C["t3"], (x, r.y + 44), glow=True)
            th.text(s, f, f.s, f"MHz · load {ld:.0f}%" if ld is not None else "MHz", C["t2"], (x, r.y + 76))
            g = pygame.Rect(x, r.y + 98, round(cw), r.h - 108)
            pygame.draw.rect(s, C["trk"], g)
            th.line_graph(s, g, [(lh, C["t2"])], vmin=0, vmax=100, baseline=False)
            th.line_graph(s, g, [([v if v else None for v in mh], C["amb"])], vmin=0, vmax=hw, baseline=False)

        # temperature
        r = th.tile(s, f, (6, TOP + 238, 310, 158), "TEMPERATURE · CPU")
        x, w = r.x + 10, r.w - 20
        t = sm.cpu_temp
        th.text(s, f, f.xxl, f"{t:.1f}°C" if t is not None else "n/a", temp_color(t), (x, r.y + 20), glow=True)
        with sm.lock:
            tch = list(sm.temp_hist)
        vals = [v for v in tch if v is not None]
        if vals:
            lo, hi = min(vals) - 2, max(vals) + 2
            th.line_graph(s, (r.x + 170, r.y + 26, r.w - 180, 36), [(tch, C["amb"])], vmin=lo, vmax=max(hi, lo + 5))
        y = r.y + 70
        gpu = []
        if sm.freq.get("gpu"):
            gpu.append(f"{sm.freq['gpu']:.0f} MHz")
        if sm.gpu_temp is not None:
            gpu.append(f"{sm.gpu_temp:.1f}°C")
        th.kv(s, f, f.m, x, y, w, "GPU", " · ".join(gpu) or "n/a", C["t2"], C["t1"])
        y += 22
        th.kv(s, f, f.m, x, y, w, "memory", f"{sm.freq['mem']:.0f} MHz" if sm.freq.get("mem") else "n/a", C["t2"],
              C["t1"])
        y += 22
        hot = t is not None and t >= 75
        th.text(s, f, f.s, "hot: the CPU slows itself down to cool off" if hot else
                "the CPU slows itself down if it gets too hot", C["red"] if hot else C["t3"], (x, y + 4), maxw=w)

        # mode
        r = th.tile(s, f, (322, TOP + 238, 312, 158), "CPU MODE")
        gov = sm.policy.get("governor")
        cur = actions.mode_of(gov)
        sel = self.sel if self.sel is not None else (cur if cur is not None else 1)
        avail = sm.policy.get("available") or []
        for i, (name, desc, _g) in enumerate(actions.MODES):
            y = r.y + 22 + i * 30
            rr = pygame.Rect(r.x + 6, y, r.w - 12, 28)
            if i == sel:
                pygame.draw.rect(s, C["sel"], rr)
                pygame.draw.rect(s, C["t1"], (rr.x, rr.y, 2, rr.h))
            offered = actions.governor_for(i, avail) is not None
            pygame.draw.rect(s, C["t1"] if i == cur else C["trk"], (rr.x + 10, rr.y + 10, 8, 8))
            th.text(s, f, f.m, name, C["t1"] if offered else C["t3"], (rr.x + 28, rr.y + 4), glow=i == cur)
            th.text(s, f, f.m, desc if offered else "not offered", C["t2"] if offered else C["t3"], (rr.x + 120, rr.y + 4))
        th.text(s, f, f.s, f"now: {gov or 'n/a'} · saved for next boot", C["t3"], (r.x + 10, r.bottom - 22),
                maxw=r.w - 20)
        return [("UP DN", "select"), ("A", "apply"), ("L1 R1", "tabs")]


# =========================================================================== MEMORY

class Memory(Screen):
    """Will this game run smoothly?"""
    name = "MEMORY"

    def handle(self, app, a):
        if a == "a":
            app.toggle_zram_boot()
            return True
        if a == "x":
            app.toggle_zram_now()
            return True
        return False

    def draw(self, app, s):
        f, sm = app.fonts, app.sm
        m, z = sm.mem, sm.zram
        total = m.get("total")
        r = th.tile(s, f, (6, TOP + 6, 628, 170), "RAM")
        x, w = r.x + 10, r.w - 20
        if total:
            pct = 100.0 * m["used"] / total
            pr = th.text(s, f, f.xxl, f"{pct:.0f} %", C["amb"] if pct >= 90 else C["t1"], (x, r.y + 20), glow=True)
            th.text(s, f, f.m, "in use", C["t2"], (pr.right + 10, r.y + 36))
            th.text(s, f, f.m, f"{m['used'] / 1048576:.0f} MB used · {m['available'] / 1048576:.0f} free · "
                    f"{total / 1048576:.0f} total", C["t2"], (x, r.y + 62))
            th.seg_bar(s, (x, r.y + 86, w, 14), pct / 100, C["amb"] if pct >= 90 else None)
            with sm.lock:
                hist = list(sm.mem_hist)
            th.line_graph(s, (x, r.y + 108, w - 150, 50), [(hist, C["t1"])], vmin=0, vmax=100)
            th.text(s, f, f.s, "last 90 s", C["t3"], (x + w - 140, r.y + 110))
            th.text(s, f, f.s, trend(hist), C["t2"], (x + w - 140, r.y + 126))
            th.text(s, f, f.s, f"{th.fmt_mb(m['cache'])} is cache", C["t3"], (x + w - 140, r.y + 142))
        else:
            th.text(s, f, f.m, "n/a", C["t3"], (x, r.y + 30))

        r = th.tile(s, f, (6, TOP + 182, 628, 214), "ZRAM · COMPRESSED MEMORY")
        x, w = r.x + 10, r.w - 20
        if z is None:
            th.text(s, f, f.l, "not set up", C["t2"], (x, r.y + 28), glow=True)
            th.text(s, f, f.m, "Knulli's ZRAM service has not been started since boot.", C["t3"], (x, r.y + 60))
            th.text(s, f, f.m, "A switches it on at boot; X starts it now.", C["t3"], (x, r.y + 82))
        else:
            gr = th.text(s, f, f.xxl, f"+{z['gained'] / 1048576:.0f} MB", C["t1"], (x, r.y + 20), glow=True)
            th.text(s, f, f.m, "of memory gained now", C["t2"], (gr.right + 10, r.y + 36))
            size = max(1, z["size"])
            th.text(s, f, f.m, f"Stored: {z['stored'] / 1048576:.0f} MB", C["t1"], (x, r.y + 66))
            th.seg_bar(s, (x, r.y + 88, w, 10), z["stored"] / size)
            th.text(s, f, f.m, f"Takes in RAM: {z['in_ram'] / 1048576:.0f} MB", C["t1"], (x, r.y + 104))
            th.seg_bar(s, (x, r.y + 126, w, 10), z["in_ram"] / size, C["amb"])
            if not z["active"]:
                state = "Off"
            elif z["stored"] == 0:
                state = "On and waiting: not needed yet"
            else:
                state = f"On: holding {z['stored'] / 1048576:.0f} MB in {z['in_ram'] / 1048576:.0f} MB of RAM"
            th.text(s, f, f.m, f"{state} · size {z['size'] / 1048576:.0f} MB" + (f" · {z['algo']}" if z["algo"] else ""),
                    C["t2"], (x, r.y + 144), maxw=w)
        boot = app.zram_boot()
        bt_txt = {True: "yes", False: "no", None: "n/a"}[boot]
        th.text(s, f, f.m, f"At boot: {bt_txt}", C["t1"], (x, r.bottom - 26))
        th.text(s, f, f.s, "fixed size: half of RAM (Knulli's own service)", C["t3"], (x + w, r.bottom - 22), "topright")
        return [("A", "at boot on/off"), ("X", "start/stop now"), ("L1 R1", "tabs")]


# =========================================================================== DISK

class Disk(Screen):
    """Do I have room for more games?"""
    name = "DISK"

    def __init__(self):
        self.sel = 0

    def enter(self, app):
        app.sm.want_disk = True
        app.sm._last_disk = 0
        if app.sm.games is None and not app.sm.games_busy:
            app.bg(app.sm.scan_games)

    def leave(self, app):
        app.sm.want_disk = False

    def handle(self, app, a):
        g = app.sm.games
        n = len(g["systems"]) if g else 0
        if a in ("up", "down") and n:
            self.sel = (self.sel + (1 if a == "down" else -1)) % n
            return True
        if a == "x":
            app.bg(app.sm.scan_games, True)
            app.notify("Counting games again")
            return True
        return False

    def draw(self, app, s):
        f, sm = app.fonts, app.sm
        with sm.lock:
            disks = list(sm.disks)
        nd = max(1, len(disks))
        r = th.tile(s, f, (6, TOP + 6, 628, 24 + 44 * nd), "CARDS")
        x, w = r.x + 10, r.w - 20
        for i, d in enumerate(disks):
            y = r.y + 22 + i * 44
            used = 100.0 * d["used"] / d["total"]
            warn = used >= 80
            th.text(s, f, f.m, f"{d['label']}", C["t1"], (x, y))
            th.text(s, f, f.s, f"{d['mount']} · {d['fs']}", C["t3"], (x + 90, y + 4))
            th.text(s, f, f.m, f"{th.fmt_gb(d['free'])} free of {th.fmt_gb(d['total'])} · {used:.0f}% used",
                    C["amb"] if warn else C["t2"], (x + w, y), "topright")
            th.seg_bar(s, (x, y + 22, w, 10), used / 100, C["amb"] if warn else None)
        if not disks:
            th.text(s, f, f.m, "reading…", C["t3"], (x, r.y + 26))

        g = sm.games
        y0 = r.bottom + 6
        r = th.tile(s, f, (6, y0, 628, BOTTOM - PAD - y0), "GAMES PER SYSTEM",
                    "counting…" if sm.games_busy else (f"includes {g['ports']} ports" if g and g["ports"] else ""))
        x, w = r.x + 10, r.w - 20
        if not g:
            th.text(s, f, f.m, "Counting games in the background…", C["t3"], (x, r.y + 30))
            return [("L1 R1", "tabs")]
        th.label(s, f, "system", C["t3"], (x, r.y + 22))
        th.label(s, f, "games", C["t3"], (x + 330, r.y + 22), "topright")
        th.label(s, f, "size", C["t3"], (x + w, r.y + 22), "topright")
        rows = max(1, (r.h - 64) // 22)
        systems = g["systems"]
        sel = min(self.sel, max(0, len(systems) - 1))
        start = th.window_start(sel, len(systems), rows)
        biggest = max((x_["bytes"] for x_ in systems), default=1) or 1
        for i, sy in enumerate(systems[start:start + rows]):
            y = r.y + 36 + i * 22
            th.row_bg(s, (r.x + 4, y, r.w - 8, 22), start + i == sel)
            th.text(s, f, f.m, sy["full"], C["t1"], (x + 4, y + 1), maxw=220)
            th.text(s, f, f.m, str(sy["games"]) if sy["games"] is not None else "n/a", C["t1"], (x + 330, y + 1),
                    "topright")
            th.thin_bar(s, (x + 350, y + 9, w - 440, 4), sy["bytes"] / biggest)
            th.text(s, f, f.m, th.fmt_gb(sy["bytes"]), C["t2"], (x + w, y + 1), "topright")
        tot_b = sum(sy["bytes"] for sy in systems)
        th.text(s, f, f.m, f"{g['total']} games in {g['nsys']} systems · {th.fmt_gb(tot_b)}" if g["total"] is not None
                else f"{g['nsys']} folders · {th.fmt_gb(tot_b)} · game count n/a (no system list)", C["amb"],
                (x, r.bottom - 24), glow=True)
        th.text(s, f, f.s, "counted like the game menu: multi-disc games count once", C["t3"], (x + w, r.bottom - 20),
                "topright")
        return [("UP DN", "scroll"), ("X", "count again"), ("L1 R1", "tabs")]


# =========================================================================== PROCS

class Procs(Screen):
    """What's draining my battery?"""
    name = "PROCS"

    def __init__(self):
        self.sel = 0

    def enter(self, app):
        app.sm.reset_procs()
        app.sm.want_procs = True

    def leave(self, app):
        app.sm.want_procs = False

    def handle(self, app, a):
        n = len(app.sm.procs)
        if a in ("up", "down") and n:
            self.sel = (self.sel + (1 if a == "down" else -1)) % n
            return True
        return False

    def draw(self, app, s):
        f, sm = app.fonts, app.sm
        with sm.lock:
            procs, n = list(sm.procs), sm.nprocs
        r = th.tile(s, f, (6, TOP + 6, 628, BOTTOM - TOP - 12), "TOP USAGE RIGHT NOW")
        x, w = r.x + 10, r.w - 20
        th.label(s, f, "cpu", C["t2"], (x + 380, r.y + 7))
        th.label(s, f, "memory", C["t2"], (x + w, r.y + 7), "topright")
        rows = 13
        sel = min(self.sel, max(0, len(procs) - 1))
        start = th.window_start(sel, len(procs), rows)
        for i, p in enumerate(procs[start:start + rows]):
            y = r.y + 24 + i * 26
            th.row_bg(s, (r.x + 4, y, r.w - 8, 26), start + i == sel, divider=False)
            name = ("Field Terminal (this app)" if p["me"] else p["name"]) + (f" ×{p['count']}" if p["count"] > 1 else "")
            th.text(s, f, f.m, name, C["t1"], (x + 6, y + 3), maxw=300)
            th.thin_bar(s, (x + 330, y + 10, 110, 6), min(1.0, p["cpu"] / 25))
            th.text(s, f, f.m, f"{p['cpu']:.1f}%", C["amb"], (x + 500, y + 3), "topright")
            th.text(s, f, f.m, f"{p['rss'] / 1048576:.0f} MB", C["t1"], (x + w - 4, y + 3), "topright")
        if not procs:
            th.text(s, f, f.m, "reading…", C["t3"], (x, r.y + 30))
        th.text(s, f, f.s, "For information only: nothing can be closed from here.", C["t3"], (x, r.bottom - 22))
        th.text(s, f, f.m, f"{n} programs", C["t2"], (x + w, r.bottom - 24), "topright")
        return [("UP DN", "select"), ("L1 R1", "tabs")]


# =========================================================================== LOG

class Log(Screen):
    """What happened?"""
    name = "LOG"

    def __init__(self):
        self.scroll = 0

    def handle(self, app, a):
        n = len(app.log.items)
        if a == "up":
            self.scroll = min(max(0, n - 1), self.scroll + 1)
            return True
        if a == "down":
            self.scroll = max(0, self.scroll - 1)
            return True
        return False

    def draw(self, app, s):
        f = app.fonts
        items = app.log.snapshot()
        r = th.tile(s, f, (6, TOP + 6, 628, BOTTOM - TOP - 12), "EVENT LOG",
                    f"{len(items)} events · logs/{os.path.basename(app.log.path)}")
        x, w = r.x + 10, r.w - 20
        rows = (r.h - 30) // 18
        end = len(items) - self.scroll
        for i, (t, level, msg) in enumerate(items[max(0, end - rows):end]):
            y = r.y + 22 + i * 18
            col = {"warn": C["amb"], "error": C["red"]}.get(level, C["t1"])
            th.text(s, f, f.s, time.strftime("%H:%M:%S", time.localtime(t)), C["t3"], (x, y))
            th.text(s, f, f.s, {"warn": "WARN", "error": "ERROR"}.get(level, "info"), col, (x + 64, y))
            th.text(s, f, f.s, msg, C["t1"] if level == "info" else col, (x + 112, y), maxw=w - 112)
        if self.scroll:
            th.text(s, f, f.s, f"{self.scroll} newer below", C["amb"], (x + w, r.bottom - 20), "topright")
        return [("UP DN", "scroll"), ("L1 R1", "tabs")]
