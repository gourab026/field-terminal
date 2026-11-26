"""Field Terminal - NET tab: everything Net Analyzer did, as five pages (left / right):
Overview, Wi-Fi, Devices (with port scan), Traffic (live capture, .pcap), Tools (ping, traceroute, DNS).

Heavy work only runs while you are here: packet capture starts when NET opens and stops when
you leave; the Wi-Fi scan and the device sweep start the first time their page opens.
"""
import os
import time

import pygame

import netcore as nc
import netmon
import theme as th
from screens_sys import Screen
from theme import C, TOP, BOTTOM

PAGES = ["OVERVIEW", "WI-FI", "DEVICES", "TRAFFIC", "TOOLS"]
Y0 = TOP + 26          # content starts under the page strip
SECURITY_NOTES = {
    "Open": "No encryption. Anyone nearby can read the traffic.",
    "WEP": "WEP has been broken since 2001 and can be cracked in minutes.",
    "WPA": "Original WPA (TKIP) is deprecated. Use WPA2 or WPA3.",
    "WPA2": "WPA2 is safe with a long, unique password.",
    "WPA2/WPA3": "Mixed mode. WPA3 devices get the stronger SAE handshake.",
    "WPA3": "WPA3 (SAE) resists offline password guessing.",
    "WPA2-Enterprise": "Enterprise: every user signs in separately (802.1X).",
}


def sec_color(sec):
    return C["red"] if sec in ("Open", "WEP") else C["amb"] if sec == "WPA" else C["t1"]


class Net(Screen):
    name = "NET"

    def __init__(self):
        self.page = 0
        self.dev_view = "list"
        self.side = "hosts"
        self.sel = {"wifi": 0, "dev": 0, "tools": 0}
        self.started = {"wifi": False, "dev": False}
        self.paused = False                  # capture paused with A: stays paused when NET reopens

    # ------------------------------------------------------------------ lifecycle
    def enter(self, app):
        app.mon.active = True
        app.cap.iface = app.mon.iface or app.cap.iface        # the network may have come up after launch
        app.cap.my_ip = app.mon.info.get("ip") or app.cap.my_ip
        app.cap.start()
        if not self.paused:
            app.cap.capturing.set()
        self._page_jobs(app)

    def leave(self, app):
        app.mon.active = False
        app.cap.stop()                       # a port scan keeps running; B stops it

    def _page_jobs(self, app):
        name = PAGES[self.page]
        if name == "WI-FI" and not self.started["wifi"]:
            self.started["wifi"] = True
            app.wifi.start()
        if name == "DEVICES" and not self.started["dev"]:
            self.started["dev"] = True
            app.disc.start()

    def tick(self, app):
        """Re-scan Wi-Fi every 30 s while its page is on screen."""
        if PAGES[self.page] == "WI-FI" and not app.wifi.busy and app.wifi.when and time.time() - app.wifi.when > 30:
            app.wifi.start()

    def busy(self, app):
        return app.wifi.busy or app.disc.busy or app.scan.busy or app.tools.busy

    # ------------------------------------------------------------------ input
    def handle(self, app, a):
        name = PAGES[self.page]
        if a in ("left", "right") and not (name == "DEVICES" and self.dev_view == "scan"):
            self.page = (self.page + (1 if a == "right" else -1)) % len(PAGES)
            self._page_jobs(app)
            return True
        if name == "WI-FI":
            self._move("wifi", a, len(app.wifi.nets))
            if a == "x" and app.wifi.start():
                app.notify("Scanning Wi-Fi")
        elif name == "DEVICES":
            if self.dev_view == "scan":
                if a == "b":
                    app.scan.stop_ev.set()
                    self.dev_view = "list"
                elif a in ("left", "right"):     # back to the list; the scan carries on
                    self.dev_view = "list"
                elif a == "x" and app.scan.target and app.scan.start(app.scan.target):
                    app.notify("Port scan restarted")
                return True
            self._move("dev", a, len(app.disc.devices))
            if a == "x" and app.disc.start():
                app.notify("Scanning the network")
            elif a == "a":
                dev = self.selected_device(app)
                if dev:
                    self.dev_view = "scan"
                    if not (app.scan.target and app.scan.target["ip"] == dev["ip"] and app.scan.results):
                        app.scan.start(dev)
        elif name == "TRAFFIC":
            if a == "a":
                if app.cap.capturing.is_set():
                    self.paused = True
                    app.cap.capturing.clear()
                    app.log.add("Capture paused")
                    app.notify("Capture paused", "warn")
                else:
                    self.paused = False
                    app.cap.capturing.set()
                    app.log.add("Capture resumed")
                    app.notify("Capture resumed", "ok")
            elif a == "y":
                app.save_pcap()
            elif a == "x":
                self.side = "dns" if self.side == "hosts" else "hosts"
            elif a == "b":
                app.cap.clear()
                app.notify("Capture statistics cleared", "warn")
        elif name == "TOOLS":
            items = self.tool_items(app)
            self._move("tools", a, len(items))
            if a == "b" and app.tools.busy:
                app.tools.stop_ev.set()
                app.notify("Stopping", "warn")
            elif a == "a":
                label, _sub, (kind, target) = items[self.sel["tools"]]
                if kind == "remap":
                    app.recalibrate()
                elif kind == "quit":
                    app.running = False
                elif target is None and kind == "ping":
                    app.notify("No device selected", "warn", "Scan in Devices first")
                elif app.tools.busy:
                    app.notify("A tool is already running", "warn", "B stops it")
                else:
                    app.tools.start(kind, target, label)
        return True

    def _move(self, key, a, count):
        if count <= 0:
            self.sel[key] = 0
        elif a == "up":
            self.sel[key] = (self.sel[key] - 1) % count
        elif a == "down":
            self.sel[key] = (self.sel[key] + 1) % count

    def selected_device(self, app):
        devs = app.disc.devices
        return devs[min(self.sel["dev"], len(devs) - 1)] if devs else None

    def tool_items(self, app):
        gw = app.mon.gateway or "?"
        dev = self.selected_device(app)
        return [
            ("Ping gateway", gw, ("ping", gw)),
            ("Ping 1.1.1.1", "Cloudflare, on the internet", ("ping", "1.1.1.1")),
            ("Ping 8.8.8.8", "Google, on the internet", ("ping", "8.8.8.8")),
            ("Ping selected device", dev["ip"] if dev else "select one in Devices", ("ping", dev["ip"] if dev else None)),
            ("Traceroute 1.1.1.1", "every router on the way", ("trace", "1.1.1.1")),
            ("Traceroute 8.8.8.8", "every router on the way", ("trace", "8.8.8.8")),
            ("DNS lookups", "five sites, timed", ("dns", None)),
            ("Set up buttons", "if A and B feel swapped", ("remap", None)),
            ("Quit", "or MENU + START", ("quit", None)),
        ]

    # ------------------------------------------------------------------ drawing
    def draw(self, app, s):
        f = app.fonts
        x = 10
        busy = {"WI-FI": app.wifi.busy, "DEVICES": app.disc.busy or app.scan.busy, "TOOLS": app.tools.busy}
        th.arrow(s, x, TOP + 9, "left", C["t3"])
        x += 16
        for i, p in enumerate(PAGES):
            r = th.label(s, f, p + (" ·" if busy.get(p) else ""), C["bg"] if i == self.page else C["t3"], (x + 6, TOP + 9))
            if i == self.page:
                pygame.draw.rect(s, C["t2"], r.inflate(12, 8))
                th.label(s, f, p + (" ·" if busy.get(p) else ""), C["bg"], (x + 6, TOP + 9))
            x = r.right + 20
        th.arrow(s, x - 6, TOP + 9, "right", C["t3"])
        pygame.draw.line(s, C["line"], (0, TOP + 23), (th.W, TOP + 23))
        page = PAGES[self.page]
        fn = {"OVERVIEW": self.draw_overview, "WI-FI": self.draw_wifi, "DEVICES": self.draw_devices,
              "TRAFFIC": self.draw_traffic, "TOOLS": self.draw_tools}[page]
        hints = fn(app, s)
        if not (page == "DEVICES" and self.dev_view == "scan"):
            hints = [("LEFT RIGHT", "pages")] + hints
        return hints

    # -- overview
    def status_cells(self, app):
        mon, link = app.mon, app.mon.link
        cells = []
        if not mon.info.get("wireless"):
            cells.append(("NETWORK", "ok", "wired"))
        elif link.get("signal") is None:
            cells.append(("WI-FI", "error", "disconnected"))
        else:
            q = nc.signal_quality(link["signal"])
            word = "excellent" if q >= 80 else "good" if q >= 60 else "fair" if q >= 35 else "weak"
            cells.append(("WI-FI", "ok" if q >= 60 else "warn" if q >= 35 else "error", word))
        for lab, series, slow in (("GATEWAY", mon.gw, 30), ("INTERNET", mon.inet, 100)):
            last = series.last()
            if series.streak >= 3:
                cells.append((lab, "error", "no reply"))
            elif last is None:
                cells.append((lab, "off", "checking"))
            elif last < 0:
                cells.append((lab, "warn", "packet lost"))
            else:
                cells.append((lab, "ok" if last < slow else "warn", f"{last:.0f} ms"))
        if mon.dns_ok is None:
            cells.append(("DNS", "off", "checking"))
        elif not mon.dns_ok:
            cells.append(("DNS", "error", "failing"))
        else:
            cells.append(("DNS", "ok" if mon.dns_ms < 300 else "warn", f"{mon.dns_ms:.0f} ms"))
        return cells

    def draw_overview(self, app, s):
        f, mon, capt = app.fonts, app.mon, app.cap
        info, link = mon.info, mon.link
        with mon.lock:
            rx, tx = list(mon.rx_hist), list(mon.tx_hist)
            sig_hist = list(mon.sig_hist)
            gw_raw = list(mon.gw.hist)
            gw_hist = [v if v is None or v >= 0 else None for v in gw_raw]
            inet_hist = [v if v is None or v >= 0 else None for v in mon.inet.hist]
            lost = [i for i, v in enumerate(gw_raw) if v is not None and v < 0]
            rx_rate, tx_rate = mon.rx_rate, mon.tx_rate

        r = th.tile(s, f, (6, Y0, 628, 44))
        cells = self.status_cells(app)
        cw = r.w / len(cells)
        for i, (lab, kind, value) in enumerate(cells):
            x = round(r.x + i * cw)
            if i:
                pygame.draw.line(s, C["line"], (x, r.y + 8), (x, r.bottom - 8))
            th.label(s, f, lab, C["t2"], (x + 10, r.y + 7))
            th.status_mark(s, x + 10, r.y + 26, kind)
            th.text(s, f, f.m, value, th.STATUS[kind], (x + 22, r.y + 19), maxw=round(cw) - 28)

        y = Y0 + 50
        r = th.tile(s, f, (6, y, 310, 122), "CONNECTION", "up " + th.fmt_dur(time.time() - mon.started))
        wireless = info.get("wireless")
        conn = mon.station.get("connected")
        rows = [
            ("network", (link.get("ssid") or "not connected") if wireless else f"{mon.iface}, wired"),
            ("ip", f"{info.get('ip') or '-'}/{info.get('prefix', '-')}"),
            ("gateway", mon.gateway or "-"),
            ("dns", ", ".join(mon.dns[:2]) or "-"),
            ("mac", (info.get("mac") or "-") + (" random" if nc.is_random_mac(info.get("mac") or "") else "")),
            ("radio", f"ch {link.get('channel', '?')} · {link.get('band', '?')} · {link.get('txrate', 0):.0f} Mbit/s"
             if wireless and link else "-"),
            ("connected", th.fmt_dur(conn) if conn is not None else "-"),
        ]
        for i, (k, v) in enumerate(rows):
            yy = r.y + 20 + i * 14
            th.text(s, f, f.s, k, C["t3"], (r.x + 10, yy))
            th.text(s, f, f.s, v, C["t1"], (r.x + 78, yy), maxw=r.w - 88)

        r = th.tile(s, f, (322, y, 312, 122), "WI-FI SIGNAL", (link.get("bssid") or "") if wireless else "")
        sig = link.get("signal")
        if not wireless or sig is None:
            th.text(s, f, f.m, "wired connection" if not wireless else "not connected", C["t3"], r.center, "center")
        else:
            q = nc.signal_quality(sig)
            col = C["t1"] if q >= 60 else C["amb"] if q >= 35 else C["red"]
            m = th.text(s, f, f.xl, str(sig), col, (r.x + 10, r.y + 18), glow=True)
            th.text(s, f, f.m, "dBm", C["t2"], (m.right + 6, r.y + 28))
            word = "excellent" if q >= 80 else "good" if q >= 60 else "fair" if q >= 35 else "weak"
            th.text(s, f, f.m, f"{word}, {q}%", col, (r.right - 10, r.y + 20), "topright")
            extra = []
            if mon.retry_pct is not None:
                extra.append(f"retries {mon.retry_pct:.1f}%")
            if "beacon_loss" in mon.station:
                extra.append(f"beacon loss {mon.station['beacon_loss']}")
            th.text(s, f, f.s, ", ".join(extra), C["t3"], (r.right - 10, r.y + 40), "topright")
            th.line_graph(s, (r.x + 10, r.y + 62, r.w - 50, 50), [(sig_hist, col)], vmin=-95, vmax=-25)
            th.text(s, f, f.s, "-25", C["t3"], (r.right - 36, r.y + 58))
            th.text(s, f, f.s, "-95", C["t3"], (r.right - 36, r.y + 100))

        y += 128
        er = mon.errors
        errs, drops = er["rx_errs"] + er["tx_errs"], er["rx_drop"] + er["tx_drop"]
        r = th.tile(s, f, (6, y, 628, 92), "THROUGHPUT",
                    f"session dn {th.fmt_bytes(mon.rx_total)} up {th.fmt_bytes(mon.tx_total)} · errors {errs} drops {drops}",
                    C["amb"] if errs else None)
        th.arrow(s, r.x + 10, r.y + 24, "down", C["t1"])
        a = th.text(s, f, f.m, th.fmt_rate(rx_rate), C["t1"], (r.x + 22, r.y + 18))
        th.arrow(s, a.right + 16, r.y + 24, "up", C["t3"])
        th.text(s, f, f.m, th.fmt_rate(tx_rate), C["t2"], (a.right + 28, r.y + 18))
        top = th.line_graph(s, (r.x + 10, r.y + 40, r.w - 70, 44), [(tx, C["t3"]), (rx, C["t1"])], vmin=0)
        th.text(s, f, f.s, th.fmt_rate(top).replace(".0 ", " "), C["t3"], (r.right - 8, r.y + 38), "topright")

        y += 98
        r = th.tile(s, f, (6, y, 412, BOTTOM - 6 - y), "LATENCY", f"gateway {mon.gateway or '-'} · internet {netmon.INTERNET_HOST}")
        gl, il = mon.gw.last(), mon.inet.last()
        lx = r.x + 10
        pygame.draw.rect(s, C["t1"], (lx, r.y + 24, 8, 8))
        a = th.text(s, f, f.m, "gateway " + ("lost" if gl is not None and gl < 0 else (f"{gl:.1f} ms" if gl else "-")),
                    C["t1"], (lx + 12, r.y + 18))
        pygame.draw.rect(s, C["amb"], (a.right + 16, r.y + 24, 8, 8))
        th.text(s, f, f.m, "internet " + ("lost" if il is not None and il < 0 else (f"{il:.1f} ms" if il else "-")),
                C["amb"], (a.right + 28, r.y + 18))
        top = th.line_graph(s, (lx, r.y + 40, r.w - 60, r.h - 66), [(inet_hist, C["amb"]), (gw_hist, C["t1"])], vmin=0,
                            markers=lost)
        th.text(s, f, f.s, f"{top:.0f} ms", C["t3"], (r.right - 8, r.y + 38), "topright")
        for i, series in enumerate((mon.gw, mon.inet)):
            st = series.stats()
            txt = f"avg {st[1]:.1f} max {st[2]:.0f} loss {st[3]:.1f}%" if st else "waiting for replies"
            th.text(s, f, f.s, txt, C["t3"], (lx + i * 200, r.bottom - 20), maxw=190)

        state = ("unavailable", C["red"]) if capt.error else (("capturing", C["t1"]) if capt.capturing.is_set()
                                                               else ("paused", C["amb"]))
        r = th.tile(s, f, (424, y, 210, BOTTOM - 6 - y), "PACKETS", state[0], state[1])
        if capt.error:
            for i, line in enumerate(th.wrap(f.s, capt.error, r.w - 20)[:3]):
                th.text(s, f, f.s, line, C["t3"], (r.x + 10, r.y + 24 + i * 16))
        else:
            m = th.text(s, f, f.xl, str(capt.pps), C["t1"], (r.x + 10, r.y + 14), glow=True)
            th.text(s, f, f.m, "packets/s", C["t2"], (m.right + 6, r.y + 24))
            with capt.lock:
                top3 = capt.proto_pkts.most_common(3)
                total = max(1, sum(capt.proto_pkts.values()))
            for i, (p, n) in enumerate(top3):
                yy = r.y + 46 + i * 15
                pygame.draw.rect(s, th.proto_color(p), (r.x + 10, yy + 5, 7, 7))
                th.text(s, f, f.s, p, C["t1"], (r.x + 22, yy))
                th.text(s, f, f.s, f"{100 * n / total:.0f}%", C["t2"], (r.right - 10, yy), "topright")
        return [("L1 R1", "tabs")]

    # -- Wi-Fi
    def draw_wifi(self, app, s):
        f, nets, w = app.fonts, list(app.wifi.nets), app.wifi
        aside = "scanning" if w.busy else (f"{len(nets)} found, {th.fmt_ago(w.when)}" if w.when else "")
        r = th.tile(s, f, (6, Y0, 392, BOTTOM - 6 - Y0), "NEARBY NETWORKS", aside, C["amb"] if w.busy else None)
        for x, lab in ((10, "dBm"), (80, "network"), (250, "band"), (290, "ch"), (322, "security")):
            th.label(s, f, lab, C["t3"], (r.x + x, r.y + 22))
        if not nets:
            msg = w.error or ("looking for networks…" if w.busy else "X scans")
            th.text(s, f, f.m, msg, C["t3"], (r.centerx, r.y + 120), "center", maxw=r.w - 30)
        rows = (r.h - 40) // 22
        sel = min(self.sel["wifi"], max(0, len(nets) - 1))
        start = th.window_start(sel, len(nets), rows)
        for i, n in enumerate(nets[start:start + rows]):
            y = r.y + 34 + i * 22
            th.row_bg(s, (r.x + 2, y, r.w - 4, 22), start + i == sel)
            q = nc.signal_quality(n["signal"])
            th.text(s, f, f.s, f"{n['signal']:.0f}" if n["signal"] is not None else "?", C["t2"], (r.x + 10, y + 3))
            th.signal_bars(s, r.x + 40, y + 16, q, sec_color("WPA2") if q >= 60 else C["amb"] if q >= 35 else C["red"],
                           small=True)
            name = n["ssid"] or "hidden network"
            col = C["amb"] if n["connected"] else (C["t1"] if n["ssid"] else C["t3"])
            th.text(s, f, f.m, name, col, (r.x + 80, y + 1), maxw=164)
            band = {"2.4 GHz": "2.4", "5 GHz": "5", "6 GHz": "6"}.get(n["band"], "?")
            th.text(s, f, f.s, band, C["t2"], (r.x + 250, y + 3))
            th.text(s, f, f.s, str(n["channel"]), C["t2"], (r.x + 290, y + 3))
            sec = n["security"]
            short = sec.replace("-Enterprise", " Ent").replace("WPA2/WPA3", "WPA2/3")
            th.text(s, f, f.s, short, sec_color(sec), (r.x + 322, y + 3), maxw=64)

        d = th.tile(s, f, (404, Y0, 230, 184), "DETAILS")
        if nets:
            n = nets[sel]
            q = nc.signal_quality(n["signal"])
            th.text(s, f, f.m, n["ssid"] or "hidden network", C["t1"], (d.x + 10, d.y + 18), maxw=d.w - 20, glow=True)
            sec = n["security"]
            rows_ = [("bssid", n["bssid"], C["t1"]),
                     ("signal", f"{n['signal']:.0f} dBm, {q}%" if n["signal"] is not None else "?", C["t1"]),
                     ("channel", f"{n['channel']}, {n['band']}", C["t1"]),
                     ("freq", f"{n['freq']:.0f} MHz" if n["freq"] else "?", C["t1"]),
                     ("security", sec, sec_color(sec)),
                     ("status", "connected" if n["connected"] else "in range", C["amb"] if n["connected"] else C["t2"])]
            for i, (k, v, col) in enumerate(rows_):
                y = d.y + 42 + i * 15
                th.text(s, f, f.s, k, C["t3"], (d.x + 10, y))
                th.text(s, f, f.s, v, col, (d.x + 74, y), maxw=d.w - 84)
            for i, line in enumerate(th.wrap(f.s, SECURITY_NOTES.get(sec, ""), d.w - 20)[:3]):
                th.text(s, f, f.s, line, C["t2"], (d.x + 10, d.y + 134 + i * 15))

        c = th.tile(s, f, (404, Y0 + 190, 230, BOTTOM - 6 - Y0 - 190), "2.4 GHZ CHANNELS", "load")
        load = [0.0] * 14
        for n in nets:
            ch = n["channel"]
            if n["band"] == "2.4 GHz" and 1 <= ch <= 13:
                wgt = max(0.05, nc.signal_quality(n["signal"]) / 100)
                for k in range(max(1, ch - 2), min(13, ch + 2) + 1):
                    load[k] += wgt * (1.0 if k == ch else 0.5)
        mx = max(load) or 1
        mine = app.mon.link.get("channel")
        bw = (c.w - 20) / 13
        base, bh = c.y + 22 + 80, 80
        for ch in range(1, 14):
            x = c.x + 10 + (ch - 1) * bw
            hgt = round(bh * load[ch] / mx) if load[ch] else 0
            col = C["amb"] if ch == mine else (C["t1"] if ch in (1, 6, 11) else C["t3"])
            pygame.draw.rect(s, C["trk"], (round(x) + 2, base - bh, round(bw) - 4, bh))
            if hgt:
                pygame.draw.rect(s, col, (round(x) + 2, base - hgt, round(bw) - 4, hgt))
            th.text(s, f, f.s, str(ch), C["amb"] if ch == mine else C["t3"], (x + bw / 2, base + 1), "midtop")
        best = min((1, 6, 11), key=lambda ch: load[ch])
        n5 = sum(1 for n in nets if n["band"] != "2.4 GHz")
        th.text(s, f, f.s, f"least busy of 1, 6, 11: {best}", C["t1"], (c.x + 10, c.bottom - 36))
        th.text(s, f, f.s, f"amber is yours · 5/6 GHz: {n5}", C["t3"], (c.x + 10, c.bottom - 20), maxw=c.w - 20)
        return [("UP DN", "select"), ("X", "rescan")]

    # -- Devices
    def draw_devices(self, app, s):
        if self.dev_view == "scan":
            return self.draw_portscan(app, s)
        f, devs, dj = app.fonts, list(app.disc.devices), app.disc
        if dj.busy:
            done, total = dj.prog
            aside = "resolving names" if dj.stage == "names" else f"{dj.method or 'sweeping'} {done}/{total}"
        else:
            aside = f"{len(devs)} found · {dj.method} · {th.fmt_ago(dj.when)}" if dj.when else ""
        r = th.tile(s, f, (6, Y0, 628, BOTTOM - 6 - Y0), f"DEVICES ON {dj.cidr or 'YOUR NETWORK'}", aside,
                    C["amb"] if dj.busy else None)
        if dj.busy:
            done, total = dj.prog
            th.thin_bar(s, (r.x + 1, r.y + 18, r.w - 2, 2), done / total if total else 0.05, C["amb"])
        for x, lab in ((10, "ip address"), (122, "name"), (262, "mac address"), (420, "rtt"), (462, "ports"),
                       (544, "notes")):
            th.label(s, f, lab, C["t3"], (r.x + x, r.y + 24))
        if not devs:
            msg = dj.error or ("scanning your network…" if dj.busy else "X scans your network")
            th.text(s, f, f.m, msg, C["t3"], (r.centerx, r.y + 130), "center")
        rows = (r.h - 44) // 22
        sel = min(self.sel["dev"], max(0, len(devs) - 1))
        start = th.window_start(sel, len(devs), rows)
        for i, d in enumerate(devs[start:start + rows]):
            y = r.y + 36 + i * 22
            th.row_bg(s, (r.x + 2, y, r.w - 4, 22), start + i == sel)
            me = "This device" in d["notes"]
            th.text(s, f, f.s, d["ip"], C["amb"] if me else C["t1"], (r.x + 10, y + 3))
            th.text(s, f, f.m, d["name"] or "unknown", C["t1"] if d["name"] else C["t3"], (r.x + 122, y + 1), maxw=134)
            th.text(s, f, f.s, d["mac"], C["t2"], (r.x + 262, y + 3))
            rtt = d.get("rtt")
            th.text(s, f, f.s, "-" if rtt is None or me else f"{rtt:.0f}ms", C["t2"], (r.x + 452, y + 3), "topright")
            hist = app.scan.history.get(d["ip"])
            ports = "-" if hist is None else (" ".join(str(p) for p, *_ in hist) or "none")
            th.text(s, f, f.s, ports, C["t1"] if hist else C["t3"], (r.x + 462, y + 3), maxw=78)
            th.text(s, f, f.s, ", ".join(d["notes"]).lower(), C["amb"] if me else C["t2"], (r.x + 544, y + 3),
                    maxw=r.w - 552)
        return [("UP DN", "select"), ("A", "port scan"), ("X", "rescan")]

    def draw_portscan(self, app, s):
        f, sc = app.fonts, app.scan
        t = sc.target or {}
        r = th.tile(s, f, (6, Y0, 628, BOTTOM - 6 - Y0), f"PORT SCAN · {t.get('ip', '')}", t.get("fqdn") or t.get("name") or "")
        th.text(s, f, f.s, f"mac {t.get('mac', '?')} · {len(nc.TOP_PORTS)} common TCP ports · "
                f"{', '.join(t.get('notes', [])).lower()}", C["t3"], (r.x + 10, r.y + 20), maxw=r.w - 20)
        done, total = sc.prog
        frac = 1.0 if sc.stage == "done" else (done / total if total else 0.02)
        th.seg_bar(s, (r.x + 10, r.y + 40, r.w - 20, 6), frac, C["red"] if sc.error else (C["t1"] if sc.stage == "done" else C["amb"]))
        if sc.error:
            kind, msg = "error", f"error: {sc.error}"
        elif sc.stage == "done":
            kind, msg = "ok", (f"finished in {sc.duration:.1f} s: {len(sc.results)} open, "
                               f"{len(nc.TOP_PORTS) - len(sc.results)} closed or filtered")
        elif sc.stage == "stopped":
            kind, msg = "warn", f"stopped after {done} of {total} ports: X scans again"
        elif sc.stage == "banners":
            kind, msg = "info", f"asking open ports to identify themselves ({len(sc.results)} so far)"
        else:
            kind, msg = "info", f"scanning {done} of {total} ports"
        th.status_mark(s, r.x + 10, r.y + 58, kind)
        th.text(s, f, f.m, msg, th.STATUS[kind], (r.x + 22, r.y + 51), maxw=r.w - 32)
        for x, lab in ((10, "port"), (70, "service"), (190, "time"), (250, "response")):
            th.label(s, f, lab, C["t3"], (r.x + x, r.y + 78))
        for i, (port, svc, ms, banner) in enumerate(sc.results[:12]):
            y = r.y + 92 + i * 22
            th.row_bg(s, (r.x + 2, y, r.w - 4, 22))
            th.text(s, f, f.m, str(port), C["t1"], (r.x + 10, y + 1))
            th.text(s, f, f.m, svc, C["t1"], (r.x + 70, y + 1), maxw=112)
            th.text(s, f, f.s, f"{ms:.0f} ms", C["t2"], (r.x + 190, y + 3))
            th.text(s, f, f.s, banner or ("encrypted (TLS)" if port in sc.NO_BANNER else "-"),
                    C["t1"] if banner else C["t3"], (r.x + 250, y + 3), maxw=r.w - 260)
        if sc.stage == "done" and not sc.results and not sc.error:
            th.text(s, f, f.m, "No open ports: the device ignores or blocks connections.", C["t3"],
                    (r.centerx, r.y + 150), "midtop")
        th.text(s, f, f.s, "Only scan networks you own or have permission to test.", C["t3"], (r.x + 10, r.bottom - 20))
        return [("B", "back"), ("X", "scan again"), ("L1 R1", "tabs")]

    # -- Traffic
    def draw_traffic(self, app, s):
        f, c = app.fonts, app.cap
        with c.lock:
            pps = list(c.pps_hist)
            protos = c.proto_pkts.most_common(5)
            pbytes = dict(c.proto_bytes)
            total = max(1, sum(c.proto_pkts.values()))
            hosts = c.talkers.most_common(5)
            names = dict(c.names)
            dns = list(c.dns)
            pkts = list(c.packets)[-7:]
            nraw = len(c.raw)
        r = th.tile(s, f, (6, Y0, 412, 92), "PACKETS PER SECOND", f"{c.pps}/s · {th.fmt_rate(c.bps)}")
        top = th.line_graph(s, (r.x + 10, r.y + 22, r.w - 56, r.h - 30), [(pps, C["t1"])], vmin=0)
        th.text(s, f, f.s, f"{top:.0f}", C["t3"], (r.right - 8, r.y + 20), "topright")

        state = ("unavailable", "error") if c.error else (("capturing", "ok") if c.capturing.is_set() else ("paused", "warn"))
        r = th.tile(s, f, (424, Y0, 210, 92), "CAPTURE")
        th.status_mark(s, r.x + 10, r.y + 26, state[1])
        th.text(s, f, f.m, state[0], th.STATUS[state[1]], (r.x + 22, r.y + 19), glow=True)
        if c.error:
            th.text(s, f, f.s, c.error, C["t3"], (r.x + 10, r.y + 42), maxw=r.w - 20)
        else:
            for i, (k, v) in enumerate((("packets", f"{c.total_pkts:,}"), ("data", th.fmt_bytes(c.total_bytes)),
                                        ("for .pcap", f"{nraw:,} kept"))):
                th.kv(s, f, f.s, r.x + 10, r.y + 40 + i * 15, r.w - 20, k, v, C["t3"], C["t1"])

        y = Y0 + 98
        r = th.tile(s, f, (6, y, 310, 126), "PROTOCOLS", "green safe · red plain")
        for x, lab, anc in ((10, "protocol", "topleft"), (190, "packets", "topright"), (250, "data", "topright"),
                            (300, "share", "topright")):
            th.label(s, f, lab, C["t3"], (r.x + x, r.y + 22), anc)
        for i, (p, n) in enumerate(protos):
            yy = r.y + 36 + i * 17
            pygame.draw.rect(s, th.proto_color(p), (r.x + 10, yy + 5, 7, 7))
            th.text(s, f, f.s, p, C["t1"], (r.x + 22, yy))
            th.text(s, f, f.s, f"{n:,}", C["t2"], (r.x + 190, yy), "topright")
            th.text(s, f, f.s, th.fmt_bytes(pbytes.get(p, 0)), C["t2"], (r.x + 250, yy), "topright")
            th.text(s, f, f.s, f"{100 * n / total:.0f}%", C["t1"], (r.x + 300, yy), "topright")
        if not protos:
            th.text(s, f, f.m, "waiting for packets…", C["t3"], (r.centerx, r.y + 70), "center")

        title = "TOP HOSTS" if self.side == "hosts" else "DNS LOOKUPS"
        r = th.tile(s, f, (322, y, 312, 126), title, "X switches")
        if self.side == "hosts":
            th.label(s, f, "host", C["t3"], (r.x + 10, r.y + 22))
            th.label(s, f, "data", C["t3"], (r.right - 10, r.y + 22), "topright")
            for i, (ip, b) in enumerate(hosts):
                yy = r.y + 36 + i * 17
                name = names.get(ip)
                nr = th.text(s, f, f.s, name or ip, C["t1"], (r.x + 10, yy), maxw=180)
                if name:
                    th.text(s, f, f.s, ip, C["t3"], (nr.right + 6, yy), maxw=max(10, r.right - 70 - nr.right))
                th.text(s, f, f.s, th.fmt_bytes(b), C["t2"], (r.right - 10, yy), "topright")
        else:
            th.label(s, f, "name looked up", C["t3"], (r.x + 10, r.y + 22))
            th.label(s, f, "when", C["t3"], (r.right - 10, r.y + 22), "topright")
            seen, uniq = set(), []
            for t, name in reversed(dns):
                if name not in seen:
                    seen.add(name)
                    uniq.append((t, name))
            for i, (t, name) in enumerate(uniq[:5]):
                yy = r.y + 36 + i * 17
                th.text(s, f, f.s, name, C["amb"], (r.x + 10, yy), maxw=r.w - 90)
                th.text(s, f, f.s, th.fmt_ago(t), C["t2"], (r.right - 10, yy), "topright")
            if not uniq:
                th.text(s, f, f.m, "no DNS lookups seen yet", C["t3"], (r.centerx, r.y + 70), "center")

        y += 132
        r = th.tile(s, f, (6, y, 628, BOTTOM - 6 - y), "LIVE PACKETS", "newest first")
        for x, lab in ((10, "time"), (78, "proto"), (138, "source"), (268, "destination"), (398, "info")):
            th.label(s, f, lab, C["t3"], (r.x + x, r.y + 22))
        nrow = (r.h - 40) // 16
        for i, p in enumerate(list(reversed(pkts))[:nrow]):
            yy = r.y + 36 + i * 16
            th.text(s, f, f.s, time.strftime("%H:%M:%S", time.localtime(p.t)), C["t3"], (r.x + 10, yy))
            pygame.draw.rect(s, th.proto_color(p.proto), (r.x + 78, yy + 5, 6, 6))
            th.text(s, f, f.s, p.proto, C["t1"], (r.x + 88, yy))
            th.text(s, f, f.s, p.src, C["t2"] if p.out else C["t1"], (r.x + 138, yy), maxw=124)
            th.text(s, f, f.s, p.dst, C["t1"] if p.out else C["t2"], (r.x + 268, yy), maxw=124)
            th.text(s, f, f.s, p.info, C["t2"], (r.x + 398, yy), maxw=r.w - 408)
        if not pkts:
            th.text(s, f, f.m, c.error or "waiting for packets…", C["t3"], (r.centerx, r.y + 60), "center")
        return [("A", "pause"), ("Y", "save .pcap"), ("X", "hosts/dns"), ("B", "clear")]

    # -- Tools
    def draw_tools(self, app, s):
        f, tl = app.fonts, app.tools
        items = self.tool_items(app)
        r = th.tile(s, f, (6, Y0, 232, BOTTOM - 6 - Y0), "TOOLS")
        step = (r.h - 24) // len(items)
        for i, (lab, sub, _act) in enumerate(items):
            y = r.y + 20 + i * step
            th.row_bg(s, (r.x + 2, y, r.w - 4, step), i == self.sel["tools"])
            th.text(s, f, f.m, lab, C["t1"], (r.x + 12, y + 2), maxw=r.w - 20)
            th.text(s, f, f.s, sub, C["t3"], (r.x + 12, y + 20), maxw=r.w - 20)

        aside, acol = ("running", C["amb"]) if tl.busy else (("finished", None) if tl.lines else ("select a tool, press A", None))
        o = th.tile(s, f, (244, Y0, 390, BOTTOM - 6 - Y0), (tl.title or "OUTPUT").upper(), aside, acol)
        y0 = o.y + 20
        if tl.kind in ("ping", "dns") and tl.rtts:
            vals = list(tl.rtts)
            ok = [v for v in vals if v >= 0]
            last = vals[-1]
            th.text(s, f, f.m, "response " + ("lost" if last < 0 else f"{last:.1f} ms"), C["t1"], (o.x + 10, y0))
            top = th.line_graph(s, (o.x + 10, y0 + 22, o.w - 60, 60), [([v if v >= 0 else None for v in vals], C["t1"])],
                                vmin=0)
            th.text(s, f, f.s, f"{top:.0f} ms", C["t3"], (o.right - 8, y0 + 20), "topright")
            for i, v in enumerate(vals):
                if v < 0 and len(vals) > 1:
                    x = round(o.x + 10 + (o.w - 61) * i / (len(vals) - 1))
                    pygame.draw.line(s, C["red"], (x, y0 + 76), (x, y0 + 82), 2)
            if tl.kind == "ping" and ok:
                stat = (f"sent {tl.sent} lost {tl.lost} min {min(ok):.1f} avg {sum(ok) / len(ok):.1f} "
                        f"max {max(ok):.1f} ms")
                th.text(s, f, f.s, stat, C["t2"], (o.x + 10, y0 + 88), maxw=o.w - 20)
            y0 += 108
        lines = list(tl.lines)
        nrows = (o.bottom - 8 - y0) // 16
        for i, line in enumerate(lines[-nrows:] if nrows > 0 else []):
            col = (C["red"] if ("timed out" in line or "FAILED" in line or "needs root" in line or "no reply" in line)
                   else C["amb"] if line.startswith("---") else C["t2"])
            th.text(s, f, f.s, line, col, (o.x + 10, y0 + i * 16), maxw=o.w - 20)
        if not lines:
            th.text(s, f, f.m, "ping, traceroute and DNS results appear here", C["t3"], o.center, "center")
        return [("UP DN", "select"), ("A", "run"), ("B", "stop")]


def save_pcap(app, cap_dir):
    if not app.cap.raw:
        app.notify("Nothing captured yet", "warn")
        return
    path = os.path.join(cap_dir, time.strftime("capture-%Y%m%d-%H%M%S.pcap"))
    try:
        n = app.cap.save_pcap(path)
    except OSError as exc:
        app.notify("Save failed", "error", str(exc))
        return
    app.log.add(f"Saved {n} packets to captures/{os.path.basename(path)}")
    app.notify(f"Saved {n} packets", "ok", os.path.basename(path), 4)
