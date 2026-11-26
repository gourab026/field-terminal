#!/usr/bin/env python3
"""Field Terminal - a pocket system monitor and network lab for Knulli handhelds (640x480).

    python3 main.py              run (as root on the handheld, from the Ports launcher)
    python3 main.py --demo       made-up data, for trying the interface anywhere
    python3 main.py --screens D  render every screen to PNGs in D (use SDL_VIDEODRIVER=dummy)

Tabs (L1 / R1): DASH, NET, POWER, CPU, MEMORY, DISK, PROCS, LOG. Quit: MENU + START.
"""
import os
import sys
import threading
import time

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

import pygame  # noqa: E402

import actions  # noqa: E402
import capture as cap  # noqa: E402
import inputs  # noqa: E402
import netcore as nc  # noqa: E402
import netmon  # noqa: E402
import screens_net  # noqa: E402
import screens_sys as ss  # noqa: E402
import sysinfo as si  # noqa: E402
import sysmon  # noqa: E402
import theme as th  # noqa: E402
from theme import C, W, H, TOP, BOTTOM  # noqa: E402

VERSION = "2.0"
LOG_DIR = os.path.join(BASE, "logs")
CAP_DIR = os.path.join(BASE, "captures")
CONF_PATH = os.path.join(BASE, "config.json")
CACHE_PATH = os.path.join(BASE, "cache.json")


class App:
    def __init__(self, demo=False, screens=None):
        pygame.init()
        pygame.mouse.set_visible(False)
        self.demo, self.screens_out = demo, screens
        if screens:
            self.screen = pygame.display.set_mode((W, H))
        else:
            info = pygame.display.Info()
            if demo and info.current_w > 1000:
                self.screen = pygame.display.set_mode((W * 2, H * 2))
            else:
                self.screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
        pygame.display.set_caption("Field Terminal")
        self.canvas = pygame.Surface((W, H))
        self.fonts = th.Fonts(BASE)
        self.conf = inputs.load_conf(CONF_PATH)
        self.input = inputs.Input(self.conf)
        self.calibrating = self.input.has_pad and "layout" not in self.conf and not screens
        self.log = netmon.EventLog(LOG_DIR)
        os.makedirs(CAP_DIR, exist_ok=True)
        if demo:
            import demo as dm
            (self.mon, self.wifi, self.disc, self.scan, self.tools, self.cap) = dm.build(self.log, netmon)
            self.sm = dm.build_sys(self.log, CACHE_PATH)
            self.act = dm.DemoActions(self.sm)
        else:
            self.mon = netmon.Monitor(self.log)
            self.wifi, self.disc = netmon.WifiScan(self.mon, self.log), netmon.Discovery(self.mon, self.log)
            self.scan, self.tools = netmon.PortScan(self.log), netmon.Tools(self.mon, self.log)
            self.cap = cap.Capture(self.mon.iface, self.mon.info.get("ip"), self.log)
            self.sm = sysmon.SysMonitor(self.log, CACHE_PATH)
            self.act = actions
        info = self.mon.info
        self.log.add(f"Field Terminal {VERSION} started on {self.mon.iface} ({info.get('ip')}), "
                     f"battery {'found' if self.sm.bdir or demo else 'not found'}"
                     + ("" if nc.is_root() or demo else ", not root: limited mode"))
        self.pages = [ss.Dash(), screens_net.Net(), ss.Power(), ss.Cpu(), ss.Memory(), ss.Disk(), ss.Procs(), ss.Log()]
        self.tab = 0
        self.toast = None
        self.confirm = None                  # (title, detail, on_yes)
        self._logger = ("n/a", 0.0)
        self._zram_boot = None
        self.running, self.dirty, self.last_draw = True, True, 0.0
        self.mon.active = False
        self.mon.start()
        self.sm.start()
        self.pages[0].enter(self)
        if not screens:
            self.bg(self.ensure_logger)
            if not demo:
                self.bg(self.write_report)

    # ------------------------------------------------------------------ helpers
    def bg(self, fn, *args):
        threading.Thread(target=fn, args=args, daemon=True).start()

    def notify(self, title, kind="info", subtitle="", secs=2.8):
        self.toast = (title, kind, subtitle, time.time() + secs)
        self.dirty = True

    def ask(self, title, detail, on_yes):
        self.confirm = (title, detail, on_yes)
        self.dirty = True

    @property
    def page(self):
        return self.pages[self.tab]

    def switch(self, tab):
        if tab == self.tab:
            return
        self.page.leave(self)
        self.tab = tab % len(self.pages)
        self.page.enter(self)

    def recalibrate(self):
        self.conf.pop("layout", None)
        self.calibrating = True

    def save_pcap(self):
        screens_net.save_pcap(self, CAP_DIR)

    def write_report(self):
        """What the sensors look like on this handheld (read only), for checking from a PC."""
        try:
            with open(os.path.join(LOG_DIR, "sensors.txt"), "w") as f:
                f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + f"Field Terminal {VERSION}\n" + si.report())
        except Exception as exc:
            self.log.add(f"Sensor report failed: {exc}", "warn")

    # ------------------------------------------------------------------ battery logger
    def can_change_system(self):
        return self.demo or (nc.is_root() and os.path.isdir(si.P("/userdata/system")))

    def logger_state(self):
        if self.demo:
            return "on" if self.act.logger else "off"
        if not self.can_change_system():
            return "n/a"
        state, when = self._logger
        if time.time() - when > 5:
            state = "on" if actions.logger_installed() and actions.logger_running() else "off"
            self._logger = (state, time.time())
        return state

    def ensure_logger(self):
        """First start: install the battery logger (the user chose it). Later starts: keep it
        up to date, unless it was turned off in the POWER tab."""
        if self.demo or not self.can_change_system() or self.conf.get("logger") == "off":
            return
        try:
            ok, msg = actions.logger_install()
        except Exception as exc:
            ok, msg = False, str(exc)
        self._logger = ("n/a", 0.0)
        if ok:
            if self.conf.get("logger") != "on":
                self.conf["logger"] = "on"
                inputs.save_conf(CONF_PATH, self.conf)
                self.notify("Battery logger on", "ok", "records one line a minute")
            self.log.add(f"Battery logger {msg}")
        else:
            self.log.add(f"Battery logger not installed: {msg}", "warn")

    def toggle_logger(self):
        state = self.logger_state()
        if state == "n/a":
            self.notify("Not available here", "warn", "needs the handheld (root)")
            return
        if state == "on":
            self.ask("Turn the battery logger off?", "It stops, and is removed from boot. History stays.",
                     self._logger_off)
        else:
            self.ask("Turn the battery logger on?", "One line a minute to the SD card, also when this app is closed.",
                     self._logger_on)

    def _logger_on(self):
        self.bg(self._logger_set, True)

    def _logger_off(self):
        self.bg(self._logger_set, False)

    def _logger_set(self, on):
        """Runs in the background: installing or removing can take a few seconds."""
        try:
            if self.demo:
                self.act.logger = on
                ok, msg = True, "on" if on else "removed"
            else:
                ok, msg = actions.logger_install() if on else actions.logger_remove()
        except Exception as exc:          # full or read-only card, permissions: report, never crash
            ok, msg = False, str(exc)
        self._logger = ("n/a", 0.0)
        if not ok:
            self.log.add(f"Battery logger not changed: {msg}", "warn")
            self.notify("Logger not changed", "warn", msg[:60])
            return
        self.conf["logger"] = "on" if on else "off"
        inputs.save_conf(CONF_PATH, self.conf)
        self.log.add(f"Battery logger {msg}" if on else "Battery logger turned off and removed from boot")
        self.notify("Battery logger on" if on else "Battery logger off", "ok" if on else "warn",
                    "" if on else "history kept")

    # ------------------------------------------------------------------ CPU mode, ZRAM
    def set_cpu_mode(self, mode):
        if not self.can_change_system():
            self.notify("Not available here", "warn", "needs the handheld (root)")
            return
        cur = actions.mode_of(self.sm.policy.get("governor"))
        name = actions.MODES[mode][0]
        if mode == cur:
            self.notify(f"Already {name}")
            return

        def go():
            try:
                ok, msg = self.act.set_mode(mode)
            except actions.Refused as exc:
                ok, msg = False, str(exc)
            self.sm.policy = si.cpu_policy() if not self.demo else self.sm.policy
            self.log.add(f"CPU mode: {msg}", "info" if ok else "warn")
            self.notify(f"CPU mode {name}" if ok else "Not changed", "ok" if ok else "warn", msg if not ok else "")

        if name == "Maximum":
            self.ask("Run the CPU at full speed?", "Warmer, shorter battery, and it stays on after reboot until you "
                     "change it. It still slows down if too hot.",
                     lambda: self.bg(go))
        else:
            self.bg(go)

    def zram_boot(self):
        if self.demo:
            return self.act.zram_boot
        if not self.can_change_system():
            return None
        if self._zram_boot is None:
            self._zram_boot = "?"
            self.bg(self._read_zram_boot)
        return None if self._zram_boot == "?" else self._zram_boot

    def _read_zram_boot(self):
        self._zram_boot = actions.zram_boot_enabled()
        self.dirty = True

    def toggle_zram_boot(self):
        cur = self.zram_boot()
        if cur is None:
            self.notify("Not available here", "warn")
            return

        def go():
            ok, out = self.act.zram_set_boot(not cur)
            self._zram_boot = None
            self.log.add(f"ZRAM at boot: {'on' if not cur else 'off'}" if ok else f"ZRAM setting failed: {out}")
            self.notify(f"ZRAM at boot {'on' if not cur else 'off'}" if ok else "Not changed", "ok" if ok else "warn")

        self.ask(f"ZRAM at boot: turn {'off' if cur else 'on'}?", "Uses Knulli's own ZRAM service. Takes effect at the "
                 "next boot.", lambda: self.bg(go))

    def toggle_zram_now(self):
        if not self.can_change_system():
            self.notify("Not available here", "warn")
            return
        z, m = self.sm.zram, self.sm.mem
        if z and z["active"]:
            if not actions.zram_can_stop(m, z):
                self.notify("Not now", "warn", "ZRAM holds more than free RAM")
                return

            def stop():
                try:
                    ok, out = self.act.zram_stop(m, z)
                except actions.Refused as exc:
                    ok, out = False, str(exc)
                self.log.add("ZRAM stopped" if ok else f"ZRAM stop failed: {out}", "info" if ok else "warn")
                self.notify("ZRAM stopped" if ok else "Not stopped", "ok" if ok else "warn")
            self.ask("Stop ZRAM now?", "Its contents move back to RAM first.", lambda: self.bg(stop))
        else:
            def start():
                ok, out = self.act.zram_start()
                self.log.add("ZRAM started" if ok else f"ZRAM start failed: {out}", "info" if ok else "warn")
                self.notify("ZRAM started" if ok else "Not started", "ok" if ok else "warn")
            self.ask("Start ZRAM now?", "Adds about half of RAM as compressed memory until reboot.",
                     lambda: self.bg(start))

    # ------------------------------------------------------------------ input
    def handle(self, a):
        self.dirty = True
        if a == "quit" or ({"menu", "start"} <= self.input.pressed and a in ("menu", "start")):
            self.running = False
            return
        if self.confirm:
            if a == "a":
                _t, _d, fn = self.confirm
                self.confirm = None
                fn()
            elif a == "b":
                self.confirm = None
            return
        if a == "l1":
            self.switch(self.tab - 1)
            return
        if a == "r1":
            self.switch(self.tab + 1)
            return
        try:
            if self.page.handle(self, a):
                return
        except Exception as exc:
            self.log.add(f"{self.page.name} button error: {type(exc).__name__}: {exc}", "error")
            self.notify("Something went wrong", "error", "see LOG")
            return
        if a in ("left", "right"):
            self.switch(self.tab + (1 if a == "right" else -1))

    def calibrate(self, ev):
        """First run: learn whether the button labelled A is SDL's A or B."""
        raw = None
        if ev.type == pygame.KEYDOWN:
            raw = "keyboard"
        elif self.input.sdlc and ev.type == pygame.CONTROLLERBUTTONDOWN:
            raw = self.input.cbtn.get(ev.button)
        if raw == "s_b":
            self.conf["layout"] = "nintendo"
        elif raw in ("s_a", "keyboard"):
            self.conf["layout"] = "xbox"
        else:
            return
        inputs.save_conf(CONF_PATH, self.conf)
        self.input.pressed.clear()
        self.input.held.clear()
        self.calibrating = False
        self.log.add(f"Button layout set: {self.conf['layout']}")
        self.notify("Buttons set", "ok", "A confirms, B goes back")

    # ------------------------------------------------------------------ main loop
    def run(self):
        clock = pygame.time.Clock()
        while self.running:
            for ev in pygame.event.get():
                if ev.type == pygame.QUIT:
                    self.running = False
                elif self.calibrating:
                    self.calibrate(ev)
                    self.dirty = True
                else:
                    for a in self.input.handle(ev):
                        self.handle(a)
            if not self.calibrating:
                for a in self.input.repeats():
                    self.handle(a)
            page = self.page
            if hasattr(page, "tick"):
                page.tick(self)
            now = time.time()
            net = page.name == "NET"
            busy = net and page.busy(self)
            period = 0.1 if busy else (0.5 if net else 1.0)
            if self.dirty or now - self.last_draw > period:
                self.draw()
                self.present()
                self.last_draw, self.dirty = now, False
            clock.tick(30)
        self.shutdown()

    def shutdown(self):
        self.log.add("Field Terminal closed")
        self.page.leave(self)
        self.mon.stop_ev.set()
        self.sm.stop_ev.set()
        self.cap.stop()
        pygame.quit()

    def present(self):
        sw, sh = self.screen.get_size()
        if (sw, sh) == (W, H):
            self.screen.blit(self.canvas, (0, 0))
        else:
            scale = min(sw / W, sh / H)
            size = (int(W * scale), int(H * scale))
            self.screen.fill(C["bg"])
            self.screen.blit(pygame.transform.scale(self.canvas, size), ((sw - size[0]) // 2, (sh - size[1]) // 2))
        pygame.display.flip()

    # ================================================================== drawing
    def draw(self):
        s = self.canvas
        s.fill(C["bg"])
        self.draw_header(s)
        self.draw_tabs(s)
        try:
            hints = self.page.draw(self, s)
        except Exception as exc:                    # one bad value must never close the app
            hints = [("L1 R1", "tabs")]
            self.draw_error(s, exc)
        th.scanlines(s, (0, 0, W, BOTTOM))
        self.draw_footer(s, hints)
        self.draw_toast(s)
        if self.confirm:
            self.draw_confirm(s)
        if self.calibrating:
            self.draw_calibrate(s)

    def draw_error(self, s, exc):
        msg = f"{type(exc).__name__}: {exc}"
        if getattr(self, "_last_error", None) != msg:
            self._last_error = msg
            import traceback
            self.log.add(f"{self.page.name} page error: {msg}", "error")
            traceback.print_exc()                    # lands in log.txt for a bug report
        r = th.tile(s, self.fonts, (6, TOP + 6, W - 12, BOTTOM - TOP - 12), self.page.name, "error", C["red"])
        th.text(s, self.fonts, self.fonts.m, "This page hit a value it could not show.", C["t1"], (r.x + 10, r.y + 30))
        th.text(s, self.fonts, self.fonts.s, msg, C["red"], (r.x + 10, r.y + 56), maxw=r.w - 20)
        th.text(s, self.fonts, self.fonts.s, "Other tabs still work. Details are in the LOG tab and log.txt.", C["t3"],
                (r.x + 10, r.y + 80))

    def draw_header(self, s):
        f, mon, b = self.fonts, self.mon, self.sm.bat
        pygame.draw.line(s, C["line"], (0, th.HEADER_H - 1), (W, th.HEADER_H - 1))
        th.text(s, f, f.m, "FIELD TERMINAL", C["t1"], (12, 4), glow=True)
        x = W - 12
        pct = b.get("pct")
        if pct is not None:
            low = pct < 20 and b.get("status") == "Discharging"
            chg = b.get("status") in ("Charging", "Full")
            r = th.text(s, f, f.m, f"{'+' if chg else ''}{pct}%", C["amb"] if low or chg else C["t2"], (x, 4), "topright",
                        glow=low)
            x = r.left - 16
        r = th.text(s, f, f.m, time.strftime("%H:%M"), C["t2"], (x, 4), "topright")
        x = r.left - 16
        if mon.info.get("wireless"):
            sig = mon.link.get("signal")
            th.signal_bars(s, x - 18, 20, nc.signal_quality(sig) if sig is not None else 0, small=True)
            r = th.text(s, f, f.m, mon.link.get("ssid") or "no Wi-Fi", C["t2"], (x - 24, 4), "topright", maxw=170)

    def draw_tabs(self, s):
        f = self.fonts
        y0, y1 = th.HEADER_H, TOP
        pygame.draw.line(s, C["line"], (0, y1 - 1), (W, y1 - 1))
        x0, x1 = 8, W - 8
        w = (x1 - x0) / len(self.pages)
        busy = self.pages[1].busy(self)
        for i, p in enumerate(self.pages):
            rr = pygame.Rect(round(x0 + w * i) + 2, y0 + 5, round(w) - 4, y1 - y0 - 10)
            name = p.name + (" ·" if p.name == "NET" and busy else "")
            if i == self.tab:
                pygame.draw.rect(s, C["t1"], rr)
                th.label(s, f, name, C["bg"], rr.center, "center")
            else:
                th.label(s, f, name, C["t2"], rr.center, "center")

    def draw_footer(self, s, hints):
        f = self.fonts
        pygame.draw.rect(s, C["f_bg"], (0, BOTTOM, W, H - BOTTOM))
        pygame.draw.line(s, C["f_line"], (0, BOTTOM), (W, BOTTOM))
        x, y = 12, BOTTOM + 4
        for key, lab in hints or []:
            k = th.text(s, f, f.s, key, C["f_key"], (x, y))
            r = th.text(s, f, f.s, lab, C["f_lbl"], (k.right + 6, y))
            x = r.right + 16
        q = th.text(s, f, f.s, "quit", C["f_lbl"], (W - 12, y), "topright")
        th.text(s, f, f.s, "MENU+START", C["f_key"], (q.left - 6, y), "topright")

    def draw_toast(self, s):
        if not self.toast:
            return
        title, kind, sub, until = self.toast
        if time.time() > until:
            self.toast = None
            return
        f = self.fonts
        w = min(460, f.m.size(title)[0] + (f.s.size(sub)[0] + 12 if sub else 0) + 34)
        r = pygame.Rect(W - 8 - w, BOTTOM - 38, w, 32)
        pygame.draw.rect(s, C["tile"], r)
        pygame.draw.rect(s, th.STATUS.get(kind, C["t2"]), r, 1)
        pygame.draw.rect(s, th.STATUS.get(kind, C["t2"]), (r.x, r.y, 4, r.h))
        t = th.text(s, f, f.m, title, C["t1"], (r.x + 14, r.y + 6), glow=True)
        if sub:
            th.text(s, f, f.s, sub, C["t2"], (t.right + 10, r.y + 9), maxw=r.right - t.right - 18)

    def draw_confirm(self, s):
        f = self.fonts
        title, detail, _fn = self.confirm
        th.fill_alpha(s, (0, 0, W, BOTTOM), C["bg"], 200)
        r = pygame.Rect(90, 150, 460, 150)
        pygame.draw.rect(s, C["tile"], r)
        pygame.draw.rect(s, C["amb"], r, 1)
        th.label(s, f, "confirm", C["amb"], (r.x + 14, r.y + 10))
        th.text(s, f, f.l, title, C["t1"], (r.x + 14, r.y + 24), maxw=r.w - 28, glow=True)
        for i, line in enumerate(th.wrap(f.m, detail, r.w - 28)[:3]):
            th.text(s, f, f.m, line, C["t2"], (r.x + 14, r.y + 56 + i * 20))
        k = th.text(s, f, f.m, "A", C["amb"], (r.x + 14, r.bottom - 28), glow=True)
        k = th.text(s, f, f.m, "yes", C["t2"], (k.right + 6, r.bottom - 28))
        k = th.text(s, f, f.m, "B", C["amb"], (k.right + 20, r.bottom - 28), glow=True)
        th.text(s, f, f.m, "cancel", C["t2"], (k.right + 6, r.bottom - 28))

    def draw_calibrate(self, s):
        f = self.fonts
        th.fill_alpha(s, (0, 0, W, H), C["bg"], 220)
        r = pygame.Rect(150, 150, 340, 170)
        pygame.draw.rect(s, C["tile"], r)
        pygame.draw.rect(s, C["line"], r, 1)
        th.label(s, f, "field terminal", C["t2"], (r.x + 16, r.y + 12))
        th.text(s, f, f.l, "Set up buttons", C["t1"], (r.x + 16, r.y + 28), glow=True)
        th.text(s, f, f.m, "Press the button labelled A.", C["t2"], (r.x + 16, r.y + 60))
        k = pygame.Rect(r.x + 16, r.y + 90, 36, 36)
        pygame.draw.rect(s, C["t1"], k, 2)
        th.text(s, f, f.l, "A", C["t1"], k.center, "center", glow=True)
        th.text(s, f, f.s, "Asked once. Change it later in NET > TOOLS.", C["t3"], (r.x + 16, r.y + 140))


def main():
    args = sys.argv[1:]
    if "--screens" in args:
        out = args[args.index("--screens") + 1]
        import demo as dm
        dm.render_screens(App, out)
        return
    app = App(demo="--demo" in args)
    app.run()


if __name__ == "__main__":
    main()
