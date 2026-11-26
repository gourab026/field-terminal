"""Shared fixtures: a fake handheld (sysfs, procfs, games folder) under a temp directory."""
import os
import sys

import pytest

APP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ports", "fieldterminal")
sys.path.insert(0, os.path.abspath(APP))
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

import sysinfo as si  # noqa: E402


def put(root, path, text):
    p = os.path.join(str(root), path.lstrip("/"))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as f:
        f.write(text)
    return p


ES = """<?xml version="1.0"?>
<systemList>
  <system><name>gba</name><fullname>Game Boy Advance</fullname><path>/userdata/roms/gba</path>
    <extension>.gba .zip .7z</extension></system>
  <system><name>psx</name><fullname>PlayStation</fullname><path>/userdata/roms/psx</path>
    <extension>.cue .chd .pbp .m3u .iso .img</extension></system>
  <system><name>ports</name><fullname>Ports</fullname><path>/userdata/roms/ports</path>
    <extension>.sh .py</extension></system>
  <system><name>empty</name><fullname>Empty</fullname><path>/userdata/roms/empty</path>
    <extension>.bin</extension></system>
</systemList>
"""


def make_handheld(root):
    """An RG40XXH-like tree with the values the readers should pick up."""
    b = "/sys/class/power_supply/axp2202-battery"
    put(root, b + "/type", "Battery\n")
    put(root, b + "/status", "Discharging\n")
    put(root, b + "/capacity", "60\n")
    put(root, b + "/voltage_now", "3790000\n")
    put(root, b + "/current_now", "-575000\n")
    put(root, b + "/charge_full_design", "3200000\n")
    put(root, "/sys/class/power_supply/axp2202-usb/type", "USB\n")
    put(root, "/sys/class/power_supply/axp2202-usb/online", "0\n")
    put(root, "/tmp/battery.percent", "57\n")
    put(root, "/sys/class/backlight/backlight/brightness", "80\n")
    put(root, "/sys/class/backlight/backlight/max_brightness", "255\n")
    put(root, "/proc/uptime", "4320.55 12000.00\n")
    put(root, "/sys/devices/system/cpu/present", "0-3\n")
    put(root, "/sys/devices/system/cpu/online", "0-3\n")
    for c in range(4):
        put(root, f"/sys/devices/system/cpu/cpu{c}/cpufreq/scaling_cur_freq", "1200000\n")
    pol = "/sys/devices/system/cpu/cpufreq/policy0"
    put(root, pol + "/scaling_governor", "schedutil\n")
    put(root, pol + "/scaling_available_governors", "conservative ondemand userspace powersave performance schedutil\n")
    put(root, pol + "/scaling_max_freq", "1512000\n")
    put(root, pol + "/cpuinfo_max_freq", "1512000\n")
    put(root, pol + "/cpuinfo_min_freq", "480000\n")
    put(root, "/sys/class/thermal/thermal_zone0/type", "cpu_thermal_zone\n")
    put(root, "/sys/class/thermal/thermal_zone0/temp", "50600\n")
    put(root, "/sys/class/thermal/thermal_zone1/type", "gpu_thermal_zone\n")
    put(root, "/sys/class/thermal/thermal_zone1/temp", "48100\n")
    put(root, "/sys/class/devfreq/1800000.gpu/cur_freq", "432000000\n")
    put(root, "/proc/stat", "cpu  100 0 100 800 0 0 0 0 0 0\n"
                            "cpu0 25 0 25 200 0 0 0 0 0 0\ncpu1 25 0 25 200 0 0 0 0 0 0\n"
                            "cpu2 25 0 25 200 0 0 0 0 0 0\ncpu3 25 0 25 200 0 0 0 0 0 0\n")
    put(root, "/proc/meminfo", "MemTotal:  919552 kB\nMemFree:  122880 kB\nMemAvailable:  355328 kB\n"
                               "Buffers:  10240 kB\nCached:  204800 kB\nSwapTotal: 459776 kB\nSwapFree: 459776 kB\n")
    put(root, "/sys/block/zram0/disksize", str(449 * 1048576) + "\n")
    put(root, "/sys/block/zram0/mm_stat", "10485760 3145728 4194304 0 4194304 0 0 0 0\n")
    put(root, "/sys/block/zram0/comp_algorithm", "lzo [lz4] zstd\n")
    put(root, "/proc/swaps", "Filename Type Size Used Priority\n/dev/zram0 partition 459776 10240 1000\n")
    os.makedirs(os.path.join(str(root), "userdata"), exist_ok=True)
    put(root, "/proc/mounts", f"/dev/mmcblk1p4 /userdata exfat rw 0 0\n/dev/loop0 / squashfs ro 0 0\n"
                              f"tmpfs /tmp tmpfs rw 0 0\n/dev/mmcblk1p4 /userdata/roms exfat rw 0 0\n")
    put(root, "/usr/share/emulationstation/es_systems.cfg", ES)
    r = "/userdata/roms"
    for n in ("Metroid.gba", "Zelda.zip", "readme.txt", "gamelist.xml"):
        put(root, f"{r}/gba/{n}", "x" * 100)
    put(root, f"{r}/gba/images/Metroid.png", "x")
    put(root, f"{r}/gba/sub/Wario.gba", "x")
    put(root, f"{r}/psx/FF7/Final Fantasy VII.m3u", "Disc1.cue\nDisc2.cue\n")
    put(root, f"{r}/psx/FF7/Disc1.cue", 'FILE "Disc1.bin" BINARY\n')
    put(root, f"{r}/psx/FF7/Disc2.cue", 'FILE "Disc2.bin" BINARY\n')
    put(root, f"{r}/psx/FF7/Disc1.bin", "x" * 1000)
    put(root, f"{r}/psx/FF7/Disc2.bin", "x" * 1000)
    put(root, f"{r}/psx/Tekken 3.chd", "x" * 500)
    put(root, f"{r}/ports/Limbo.sh", "#!/bin/bash\n")
    put(root, f"{r}/ports/PortMaster.sh", "#!/bin/bash\n")
    put(root, f"{r}/ports/Field Terminal.sh", "#!/bin/bash\n")
    put(root, f"{r}/ports/limbo/data.bin", "x" * 50)
    os.makedirs(os.path.join(str(root), "userdata/roms/empty"), exist_ok=True)
    return root


@pytest.fixture
def handheld(tmp_path, monkeypatch):
    make_handheld(tmp_path)
    monkeypatch.setattr(si, "ROOT", str(tmp_path))
    return tmp_path


@pytest.fixture
def empty_root(tmp_path, monkeypatch):
    monkeypatch.setattr(si, "ROOT", str(tmp_path))
    return tmp_path
