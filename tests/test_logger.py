"""The boot logger script, run for real with bash against the fake handheld."""
import os
import subprocess
import time

import battery as bt
from conftest import APP

SCRIPT = os.path.realpath(os.path.join(APP, "service", "fieldterminal_log"))


def env_for(root, data, run, interval="0.2", flush="3"):
    e = dict(os.environ, FT_SYSROOT=str(root), FT_DATA=str(data), FT_RUN=str(run), FT_INTERVAL=interval,
             FT_FLUSH=flush)
    return e


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def no_leftovers():
    yield
    subprocess.run(["pkill", "-f", f"{SCRIPT} loop"])


def sh(args, env, timeout=10):
    return subprocess.run(["bash", SCRIPT, *args], env=env, capture_output=True, text=True, timeout=timeout)


def test_service_name_is_valid_for_knulli():
    import re
    assert re.match(r"^[_A-Za-z][_A-Za-z0-9]*$", os.path.basename(SCRIPT))


def test_start_logs_lines_and_stop_flushes_fast(handheld, tmp_path):
    data, run = tmp_path / "data", tmp_path / "run"
    env = env_for(handheld, data, run)
    t0 = time.time()
    assert sh(["start"], env).returncode == 0
    assert time.time() - t0 < 3, "start must return at once"
    assert "running" in sh(["status"], env).stdout
    assert sh(["start"], env).returncode == 0          # second start: no second copy
    pids = subprocess.run(["pgrep", "-f", f"{SCRIPT} loop"], capture_output=True, text=True).stdout.split()
    assert len(pids) == 1
    time.sleep(1.5)
    t0 = time.time()
    sh(["stop"], env)
    assert time.time() - t0 < 2.5, "stop must not delay shutdown"
    assert "stopped" in sh(["status"], env).stdout
    files = list(data.glob("battery-*.csv"))
    assert len(files) == 1
    lines = files[0].read_text().splitlines()
    assert lines[0].startswith("# Field Terminal battery log v1")
    rows = [bt.parse_line(line) for line in lines[1:]]
    assert len(rows) >= 4 and all(rows)
    r = rows[0]
    assert r.status == "Discharging" and r.mv == 3790 and r.ma == 575 and r.pct == 57 and r.kpct == 60
    assert r.temp == 50.6 and r.bright == 31 and r.up == 4320


def test_no_battery_means_no_logging(empty_root, tmp_path):
    data, run = tmp_path / "data", tmp_path / "run"
    env = env_for(empty_root, data, run)
    sh(["start"], env)
    time.sleep(0.5)
    assert "stopped" in sh(["status"], env).stdout
    assert not data.exists()


def test_rotation_keeps_six_months(handheld, tmp_path):
    data, run = tmp_path / "data", tmp_path / "run"
    data.mkdir()
    for m in range(1, 10):
        (data / f"battery-2025{m:02d}.csv").write_text("x\n")
    env = env_for(handheld, data, run, flush="1")
    sh(["start"], env)
    time.sleep(0.8)
    sh(["stop"], env)
    files = sorted(p.name for p in data.glob("battery-*.csv"))
    assert len(files) == 6 and files[-1].startswith(time.strftime("battery-%Y%m"))


def test_logger_ignores_gamepad_battery(handheld, tmp_path):
    from conftest import put
    d = "/sys/class/power_supply/zz_gamepad_battery"
    put(handheld, d + "/type", "Battery\n")
    put(handheld, d + "/scope", "Device\n")
    put(handheld, d + "/voltage_now", "3000000\n")
    data, run = tmp_path / "data", tmp_path / "run"
    env = env_for(handheld, data, run, flush="1")
    sh(["start"], env)
    time.sleep(0.6)
    sh(["stop"], env)
    rows = [bt.parse_line(l) for l in next(data.glob("battery-*.csv")).read_text().splitlines()[1:]]
    assert rows and all(r.mv == 3790 for r in rows)


def test_two_starts_at_once_give_one_logger(handheld, tmp_path):
    data, run = tmp_path / "data", tmp_path / "run"
    env = env_for(handheld, data, run)
    a = subprocess.Popen(["bash", SCRIPT, "start"], env=env)
    b = subprocess.Popen(["bash", SCRIPT, "start"], env=env)
    a.wait(10)
    b.wait(10)
    time.sleep(1.0)
    pids = subprocess.run(["pgrep", "-f", f"{SCRIPT} loop"], capture_output=True, text=True).stdout.split()
    assert len(pids) == 1, pids
    sh(["stop"], env)
    assert not (run / "fieldterminal_log.lock").exists()


def test_stale_lock_from_a_killed_logger_is_recovered(handheld, tmp_path):
    data, run = tmp_path / "data", tmp_path / "run"
    (run / "fieldterminal_log.lock").mkdir(parents=True)
    (run / "fieldterminal_log.pid").write_text("999999\n")
    env = env_for(handheld, data, run)
    sh(["start"], env)
    time.sleep(1.0)
    assert "running" in sh(["status"], env).stdout
    sh(["stop"], env)
