"""Field Terminal - look and drawing helpers (pygame 2.5+).

Pip glow: muted terminal green on a grey-black, amber for "look at this", red only for problems.
Text is VT323 (body and numbers) and Silkscreen (small caps labels), both pixel fonts rendered
without antialiasing at sizes where they stay crisp. Glow and scanlines are pre-rendered once
and cached, so a frame costs a handful of blits.
"""
import os
import re
import time

import pygame

W, H = 640, 480
HEADER_H, TABS_H, FOOTER_H = 28, 26, 24
TOP = HEADER_H + TABS_H            # first pixel row of the content area
BOTTOM = H - FOOTER_H              # first pixel row of the footer

C = {
    "bg": (12, 16, 13),        # #0c100d  grey-black, never pure black
    "tile": (17, 23, 18),      # #111712
    "line": (33, 64, 43),      # #21402b  tile borders, dividers
    "trk": (22, 38, 27),       # #16261b  empty part of bars and graphs
    "sel": (28, 52, 36),       # selected row
    "t1": (120, 240, 159),     # #78f09f  primary text and data
    "t2": (79, 174, 112),      # #4fae70  secondary text
    "t3": (47, 115, 73),       # #2f7349  hints, units, axis labels
    "amb": (232, 184, 74),     # #e8b84a  amber: time left, temperature, attention
    "red": (226, 98, 74),      # dim red: errors, plain-text protocols
    "teal": (127, 214, 200),   # remote access / file sharing protocols
    # bottom bar uses the "quiet terminal" palette: no glow, no scanlines
    "f_bg": (13, 17, 14), "f_line": (31, 51, 38), "f_key": (95, 216, 137), "f_lbl": (44, 106, 66),
}
STATUS = {"ok": C["t1"], "warn": C["amb"], "error": C["red"], "info": C["t2"], "off": C["t3"]}

# Protocol colours keep their meaning: green = encrypted, red = plain text, amber = name lookups,
# dim = network housekeeping, teal = remote access / file sharing.
PROTO = {
    "TLS": C["t1"], "QUIC": C["t1"], "SSH": C["t1"],
    "HTTP": C["red"], "Telnet": C["red"], "FTP": C["red"], "POP3": C["red"], "IMAP": C["red"], "SMTP": C["red"],
    "DNS": C["amb"], "mDNS": C["amb"], "LLMNR": C["amb"], "NetBIOS": C["amb"],
    "SMB": C["teal"], "RDP": C["teal"], "VNC": C["teal"], "MQTT": C["teal"],
    "EAPOL": C["amb"],
}


def proto_color(p):
    return PROTO.get(p, C["t3"])


def dim(color, k):
    return tuple(max(0, min(255, round(v * k))) for v in color)


class Fonts:
    """VT323 at 16/20/24/32/40 px and Silkscreen at its native 8 px, all without antialiasing."""

    def __init__(self, base):
        d = os.path.join(base, "fonts")
        vt = os.path.join(d, "VT323-Regular.ttf")
        sk = os.path.join(d, "Silkscreen-Regular.ttf")
        self.s = pygame.font.Font(vt, 16)       # dense tables, footer
        self.m = pygame.font.Font(vt, 20)       # body
        self.l = pygame.font.Font(vt, 24)       # emphasised values
        self.xl = pygame.font.Font(vt, 32)
        self.xxl = pygame.font.Font(vt, 40)     # headline numbers
        self.lbl = pygame.font.Font(sk, 8)      # tile labels, tabs
        self._cache = {}

    def render(self, font, s, color, glow=False):
        key = (id(font), s, color, glow)
        img = self._cache.get(key)
        if img is None:
            if len(self._cache) > 4000:
                self._cache.clear()
            s = clean(s)
            try:
                img = _render_noliga(font, s, color) if s else pygame.Surface((1, font.get_height()), pygame.SRCALPHA)
                if glow and s:
                    img = _glow(font, s, color, img)
            except (ValueError, pygame.error):           # a glyph SDL_ttf refuses: show plain ASCII instead
                safe = s.encode("ascii", "replace").decode() or "?"
                img = font.render(safe, False, color)
            self._cache[key] = img
        return img


def clean(s):
    """Text from the network (SSIDs, DNS names, banners) can hold control or invisible characters
    that make SDL_ttf raise. Keep printable characters of the basic plane only."""
    s = str(s)
    if s.isascii() and s.isprintable():
        return s
    return "".join(ch if ch.isprintable() and ord(ch) <= 0xFFFF else ("?" if ord(ch) > 0xFFFF else "")
                   for ch in s.replace("\t", " "))


_LIGA = re.compile(r"(?<=f)(?=[fil])")


def _render_noliga(font, s, color):
    """SDL_ttf shapes text with HarfBuzz, which turns "fi" into one ligature glyph and breaks the
    monospace grid. Render the pieces separately and join them."""
    parts = _LIGA.split(s) if "f" in s else [s]
    if len(parts) == 1:
        return font.render(s, False, color)
    imgs = [font.render(p, False, color) for p in parts]
    out = pygame.Surface((sum(i.get_width() for i in imgs), font.get_height()), pygame.SRCALPHA)
    x = 0
    for i in imgs:
        out.blit(i, (x, 0))
        x += i.get_width()
    return out


def _glow(font, s, color, img):
    """Soft phosphor glow: faint copies one pixel around the text, then the text on top."""
    halo = _render_noliga(font, s, color)
    halo.set_alpha(55)
    w, h = img.get_size()
    out = pygame.Surface((w + 2, h + 2), pygame.SRCALPHA)
    for dx, dy in ((0, 1), (2, 1), (1, 0), (1, 2)):
        out.blit(halo, (dx, dy))
    out.blit(img, (1, 1))
    return out


def fit(font, s, maxw):
    s = clean(s)
    if maxw is None or font.size(s)[0] <= maxw:
        return s
    lo, hi = 0, len(s)                      # longest prefix that fits together with the ellipsis
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if font.size(s[:mid] + "…")[0] <= maxw:
            lo = mid
        else:
            hi = mid - 1
    return s[:lo] + "…"


def text(surf, fonts, font, s, color, pos, anchor="topleft", maxw=None, glow=False):
    img = fonts.render(font, fit(font, str(s), maxw), color, glow)
    r = img.get_rect(**{anchor: pos})
    if glow:
        r.move_ip(-1, -1)
    surf.blit(img, r)
    return r


_LABELS = {}


def label(surf, fonts, s, color, pos, anchor="topleft"):
    """Silkscreen caps with 1 px letter spacing, like the mockup. Built once per text and colour."""
    s = clean(s).upper()
    key = (s, color)
    img = _LABELS.get(key)
    if img is None:
        imgs = [fonts.render(fonts.lbl, ch, color) for ch in s]
        w = sum(i.get_width() + 1 for i in imgs) - 1 if imgs else 0
        img = pygame.Surface((max(1, w), fonts.lbl.get_height()), pygame.SRCALPHA)
        x = 0
        for i in imgs:
            img.blit(i, (x, 0))
            x += i.get_width() + 1
        if len(_LABELS) > 2000:
            _LABELS.clear()
        _LABELS[key] = img
    r = img.get_rect(**{anchor: pos})
    surf.blit(img, r)
    return r


def arrow(surf, x, y, direction, color):
    """Small pixel arrow in a 7x9 box at (x, y): 'up', 'down', 'left', 'right' (the fonts have none)."""
    if direction in ("up", "down"):
        pygame.draw.rect(surf, color, (x + 3, y + (3 if direction == "up" else 0), 1, 6))
        tip = [(x, y + 3), (x + 3, y), (x + 6, y + 3)] if direction == "up" else \
            [(x, y + 5), (x + 3, y + 8), (x + 6, y + 5)]
    else:
        pygame.draw.rect(surf, color, (x + (3 if direction == "left" else 0), y + 4, 6, 1))
        tip = [(x + 3, y + 1), (x, y + 4), (x + 3, y + 7)] if direction == "left" else \
            [(x + 5, y + 1), (x + 8, y + 4), (x + 5, y + 7)]
    pygame.draw.lines(surf, color, False, tip)


def kv(surf, fonts, font, x, y, w, key, value, kcol=None, vcol=None, vglow=False):
    """Key on the left, value on the right edge of a w-wide row."""
    k = text(surf, fonts, font, key, kcol or C["t2"], (x, y))
    text(surf, fonts, font, value, vcol or C["t1"], (x + w, y), "topright", maxw=max(10, w - k.w - 8), glow=vglow)
    return k


def tile(surf, fonts, rect, title=None, aside=None, aside_color=None):
    """Flat tile: 1 px border, Silkscreen title left, dim note right. Returns the rect."""
    r = pygame.Rect(rect)
    pygame.draw.rect(surf, C["tile"], r)
    pygame.draw.rect(surf, C["line"], r, 1)
    if title:
        label(surf, fonts, title, C["t2"], (r.x + 8, r.y + 7))
    if aside:
        label(surf, fonts, aside, aside_color or C["t3"], (r.right - 8, r.y + 7), "topright")
    return r


def seg_bar(surf, rect, frac, color=None, track=True):
    """Segmented bar: 3 px lit, 2 px gap."""
    r = pygame.Rect(rect)
    if track:
        pygame.draw.rect(surf, C["trk"], r)
    frac = max(0.0, min(1.0, frac or 0.0))
    n = int(r.w * frac)
    col = color or C["t1"]
    x = r.x
    while x + 3 <= r.x + n:
        pygame.draw.rect(surf, col, (x, r.y, 3, r.h))
        x += 5
    if 0 < n and x == r.x:      # tiny values still show one segment
        pygame.draw.rect(surf, col, (r.x, r.y, 2, r.h))


def thin_bar(surf, rect, frac, color=None):
    r = pygame.Rect(rect)
    pygame.draw.rect(surf, C["trk"], r)
    frac = max(0.0, min(1.0, frac or 0.0))
    if frac > 0:
        pygame.draw.rect(surf, color or C["t1"], (r.x, r.y, max(2, round(r.w * frac)), r.h))


def vbar(surf, rect, frac, color=None):
    """Vertical segmented bar (core load): 3 px lit, 2 px gap, from the bottom."""
    r = pygame.Rect(rect)
    pygame.draw.rect(surf, C["trk"], r)
    frac = max(0.0, min(1.0, frac or 0.0))
    n = int(r.h * frac)
    col = color or C["t1"]
    y = r.bottom - 3
    while y >= r.bottom - n:
        pygame.draw.rect(surf, col, (r.x, y, r.w, 3))
        y -= 5


def line_graph(surf, rect, series, vmin=0.0, vmax=None, baseline=True, refs=None, markers=None):
    """Thin line graph. series: [(values, color)], values may contain None (gap).
    refs: [(value, color)] dashed reference lines. Returns the top value used."""
    r = pygame.Rect(rect)
    if vmax is None:
        vals_all = [v for vals, _ in series for v in vals if v is not None]
        top = nice_ceiling(max(vals_all) if vals_all else 1.0)
    else:
        top = vmax
    span = (top - vmin) or 1.0
    if baseline:
        pygame.draw.line(surf, C["line"], (r.x, r.bottom - 1), (r.right - 1, r.bottom - 1))
    for value, color in refs or []:
        y = round(r.bottom - 1 - (r.h - 2) * (value - vmin) / span)
        for x in range(r.x, r.right, 6):
            pygame.draw.line(surf, color, (x, y), (min(r.right - 1, x + 2), y))
    for vals, color in series:
        n = len(vals)
        if n < 2:
            continue
        seg = []
        x0, sx = r.x, (r.w - 1) / (n - 1)
        y0, sy = r.bottom - 2, (r.h - 3) / span
        for i, v in enumerate(vals):
            if v is None:
                if len(seg) >= 2:
                    pygame.draw.lines(surf, color, False, seg)
                seg = []
                continue
            v = vmin if v < vmin else top if v > top else v
            seg.append((x0 + sx * i, y0 - sy * (v - vmin)))
        if len(seg) >= 2:
            pygame.draw.lines(surf, color, False, seg)
        elif len(seg) == 1:
            pygame.draw.rect(surf, color, (round(seg[0][0]), round(seg[0][1]), 2, 2))
    if markers and series and len(series[0][0]) > 1:      # red ticks: lost packets and the like
        n = len(series[0][0])
        for i in markers:
            x = round(r.x + (r.w - 1) * i / (n - 1))
            pygame.draw.line(surf, C["red"], (x, r.bottom - 6), (x, r.bottom - 1), 2)
    return top


def battery_icon(surf, rect, frac, color=None, charging=False):
    r = pygame.Rect(rect)
    col = color or C["t1"]
    pygame.draw.rect(surf, col, r, 2)
    pygame.draw.rect(surf, col, (r.right, r.y + r.h // 2 - 4, 4, 8))
    inner = r.inflate(-8, -8)
    cells = 5
    cw = (inner.w - (cells - 1) * 2) / cells
    lit = 0 if frac is None else max(1 if frac > 0.03 else 0, round(cells * frac))
    for i in range(cells):
        x = round(inner.x + i * (cw + 2))
        pygame.draw.rect(surf, col if i < lit else C["trk"], (x, inner.y, round(cw), inner.h))
    if charging:
        cx, cy = r.center
        pts = [(cx + 2, r.y + 3), (cx - 4, cy + 1), (cx, cy + 1), (cx - 2, r.bottom - 3), (cx + 4, cy - 1), (cx, cy - 1)]
        pygame.draw.polygon(surf, C["amb"], pts)


def signal_bars(surf, x, y, q, color=None, small=False):
    """Four bars, bottom-left at (x, y); q = 0..100."""
    lit = 0 if not q or q <= 0 else 1 + min(3, q // 25)
    col = color or C["t1"]
    bw, step, h0, dh = (3, 5, 3, 3) if small else (6, 9, 6, 5)
    for i in range(4):
        bh = h0 + dh * i
        pygame.draw.rect(surf, col if i < lit else C["trk"], (x + i * step, y - bh, bw, bh))


def row_bg(surf, rect, selected=False, divider=True):
    r = pygame.Rect(rect)
    if selected:
        pygame.draw.rect(surf, C["sel"], r)
        pygame.draw.rect(surf, C["t1"], (r.x, r.y, 2, r.h))
    if divider:
        pygame.draw.line(surf, C["trk"], (r.x, r.bottom - 1), (r.right - 1, r.bottom - 1))


def status_mark(surf, x, y, kind, size=6):
    pygame.draw.rect(surf, STATUS.get(kind, C["t3"]), (x, y, size, size))


_SCAN = {}


def scanlines(surf, rect):
    """Faint CRT scanlines over the content (never the bottom bar). Pre-rendered once."""
    r = pygame.Rect(rect)
    img = _SCAN.get(r.size)
    if img is None:
        img = pygame.Surface(r.size, pygame.SRCALPHA)
        for y in range(0, r.h, 3):
            pygame.draw.line(img, (0, 0, 0, 40), (0, y), (r.w, y))
        _SCAN[r.size] = img
    surf.blit(img, r.topleft)


def fill_alpha(surf, rect, color, alpha):
    r = pygame.Rect(rect)
    s = pygame.Surface(r.size, pygame.SRCALPHA)
    s.fill((*color, alpha))
    surf.blit(s, r.topleft)


def nice_ceiling(v):
    import math
    if v <= 0:
        return 1.0
    e = math.floor(math.log10(v))
    b = 10 ** e
    for m in (1, 2, 2.5, 5, 10):
        if v <= m * b + 1e-12:
            return m * b
    return 10 * b


def wrap(font, s, maxw):
    words, lines, cur = s.split(), [], ""
    for w_ in words:
        test = (cur + " " + w_).strip()
        if font.size(test)[0] <= maxw:
            cur = test
        else:
            if cur:
                lines.append(cur)
            cur = w_
    if cur:
        lines.append(cur)
    return lines


def window_start(sel, count, rows):
    return max(0, min(sel - rows // 2, count - rows))


# ------------------------------------------------------------------ formatting

def fmt_bytes(n):
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if abs(n) < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} TB"


def fmt_mb(n_bytes):
    return f"{(n_bytes or 0) / 1048576:.0f} MB"


def fmt_gb(n_bytes):
    g = (n_bytes or 0) / 1073741824
    if g < 1:
        return f"{(n_bytes or 0) / 1048576:.0f} MB"
    return f"{g:.1f} GB" if g < 100 else f"{g:.0f} GB"


def fmt_rate(n):
    return fmt_bytes(n) + "/s"


def fmt_ago(t):
    if not t:
        return "never"
    s = int(time.time() - t)
    if s < 60:
        return f"{s}s ago"
    if s < 3600:
        return f"{s // 60}m ago"
    if s < 86400:
        return f"{s // 3600}h ago"
    return f"{s // 86400}d ago"


def fmt_dur(s):
    s = int(s)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"


def fmt_span(s):
    """Human length: '45 s', '12 min', '3 h 07 min', '2 d 7 h'."""
    if s is None:
        return "-"
    s = int(max(0, s))
    if s < 60:
        return f"{s} s"
    if s < 3600:
        return f"{s // 60} min"
    if s < 86400:
        return f"{s // 3600} h {s % 3600 // 60:02d} min"
    return f"{s // 86400} d {s % 86400 // 3600} h"


def fmt_short(s):
    """Compact length for tiles: '45s', '12m', '3h 07m', '2d 7h'."""
    if s is None:
        return "-"
    s = int(max(0, s))
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m"
    if s < 86400:
        return f"{s // 3600}h {s % 3600 // 60:02d}m"
    return f"{s // 86400}d {s % 86400 // 3600}h"
