"""Field Terminal - buttons: SDL game controller (or raw joystick) and keyboard, with key repeat
for the d-pad and a one-time A/B layout check. Moved unchanged from Net Analyzer 1.1."""
import json
import time

import pygame

KEYMAP = {pygame.K_UP: "up", pygame.K_DOWN: "down", pygame.K_LEFT: "left", pygame.K_RIGHT: "right",
          pygame.K_RETURN: "a", pygame.K_z: "a", pygame.K_ESCAPE: "b", pygame.K_x: "b", pygame.K_BACKSPACE: "b",
          pygame.K_c: "x", pygame.K_v: "y", pygame.K_q: "l1", pygame.K_PAGEUP: "l1", pygame.K_e: "r1",
          pygame.K_PAGEDOWN: "r1", pygame.K_SPACE: "start", pygame.K_TAB: "select", pygame.K_m: "menu",
          pygame.K_F10: "quit"}


class Input:
    DELAY, RATE = 0.32, 0.085
    DIRS = ("up", "down", "left", "right")

    def __init__(self, conf):
        self.conf, self.ctrls, self.joys = conf, {}, {}
        self.held, self.pressed, self.axes = {}, set(), {}
        self.sdlc, self.cbtn = None, {}
        try:
            from pygame._sdl2 import controller as sdlc
            sdlc.init()
            self.sdlc = sdlc
            self.cbtn = {pygame.CONTROLLER_BUTTON_A: "s_a", pygame.CONTROLLER_BUTTON_B: "s_b",
                         pygame.CONTROLLER_BUTTON_X: "s_x", pygame.CONTROLLER_BUTTON_Y: "s_y",
                         pygame.CONTROLLER_BUTTON_LEFTSHOULDER: "l1", pygame.CONTROLLER_BUTTON_RIGHTSHOULDER: "r1",
                         pygame.CONTROLLER_BUTTON_START: "start", pygame.CONTROLLER_BUTTON_BACK: "select",
                         pygame.CONTROLLER_BUTTON_GUIDE: "menu",
                         pygame.CONTROLLER_BUTTON_DPAD_UP: "up", pygame.CONTROLLER_BUTTON_DPAD_DOWN: "down",
                         pygame.CONTROLLER_BUTTON_DPAD_LEFT: "left", pygame.CONTROLLER_BUTTON_DPAD_RIGHT: "right"}
            for i in range(sdlc.get_count()):
                self._open(i)
        except Exception:
            self.sdlc = None
        if not self.ctrls:
            pygame.joystick.init()
            for i in range(pygame.joystick.get_count()):
                j = pygame.joystick.Joystick(i)
                j.init()
                self.joys[j.get_instance_id()] = j

    def _open(self, i):
        try:
            if self.sdlc and self.sdlc.is_controller(i) and i not in self.ctrls:
                self.ctrls[i] = self.sdlc.Controller(i)
        except Exception:
            pass

    @property
    def has_pad(self):
        return bool(self.ctrls or self.joys)

    def face(self, raw):
        if self.conf.get("layout") == "nintendo":
            return {"s_a": "b", "s_b": "a", "s_x": "y", "s_y": "x"}.get(raw, raw)
        return {"s_a": "a", "s_b": "b", "s_x": "x", "s_y": "y"}.get(raw, raw)

    def _press(self, a):
        self.pressed.add(a)
        if a in self.DIRS:
            self.held[a] = time.time() + self.DELAY
        return [a]

    def _release(self, a):
        self.pressed.discard(a)
        self.held.pop(a, None)

    def _axis_dir(self, neg, pos, value):
        out = []
        for name, active in ((neg, value < -20000), (pos, value > 20000)):
            was = self.axes.get(name, False)
            if active and not was:
                out += self._press(name)
                self.axes[name] = True
            elif was and abs(value) < 12000:
                self._release(name)
                self.axes[name] = False
        return out

    def handle(self, ev):
        t, out = ev.type, []
        if t == pygame.KEYDOWN and ev.key in KEYMAP:
            out += self._press(KEYMAP[ev.key])
        elif t == pygame.KEYUP and ev.key in KEYMAP:
            self._release(KEYMAP[ev.key])
        elif self.sdlc and t == pygame.CONTROLLERDEVICEADDED:
            self._open(ev.device_index)
        elif self.sdlc and t == pygame.CONTROLLERBUTTONDOWN:
            raw = self.cbtn.get(ev.button)
            if raw:
                out += self._press(self.face(raw))
        elif self.sdlc and t == pygame.CONTROLLERBUTTONUP:
            raw = self.cbtn.get(ev.button)
            if raw:
                self._release(self.face(raw))
        elif self.sdlc and t == pygame.CONTROLLERAXISMOTION:
            if ev.axis == pygame.CONTROLLER_AXIS_LEFTY:
                out += self._axis_dir("up", "down", ev.value)
            elif ev.axis == pygame.CONTROLLER_AXIS_LEFTX:
                out += self._axis_dir("left", "right", ev.value)
        elif not self.ctrls and t == pygame.JOYHATMOTION:
            hx, hy = ev.value
            for name, on in (("up", hy == 1), ("down", hy == -1), ("left", hx == -1), ("right", hx == 1)):
                if on and name not in self.pressed:
                    out += self._press(name)
                elif not on and name in self.pressed:
                    self._release(name)
        return out

    def repeats(self):
        now, out = time.time(), []
        for a, nxt in list(self.held.items()):
            if now >= nxt:
                out.append(a)
                self.held[a] = now + self.RATE
        return out


def load_conf(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_conf(path, conf):
    try:
        with open(path, "w") as f:
            json.dump(conf, f, indent=1)
    except OSError:
        pass
