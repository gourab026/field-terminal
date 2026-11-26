"""Draw cover.png (640x480) for the Ports menu in Field Terminal's own look. PC-only helper.

    SDL_VIDEODRIVER=dummy python3 tools/make_art.py
"""
import os
import sys

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
import pygame  # noqa: E402

APP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ports", "fieldterminal")
sys.path.insert(0, APP)
import theme as th  # noqa: E402
from theme import C  # noqa: E402

pygame.init()
W, H = 640, 480
s = pygame.Surface((W, H))
s.fill(C["bg"])
f = th.Fonts(APP)

# frame
pygame.draw.rect(s, C["line"], (24, 24, W - 48, H - 48), 1)
th.label(s, f, "knulli · ports · utility", C["t3"], (40, 36))
th.label(s, f, "v2.0", C["t3"], (W - 40, 36), "topright")

# title
big = pygame.font.Font(os.path.join(APP, "fonts", "VT323-Regular.ttf"), 80)
title = th._render_noliga(big, "FIELD", C["t1"])
title2 = th._render_noliga(big, "TERMINAL", C["t1"])
for img, y in ((title, 62), (title2, 126)):
    halo = img.copy()
    halo.set_alpha(50)
    for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
        s.blit(halo, (40 + dx, y + dy))
    s.blit(img, (40, y))
th.text(s, f, f.m, "system monitor · network lab", C["t2"], (44, 206))
pygame.draw.rect(s, C["amb"], (44, 232, 10, 18))          # cursor

# three readouts, like the dashboard
y = 276
r = th.tile(s, f, (40, y, 176, 150), "BATTERY", "health 94%")
th.battery_icon(s, (r.x + 10, r.y + 26, 56, 26), 0.57)
th.text(s, f, f.xl, "57%", C["t1"], (r.x + 80, r.y + 22), glow=True)
th.text(s, f, f.l, "3h 07m left", C["amb"], (r.x + 10, r.y + 64), glow=True)
th.text(s, f, f.m, "3.79 V · 575 mA", C["t2"], (r.x + 10, r.y + 94))
th.text(s, f, f.m, "discharging", C["t3"], (r.x + 10, r.y + 116))

r = th.tile(s, f, (232, y, 176, 150), "CPU", "mode normal")
for i, ld in enumerate((0.4, 0.28, 0.33, 0.22)):
    th.vbar(s, (r.x + 10 + i * 18, r.y + 26, 13, 62), ld)
th.text(s, f, f.xl, "1248", C["t1"], (r.x + 90, r.y + 22), glow=True)
th.text(s, f, f.m, "MHz", C["t2"], (r.x + 90, r.y + 52))
th.text(s, f, f.l, "50°C", C["amb"], (r.x + 10, r.y + 100), glow=True)
th.text(s, f, f.m, "load 29%", C["t2"], (r.x + 84, r.y + 104))

r = th.tile(s, f, (424, y, 176, 150), "NETWORK", "online")
th.signal_bars(s, r.x + 10, r.y + 48, 80)
th.text(s, f, f.m, "HomeNet", C["t1"], (r.x + 54, r.y + 28))
th.text(s, f, f.m, "-45 dBm · 18 ms", C["t2"], (r.x + 10, r.y + 58))
vals = [20, 35, 25, 60, 80, 45, 70, 90, 55, 40, 65, 85, 50, 30, 75, 95, 60, 45, 70, 52]
th.line_graph(s, (r.x + 10, r.y + 84, r.w - 20, 52), [(vals, C["t1"])], vmin=0, vmax=100)

th.scanlines(s, (0, 0, W, H))
out = os.path.join(APP, "cover.png")
pygame.image.save(s, out)
print("saved", out)
