#!/usr/bin/env python3
"""Install Field Terminal on the handheld over SSH and retire Net Analyzer (one app, no duplicates).

    python3 tools/deploy.py HOST probe      read-only report: sensors, governors, Python, ports folder
    python3 tools/deploy.py HOST install    back up, install, carry settings over, remove Net Analyzer
    python3 tools/deploy.py HOST smb-install   the same over the Samba share (no SSH needed)
    python3 tools/deploy.py HOST smb-fetch     copy the app's sensor report, logs and battery log to this PC

Needs key login once:  ssh-copy-id root@HOST   (Knulli's default password is "linux").

What `install` changes on the handheld, in order (it stops at the first error):
  1. copies ports/gamelist.xml and the old netanalyzer folder to ../card-backups/ on this PC
  2. unpacks the new app into /userdata/roms/ports/.fieldterminal-new, checks every .py compiles there
  3. keeps your config.json / captures / logs from an existing fieldterminal folder, or the button layout
     and captures from netanalyzer, then swaps the folders
  4. writes "Field Terminal.sh"
  5. replaces the Net Analyzer entry in ports/gamelist.xml with Field Terminal (validated XML, atomic swap)
  6. deletes exactly two paths: ports/netanalyzer and "ports/Net Analyzer.sh"
  7. asks EmulationStation to reload its game lists (local web API); otherwise: Menu > Update gamelists
"""
import io
import os
import re
import shutil
import subprocess
import sys
import tarfile
import time
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.normpath(os.path.join(HERE, "..", "ports"))
BACKUPS = os.path.normpath(os.path.join(HERE, "..", "..", "card-backups"))
PORTS = "/userdata/roms/ports"
SKIP = {"__pycache__", "logs", "captures", "cache.json", "config.json", "log.txt"}


def ssh_base(host):
    ctl = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), f"ft-ssh-{host}")
    key = os.path.expanduser("~/.ssh/id_ed25519")
    return ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "-o", "StrictHostKeyChecking=accept-new",
            "-o", "IdentityAgent=none", "-o", "IdentitiesOnly=yes", "-i", key,
            "-o", "ControlMaster=auto", "-o", f"ControlPath={ctl}", "-o", "ControlPersist=60", f"root@{host}"]


def run(host, cmd, data=None, check=True):
    r = subprocess.run(ssh_base(host) + [cmd], input=data, capture_output=True, timeout=120)
    out = r.stdout.decode(errors="replace")
    if check and r.returncode != 0:
        sys.exit(f"FAILED ({r.returncode}): {cmd}\n{out}{r.stderr.decode(errors='replace')}")
    return out


def probe(host):
    print(run(host, r"""
echo "== board";  cat /boot/boot/knulli.board 2>/dev/null; uname -r
echo "== python"; python3 -c 'import sys, pygame; print(sys.version.split()[0], pygame.version.ver)' 2>&1
echo "== power_supply"
for d in /sys/class/power_supply/*; do echo "$d: type=$(cat $d/type 2>/dev/null) status=$(cat $d/status 2>/dev/null) cap=$(cat $d/capacity 2>/dev/null) uV=$(cat $d/voltage_now 2>/dev/null) uA=$(cat $d/current_now 2>/dev/null) design=$(cat $d/charge_full_design 2>/dev/null) online=$(cat $d/online 2>/dev/null)"; done
echo "batteryplus %: $(cat /tmp/battery.percent 2>/dev/null)"
echo "== thermal"; for z in /sys/class/thermal/thermal_zone*; do echo "$z $(cat $z/type) $(cat $z/temp)"; done
echo "== devfreq"; for d in /sys/class/devfreq/*; do echo "$d $(cat $d/cur_freq 2>/dev/null)"; done
echo "== cpufreq"; P=/sys/devices/system/cpu/cpufreq/policy0; echo "gov=$(cat $P/scaling_governor) avail=$(cat $P/scaling_available_governors) max=$(cat $P/scaling_max_freq)"
echo "== memory"; grep -E 'MemTotal|MemAvailable|Swap' /proc/meminfo; cat /proc/swaps; ls /sys/block | grep zram
echo "== mounts"; grep -E '^/dev/' /proc/mounts
echo "== services"; knulli-settings-get system.services
echo "== ports"; ls -la /userdata/roms/ports | head -40
echo "== es api"; (curl -s -m 3 http://127.0.0.1:1234/caps || wget -q -T 3 -O - http://127.0.0.1:1234/caps) 2>&1 | head -c 300; echo
"""))
    print("== Field Terminal readers on this handheld (read only, from /tmp)")
    with open(os.path.join(SRC, "fieldterminal", "sysinfo.py"), "rb") as f:
        run(host, "cat > /tmp/ft_sysinfo.py", data=f.read())
    print(run(host, r"""cd /tmp && python3 -c '
import time, ft_sysinfo as si
b = si.battery(); print("battery  ", b)
print("policy   ", si.cpu_policy())
print("temps    ", si.cpu_gpu_temp(), si.thermal_zones())
print("devfreq  ", si.devfreq())
m = si.memory(); print("memory MB", {k: round(v / 1048576) for k, v in m.items() if v})
print("zram     ", si.zram())
print("storage  ", [(d["label"], d["mount"], d["fs"], round(d["total"] / 1e9, 1)) for d in si.storage()])
es = si.es_systems(); print("systems  ", len(es or []), "in es_systems.cfg")
c = si.CpuLoad(); c.sample(); time.sleep(1); print("cpu load ", c.sample())
ok = b["present"] and b["mv"] and 3000 < b["mv"] < 4500 and (b["ma"] is None or b["ma"] < 5000)
print("SANE" if ok else "CHECK THESE NUMBERS")
'; rm -f /tmp/ft_sysinfo.py""", check=False))


def pack():
    """tar.gz of the app (no logs, caches or captures from this PC)."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        app = os.path.join(SRC, "fieldterminal")
        for dirpath, dirnames, files in os.walk(app):
            dirnames[:] = [d for d in dirnames if d not in SKIP]
            for fn in files:
                if fn in SKIP or fn.endswith(".pyc"):
                    continue
                full = os.path.join(dirpath, fn)
                arc = os.path.relpath(full, SRC)
                info = tar.gettarinfo(full, arc)
                if fn == "fieldterminal_log" or fn.endswith(".sh"):
                    info.mode = 0o755
                with open(full, "rb") as f:
                    tar.addfile(info, f)
        tar.add(os.path.join(SRC, "Field Terminal.sh"), "Field Terminal.sh")
    return buf.getvalue()


def new_gamelist(text):
    """Replace the Net Analyzer (or an older Field Terminal) entry with the current one."""
    entry = open(os.path.join(SRC, "fieldterminal", "gameinfo.xml")).read()
    game = re.search(r"<game>.*?</game>", entry, re.S).group(0)
    game = "\n".join("\t" + line.strip() if i in (0,) or line.strip().startswith("</game") else "\t\t" + line.strip()
                     for i, line in enumerate(game.splitlines()))
    pat = re.compile(r"[ \t]*<game(?:\s[^>]*)?>(?:(?!</game>).)*?<path>\./(?:Net Analyzer|Field Terminal)\.sh</path>"
                     r".*?</game>\n?", re.S)
    out, n = pat.subn("", text)
    out = out.replace("</gameList>", game + "\n</gameList>", 1)
    root = ET.fromstring(out.encode())
    paths = [g.findtext("path") for g in root.iter("game")]
    assert paths.count("./Field Terminal.sh") == 1 and "./Net Analyzer.sh" not in paths, paths
    return out, n


def install(host):
    ts = time.strftime("%Y%m%d-%H%M%S")
    os.makedirs(BACKUPS, exist_ok=True)
    print("1. checking the handheld")
    run(host, f"test -d {PORTS} && python3 -c 'import pygame'")
    print("   backing up gamelist.xml and netanalyzer/ to", BACKUPS)
    gl = run(host, f"cat {PORTS}/gamelist.xml", check=False)
    if gl.strip():
        with open(os.path.join(BACKUPS, f"ports-gamelist-{ts}.xml"), "w") as f:
            f.write(gl)
    old = subprocess.run(ssh_base(host) + [f"cd {PORTS} && [ -d netanalyzer ] && tar czf - netanalyzer 'Net Analyzer.sh' "
                                           f"2>/dev/null"], capture_output=True, timeout=120).stdout
    if old:
        with open(os.path.join(BACKUPS, f"netanalyzer-card-{ts}.tar.gz"), "wb") as f:
            f.write(old)

    print("2. unpacking the new version")
    stage = f"{PORTS}/.fieldterminal-new"
    run(host, f"rm -rf '{stage}' && mkdir -p '{stage}' && tar xzf - -C '{stage}'", data=pack())
    run(host, f"cd '{stage}/fieldterminal' && python3 -c \"import py_compile, glob; "
              f"[py_compile.compile(p, doraise=True, cfile='/tmp/ft-check.pyc') for p in glob.glob('*.py')]\"")

    print("3. keeping settings and captures, swapping folders")
    run(host, f"""set -e
cd {PORTS}
N='{stage}/fieldterminal'
if [ -d fieldterminal ]; then
  for f in config.json cache.json captures logs; do
    if [ -e "fieldterminal/$f" ]; then rm -rf "$N/$f"; mv "fieldterminal/$f" "$N/$f"; fi
  done
elif [ -d netanalyzer ]; then
  if [ -f netanalyzer/config.json ]; then cp netanalyzer/config.json "$N/"; fi
  if [ -d netanalyzer/captures ]; then mkdir -p "$N/captures"; cp -r netanalyzer/captures/. "$N/captures/"; fi
fi
rm -rf fieldterminal.old
if [ -d fieldterminal ]; then mv fieldterminal fieldterminal.old; fi
mv "$N" fieldterminal
rm -rf fieldterminal.old""")

    print("4. launcher")
    run(host, f"cd {PORTS} && mv '{stage}/Field Terminal.sh' 'Field Terminal.sh' && chmod +x 'Field Terminal.sh' "
              f"&& rmdir '{stage}'")

    print("5. game list entry")
    if gl.strip():
        text, replaced = new_gamelist(gl)
        run(host, f"cat > {PORTS}/gamelist.xml.ft-new && [ $(wc -c < {PORTS}/gamelist.xml.ft-new) = {len(text.encode())} ] "
                  f"&& python3 -c 'import xml.etree.ElementTree as E; E.parse(\"{PORTS}/gamelist.xml.ft-new\")' "
                  f"&& mv {PORTS}/gamelist.xml.ft-new {PORTS}/gamelist.xml", data=text.encode())
        print(f"   {'replaced' if replaced else 'added'} the entry")

    print("6. removing Net Analyzer")
    run(host, f"cd {PORTS} && rm -rf netanalyzer 'Net Analyzer.sh'")

    print("7. reloading the game lists")
    r = subprocess.run(ssh_base(host) + ["curl -s -m 5 -o /dev/null -w '%{http_code}' http://127.0.0.1:1234/reloadgames"],
                       capture_output=True, timeout=30)
    code = r.stdout.decode().strip()
    print("   EmulationStation reloaded" if code.startswith("2") else
          f"   no answer from EmulationStation ({code or r.returncode}): Menu > Game settings > Update gamelists")
    verify(host)


def verify(host):
    """Check the result on the handheld; prints OK/FAIL per item."""
    out = run(host, f"""cd {PORTS}
[ -d fieldterminal ] && [ -f 'Field Terminal.sh' ] && echo has_app
[ ! -e netanalyzer ] && [ ! -e 'Net Analyzer.sh' ] && echo old_gone
[ "$(grep -c '<path>./Field Terminal.sh</path>' gamelist.xml)" = 1 ] && echo one_entry
grep -q 'Net Analyzer.sh' gamelist.xml || echo no_old_entry
python3 -c 'import xml.etree.ElementTree as E; E.parse("gamelist.xml")' && echo xml_ok
""", check=False).split()
    for key, what in (("has_app", "Field Terminal installed"), ("old_gone", "Net Analyzer files removed"),
                      ("one_entry", "exactly one Field Terminal menu entry"), ("no_old_entry", "no Net Analyzer entry"),
                      ("xml_ok", "gamelist.xml is valid XML")):
        print(f"   {'OK  ' if key in out else 'FAIL'} {what}")
    print("   (the battery logger is installed by the app on its first start: check POWER > TIMES)")


# =========================================================================== over Samba (no SSH)

SMB_USER = os.environ.get("FT_SMB_USER", "root%linux")      # Knulli's default share login


def smb(host, cmds, check=True):
    r = subprocess.run(["smbclient", f"//{host}/share", "-U", SMB_USER, "-m", "SMB3", "-c", cmds],
                       capture_output=True, timeout=600)
    out = r.stdout.decode(errors="replace") + r.stderr.decode(errors="replace")
    bad = [line for line in out.splitlines() if "NT_STATUS" in line and "NT_STATUS_OBJECT_NAME_COLLISION" not in line]
    if check and (r.returncode != 0 or bad):
        sys.exit(f"FAILED: {cmds}\n{out}")
    return out


def smb_ls(host, path):
    """{name: size} of one folder on the share."""
    out = smb(host, f'cd "{path}"; ls', check=False)
    files = {}
    for line in out.splitlines():
        m = re.match(r"^\s{2}(.+?)\s+([ADHNRS]*)\s+(\d+)\s+\w{3} \w{3}\s+\d+ ", line)
        if m and m.group(1) not in (".", ".."):
            files[m.group(1)] = (int(m.group(3)), "D" in m.group(2))
    return files


def smb_install(host):
    ts = time.strftime("%Y%m%d-%H%M%S")
    ports = "roms/ports"
    print("1. backing up gamelist.xml and Net Analyzer from the handheld")
    bdir = os.path.join(BACKUPS, f"netanalyzer-card-{ts}")
    os.makedirs(bdir)
    have = smb_ls(host, ports)
    if "gamelist.xml" not in have:
        sys.exit("no roms/ports/gamelist.xml on the share: stopping")
    smb(host, f'cd {ports}; lcd "{bdir}"; get gamelist.xml')
    if "Net Analyzer.sh" in have:
        smb(host, f'cd {ports}; lcd "{bdir}"; get "Net Analyzer.sh"')
    if "netanalyzer" in have:
        smb(host, f'cd {ports}; lcd "{bdir}"; recurse ON; prompt OFF; mget netanalyzer')
    shutil.copy(os.path.join(bdir, "gamelist.xml"), os.path.join(BACKUPS, f"ports-gamelist-{ts}.xml"))
    print("   saved in", bdir)

    print("2. preparing the files")
    stage = os.path.join(os.environ.get("TMPDIR", "/tmp"), f"ft-stage-{ts}")
    os.makedirs(stage)
    with tarfile.open(fileobj=io.BytesIO(pack()), mode="r:gz") as tar:
        tar.extractall(stage, filter="data")
    app = os.path.join(stage, "fieldterminal")
    if "fieldterminal" in have:                      # an update: keep its settings, cache, captures, logs
        smb(host, f'cd {ports}/fieldterminal; lcd "{app}"; prompt OFF; mget config.json; mget cache.json', check=False)
        smb(host, f'cd {ports}/fieldterminal; lcd "{app}"; recurse ON; prompt OFF; mget captures; mget logs', check=False)
    else:                                            # first install: button layout and captures from Net Analyzer
        old = os.path.join(bdir, "netanalyzer")
        if os.path.exists(os.path.join(old, "config.json")):
            shutil.copy(os.path.join(old, "config.json"), app)
        if os.path.isdir(os.path.join(old, "captures")):
            shutil.copytree(os.path.join(old, "captures"), os.path.join(app, "captures"), dirs_exist_ok=True)

    print("3. uploading Field Terminal")
    smb(host, f'cd {ports}; lcd "{stage}"; recurse ON; prompt OFF; mkdir fieldterminal; mput fieldterminal; '
              f'put "Field Terminal.sh"', check=True)
    missing = []
    for dirpath, _dirs, files in os.walk(app):
        rel = os.path.relpath(dirpath, stage)
        remote = smb_ls(host, f"{ports}/{rel}")
        for fn in files:
            size = os.path.getsize(os.path.join(dirpath, fn))
            if remote.get(fn, (None,))[0] != size:
                missing.append(f"{rel}/{fn}")
    if missing:
        sys.exit(f"upload incomplete, Net Analyzer left in place: {missing}")
    print("   all files arrived with the right size")

    print("4. game list entry")
    text, replaced = new_gamelist(open(os.path.join(bdir, "gamelist.xml")).read())
    newgl = os.path.join(stage, "gamelist.xml")
    with open(newgl, "w") as f:
        f.write(text)
    check = os.path.join(stage, "gamelist.check.xml")
    for target in ("gamelist.xml.ft-new", "gamelist.xml"):   # a test copy first, then the real one
        smb(host, f'cd {ports}; lcd "{stage}"; put gamelist.xml "{target}"; get "{target}" gamelist.check.xml')
        if open(check, "rb").read() != open(newgl, "rb").read():
            smb(host, f'cd {ports}; lcd "{bdir}"; put gamelist.xml', check=False)     # put the original back
            sys.exit(f"gamelist.xml did not arrive intact ({target}); original restored, Net Analyzer left in place")
    smb(host, f'cd {ports}; del gamelist.xml.ft-new')
    print(f"   {'replaced Net Analyzer with' if replaced else 'added'} Field Terminal")

    print("5. removing Net Analyzer")
    if "netanalyzer" in have:
        smb(host, f"cd {ports}; deltree netanalyzer")
    if "Net Analyzer.sh" in have:
        smb(host, f'cd {ports}; del "Net Analyzer.sh"')
    smb_verify(host)
    print("\nOn the handheld: Menu > Game settings > Update gamelists, then open Ports > Field Terminal.")


def smb_verify(host):
    have = smb_ls(host, "roms/ports")
    tmp = os.path.join(os.environ.get("TMPDIR", "/tmp"), "ft-verify-gamelist.xml")
    smb(host, f'cd roms/ports; get gamelist.xml "{tmp}"')
    paths = [g.findtext("path") for g in ET.parse(tmp).getroot().iter("game")]
    for ok, what in (("fieldterminal" in have and "Field Terminal.sh" in have, "Field Terminal installed"),
                     ("netanalyzer" not in have and "Net Analyzer.sh" not in have, "Net Analyzer files removed"),
                     (paths.count("./Field Terminal.sh") == 1, "exactly one Field Terminal menu entry"),
                     ("./Net Analyzer.sh" not in paths, "no Net Analyzer entry")):
        print(f"   {'OK  ' if ok else 'FAIL'} {what}")


def smb_fetch(host):
    """Bring the app's sensor report, logs and the battery log to ../card-backups/fetched-<time>/."""
    out = os.path.join(BACKUPS, time.strftime("fetched-%Y%m%d-%H%M%S"))
    os.makedirs(out)
    smb(host, f'cd roms/ports/fieldterminal; lcd "{out}"; prompt OFF; recurse ON; mget logs; mget log.txt; '
              f'mget config.json', check=False)
    smb(host, f'cd system; lcd "{out}"; prompt OFF; recurse ON; mget fieldterminal; mget services', check=False)
    print("fetched to", out)
    for dirpath, _d, files in os.walk(out):
        for fn in files:
            print("  ", os.path.relpath(os.path.join(dirpath, fn), out))


if __name__ == "__main__":
    cmds = {"probe": probe, "install": install, "verify": verify,
            "smb-install": smb_install, "smb-verify": smb_verify, "smb-fetch": smb_fetch}
    if len(sys.argv) != 3 or sys.argv[2] not in cmds:
        sys.exit(__doc__)
    cmds[sys.argv[2]](sys.argv[1])
