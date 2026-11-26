import os

import sysinfo as si
from conftest import put


def test_battery_reads_units_and_batteryplus_percent(handheld):
    b = si.battery()
    assert b["present"] and b["status"] == "Discharging"
    assert b["pct"] == 57            # Knulli's learned % wins over the gauge
    assert b["kpct"] == 60
    assert b["mv"] == 3790.0 and b["ma"] == 575.0
    assert b["design_mah"] == 3200.0
    assert b["plugged"] is False


def test_battery_without_batteryplus_falls_back_to_gauge(handheld):
    os.remove(os.path.join(str(handheld), "tmp/battery.percent"))
    assert si.battery()["pct"] == 60


def test_cpu_policy_temps_freqs(handheld):
    assert si.cpu_ids() == [0, 1, 2, 3] and si.cpu_online() == 4
    assert si.cpu_mhz(2) == 1200.0
    pol = si.cpu_policy()
    assert pol["governor"] == "schedutil" and "performance" in pol["available"]
    assert pol["max_mhz"] == 1512.0
    cpu, gpu = si.cpu_gpu_temp()
    assert (cpu, gpu) == (50.6, 48.1)
    assert si.devfreq() == {"gpu": 432.0}


def test_cpu_load_delta(handheld):
    cl = si.CpuLoad()
    assert cl.sample() == (0.0, {})
    put(handheld, "/proc/stat", "cpu  200 0 200 800 0 0 0 0 0 0\n"
                                "cpu0 50 0 50 200 0 0 0 0 0 0\ncpu1 25 0 25 200 0 0 0 0 0 0\n"
                                "cpu2 25 0 25 200 0 0 0 0 0 0\ncpu3 100 0 100 200 0 0 0 0 0 0\n")
    total, per = cl.sample()
    assert total == 100.0          # only busy time was added
    assert per[0] == 100.0 and per[1] == 0.0


def test_memory_and_zram(handheld):
    m = si.memory()
    assert m["total"] == 919552 * 1024 and m["available"] == 355328 * 1024
    assert m["used"] == m["total"] - m["available"]
    z = si.zram()
    assert z["active"] and z["algo"] == "lz4"
    assert z["stored"] == 10485760 and z["in_ram"] == 4194304 and z["gained"] == 10485760 - 4194304


def test_storage_skips_virtual_and_bind_mounts(handheld):
    disks = si.storage()
    assert [d["label"] for d in disks] == ["Games"]
    assert disks[0]["total"] > 0 and 0 <= disks[0]["free"] <= disks[0]["total"]


def test_game_count_rules(handheld):
    systems = {n: (folder, exts) for n, _f, folder, exts in si.es_systems()}
    gba = si.scan_system(*systems["gba"])
    assert gba[0] == 3                         # images/ skipped, .txt and gamelist.xml not games
    psx = si.scan_system(*systems["psx"])
    assert psx[0] == 2                         # FF7 (.m3u + 2 cues) counts once, plus Tekken
    ports = si.scan_system(*systems["ports"], is_ports=True)
    assert ports[0] == 1                       # PortMaster and this app are not games
    assert si.scan_system(*systems["empty"]) == (0, 0)


def test_missing_everything_never_raises(empty_root):
    assert si.battery() == {"present": False, "status": None, "pct": None, "kpct": None, "mv": None, "ma": None,
                            "design_mah": None, "full_mah": None, "cycles": None, "temp": None, "plugged": None}
    assert si.cpu_ids() == [0]
    assert si.cpu_mhz(0) is None
    assert si.cpu_policy()["governor"] is None
    assert si.cpu_gpu_temp() == (None, None)
    assert si.memory() == {"total": None}
    assert si.zram() is None
    assert si.storage() == []
    assert si.es_systems() is None
    assert si.uptime() is None and si.brightness_pct() is None
    assert si.CpuLoad().sample() == (0.0, {})
    assert si.ProcSampler().sample() == []


def test_proc_sampler_reads_real_proc():
    """On the PC: the sampler finds this test process and gives sane numbers."""
    ps = si.ProcSampler()
    ps.sample()
    procs = ps.sample()
    me = [p for p in procs if p["me"]]
    assert me and me[0]["rss"] > 0
    assert all(p["cpu"] >= 0 for p in procs)
    merged = si.merge_procs(procs)
    assert merged[0]["me"]                     # this app always first


def test_current_unit_from_its_own_size(handheld):
    b = "/sys/class/power_supply/axp2202-battery"
    put(handheld, b + "/current_now", "575\n")             # mA while voltage is in uV
    assert si.battery()["ma"] == 575.0
    put(handheld, b + "/current_now", "-1500000\n")        # uA
    assert si.battery()["ma"] == 1500.0


def test_since_charge_not_counted_from_app_start(handheld, tmp_path):
    import time
    import netmon
    import sysmon
    sm = sysmon.SysMonitor(netmon.EventLog(str(tmp_path / "logs")), str(tmp_path / "c.json"))
    sm.tick(time.time())
    assert sm.run_start is None                             # first reading is discharging: unknown start
    put(handheld, "/sys/class/power_supply/axp2202-battery/status", "Charging\n")
    sm.tick(time.time())
    put(handheld, "/sys/class/power_supply/axp2202-battery/status", "Discharging\n")
    t = time.time()
    sm.tick(t)
    assert sm.run_start == t                                # a real unplug


def test_gamepad_battery_is_never_picked(handheld):
    d = "/sys/class/power_supply/sony_controller_battery_aa"
    put(handheld, d + "/type", "Battery\n")
    put(handheld, d + "/scope", "Device\n")
    put(handheld, d + "/capacity", "15\n")
    assert si.find_battery().endswith("axp2202-battery")


def test_current_unit_rules():
    assert si.current_ma(575000, True, "Discharging") == 575.0
    assert si.current_ma(-1500000, True, "Charging") == 1500.0
    assert si.current_ma(575, True, "Discharging") == 575.0          # driver in mA
    assert si.current_ma(3000, True, "Full") == 3.0                  # trickle in uA
    assert si.current_ma(30, False, "Full") == 30.0


def test_stale_batteryplus_percent_is_ignored(handheld):
    put(handheld, "/var/run/batteryplus.pid", "4242\n")
    assert si.battery()["pct"] == 60                                 # daemon dead: kernel gauge
    os.makedirs(os.path.join(str(handheld), "proc/4242"))
    assert si.battery()["pct"] == 57                                 # daemon alive: Knulli's value


def test_folder_signature_sees_deep_changes(handheld):
    before = si.dir_signature("/userdata/roms/gba")
    import time
    time.sleep(0.15)
    put(handheld, "/userdata/roms/gba/a/b/deep.gba", "x")
    assert si.dir_signature("/userdata/roms/gba") != before
