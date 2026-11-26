"""Field Terminal - the ONLY module that changes anything on the handheld.

Everything it may do, and nothing else:
  * CPU mode: write a governor the kernel itself lists (scaling_available_governors) to
    /sys/devices/system/cpu/cpufreq/policy*/scaling_governor, and save it with
    `knulli-settings-set system.cpu.governor` (the same setting the Knulli menu writes).
    Speed limits, overclock and voltages are never touched.
  * ZRAM: `knulli-services enable|disable|start|stop zramswap` (Knulli's own service).
    Stopping it now is refused unless the RAM can take back everything stored in it.
  * Battery logger: copy service/fieldterminal_log to /userdata/system/services/ and
    `knulli-services enable|disable|start|stop fieldterminal_log`; remove that one file.

Writes are checked against ALLOWED_WRITES and commands against ALLOWED_CMDS before running.
"""
import filecmp
import os
import re
import shutil
import subprocess

import sysinfo as si

BASE = os.path.dirname(os.path.abspath(__file__))
SERVICE_NAME = "fieldterminal_log"
SERVICE_SRC = os.path.join(BASE, "service", SERVICE_NAME)
SERVICE_DST = "/userdata/system/services/" + SERVICE_NAME
PIDFILE = "/var/run/fieldterminal_log.pid"

ALLOWED_WRITES = (
    re.compile(r"^/sys/devices/system/cpu/cpufreq/policy\d+/scaling_governor$"),
    re.compile(r"^/sys/devices/system/cpu/cpu\d+/cpufreq/scaling_governor$"),
    re.compile(r"^/userdata/system/services/fieldterminal_log$"),
)
ALLOWED_CMDS = {
    ("knulli-services", "enable"), ("knulli-services", "disable"),
    ("knulli-services", "start"), ("knulli-services", "stop"),
    ("knulli-settings-set", "system.cpu.governor"),
    ("knulli-settings-get", "system.services"),
}
ALLOWED_SERVICES = {"zramswap", SERVICE_NAME}

MODES = [
    ("Saver", "uses less battery", ("conservative", "powersave")),
    ("Normal", "speeds up when needed", ("schedutil", "ondemand", "interactive")),
    ("Maximum", "always at full speed", ("performance",)),
]
ZRAM_SAFETY = 64 * 1048576


class Refused(Exception):
    pass


def _check_write(path):
    if not any(p.match(path) for p in ALLOWED_WRITES):
        raise Refused(f"not allowed to write {path}")


def _bin(name):
    d = os.environ.get("FT_BIN")
    if d:
        p = os.path.join(d, name)
        return p if os.path.exists(p) else None
    return shutil.which(name) or (f"/usr/bin/{name}" if os.path.exists(f"/usr/bin/{name}") else None)


def _cmd(name, *args, timeout=20):
    if (name, args[0] if args else "") not in ALLOWED_CMDS:
        raise Refused(f"command not allowed: {name} {' '.join(args)}")
    if name == "knulli-services" and (len(args) != 2 or args[1] not in ALLOWED_SERVICES):
        raise Refused(f"service not allowed: {args}")
    exe = _bin(name)
    if not exe:
        return -1, f"{name} not found"
    try:
        r = subprocess.run(["bash", exe, *args] if not os.access(exe, os.X_OK) else [exe, *args],
                           capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout + r.stderr).strip()
    except (OSError, subprocess.SubprocessError) as exc:
        return -1, str(exc)


# --------------------------------------------------------------------------- CPU mode

def mode_of(governor):
    for i, (_name, _desc, govs) in enumerate(MODES):
        if governor in govs:
            return i
    return None


def governor_for(mode, available):
    for g in MODES[mode][2]:
        if g in available:
            return g
    return None


def set_mode(mode):
    """-> (ok, message). Applies now on every CPU policy and saves it for the next boot."""
    pol = si.cpu_policy()
    gov = governor_for(mode, pol["available"])
    if not gov:
        return False, f"{MODES[mode][0]} is not offered by this kernel"
    paths = [f"/sys/devices/system/cpu/cpufreq/{n}/scaling_governor"
             for n in si.listdir("/sys/devices/system/cpu/cpufreq") if re.match(r"policy\d+$", n)]
    if not paths:
        paths = [f"/sys/devices/system/cpu/cpu{c}/cpufreq/scaling_governor" for c in si.cpu_ids()
                 if os.path.exists(si.P(f"/sys/devices/system/cpu/cpu{c}/cpufreq/scaling_governor"))]
    done = 0
    for p in paths:
        _check_write(p)
        try:
            with open(si.P(p), "w") as f:
                f.write(gov)
            done += 1
        except OSError as exc:
            return False, f"could not set {gov}: {exc.strerror}"
    if not done:
        return False, "no CPU policy found"
    rc, out = _cmd("knulli-settings-set", "system.cpu.governor", gov)
    saved = rc == 0
    return True, f"{MODES[mode][0]} ({gov})" + ("" if saved else ", not saved for next boot")


# --------------------------------------------------------------------------- services

def enabled_services():
    rc, out = _cmd("knulli-settings-get", "system.services")
    return set(out.split()) if rc == 0 else set()


def zram_boot_enabled():
    return "zramswap" in enabled_services()


def zram_set_boot(on):
    rc, out = _cmd("knulli-services", "enable" if on else "disable", "zramswap")
    return rc == 0, out


def zram_start():
    rc, out = _cmd("knulli-services", "start", "zramswap", timeout=30)
    return rc == 0, out


def zram_can_stop(mem, z):
    """Turning ZRAM off moves everything in it back to RAM: only allowed when that clearly fits."""
    if not z or not z.get("active"):
        return True
    return (mem.get("available") or 0) > z.get("stored", 0) + ZRAM_SAFETY


def zram_stop(mem=None, z=None):
    """Re-reads memory and ZRAM right now (a confirm dialog may have been open for a while)."""
    mem, z = si.memory(), si.zram()
    if not zram_can_stop(mem, z):
        raise Refused("ZRAM holds more than free RAM can take back; it turns off at the next boot")
    rc, out = _cmd("knulli-services", "stop", "zramswap", timeout=60)
    return rc == 0, out


# --------------------------------------------------------------------------- battery logger

def logger_installed():
    return os.path.exists(si.P(SERVICE_DST))


def logger_current():
    """Installed copy is the same as the one shipped with this version of the app."""
    try:
        return filecmp.cmp(SERVICE_SRC, si.P(SERVICE_DST), shallow=False)
    except OSError:
        return False


def logger_running():
    pid = si.read(PIDFILE)
    if not pid or not pid.isdigit():
        return False
    return SERVICE_NAME in (si.read(f"/proc/{pid}/cmdline") or "")


def logger_install():
    """Install or update the logger, enable it at boot and start it now. -> (ok, message)."""
    _check_write(SERVICE_DST)
    changed = not logger_current()
    if changed:
        dst = si.P(SERVICE_DST)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        tmp = dst + ".new"
        shutil.copyfile(SERVICE_SRC, tmp)
        os.chmod(tmp, 0o755)
        os.replace(tmp, dst)
    rc, out = _cmd("knulli-services", "enable", SERVICE_NAME)
    if rc != 0:
        return False, out or "could not enable"
    if changed and logger_running():
        _cmd("knulli-services", "stop", SERVICE_NAME)
    if not logger_running():
        _cmd("knulli-services", "start", SERVICE_NAME)
    return True, "updated" if changed else "on"


def logger_remove():
    """Stop, disable and delete the service file. The battery history files stay."""
    _check_write(SERVICE_DST)
    _cmd("knulli-services", "stop", SERVICE_NAME)
    _cmd("knulli-services", "disable", SERVICE_NAME)
    try:
        os.remove(si.P(SERVICE_DST))
    except FileNotFoundError:
        pass
    except OSError as exc:
        return False, f"could not delete the service file: {exc.strerror}"
    return True, "removed"
