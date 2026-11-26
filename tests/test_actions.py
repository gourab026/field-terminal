"""The only code that changes the handheld: check it does exactly what it says and nothing else."""
import os
import stat

import pytest

import actions
from conftest import put


@pytest.fixture
def fake_bins(tmp_path, monkeypatch):
    """knulli-* commands that only record how they were called."""
    d = tmp_path / "bin"
    d.mkdir()
    calls = tmp_path / "calls.txt"
    for name in ("knulli-services", "knulli-settings-set", "knulli-settings-get"):
        p = d / name
        body = f'#!/bin/bash\necho "{name} $*" >> "{calls}"\n'
        if name == "knulli-settings-get":
            body += 'echo "ssh zramswap"\n'
        p.write_text(body)
        p.chmod(p.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("FT_BIN", str(d))
    return calls


def gov(root):
    return open(os.path.join(str(root), "sys/devices/system/cpu/cpufreq/policy0/scaling_governor")).read()


def test_set_mode_writes_only_offered_governor_and_saves(handheld, fake_bins):
    ok, msg = actions.set_mode(0)
    assert ok and gov(handheld) == "conservative"
    assert "knulli-settings-set system.cpu.governor conservative" in fake_bins.read_text()
    ok, _ = actions.set_mode(2)
    assert ok and gov(handheld) == "performance"


def test_set_mode_refuses_governor_not_offered(handheld, fake_bins):
    put(handheld, "/sys/devices/system/cpu/cpufreq/policy0/scaling_available_governors", "schedutil\n")
    ok, msg = actions.set_mode(2)
    assert not ok and "not offered" in msg
    assert gov(handheld).strip() == "schedutil"


def test_write_allowlist():
    actions._check_write("/sys/devices/system/cpu/cpufreq/policy0/scaling_governor")
    actions._check_write("/userdata/system/services/fieldterminal_log")
    for bad in ("/sys/devices/system/cpu/cpufreq/policy0/scaling_max_freq", "/boot/knulli-boot.conf",
                "/userdata/system/knulli.conf", "/userdata/system/services/zramswap", "/etc/passwd",
                "/userdata/system/services/../knulli.conf"):
        with pytest.raises(actions.Refused):
            actions._check_write(bad)


def test_command_allowlist(fake_bins):
    with pytest.raises(actions.Refused):
        actions._cmd("knulli-services", "enable", "samba")
    with pytest.raises(actions.Refused):
        actions._cmd("knulli-settings-set", "system.cpu.maxfreq", "2000000")
    with pytest.raises(actions.Refused):
        actions._cmd("rm", "-rf", "/")
    assert actions._cmd("knulli-services", "enable", "zramswap")[0] == 0


def test_zram_stop_guard(handheld, fake_bins):
    mem = {"available": 300 * 1048576}
    assert actions.zram_can_stop(mem, {"active": True, "stored": 100 * 1048576})
    assert not actions.zram_can_stop(mem, {"active": True, "stored": 280 * 1048576})
    assert actions.zram_can_stop(mem, None)
    # zram_stop judges by the numbers *now*, not the ones shown when the dialog opened
    put(handheld, "/sys/block/zram0/mm_stat", f"{400 * 1048576} {100 * 1048576} {120 * 1048576} 0 0 0 0 0 0\n")
    with pytest.raises(actions.Refused):
        actions.zram_stop({"available": 10 ** 12}, {"active": False})
    assert "zramswap" not in fake_bins.read_text() if fake_bins.exists() else True
    put(handheld, "/sys/block/zram0/mm_stat", "10485760 3145728 4194304 0 4194304 0 0 0 0\n")
    ok, _ = actions.zram_stop()
    assert ok and "knulli-services stop zramswap" in fake_bins.read_text()


def test_zram_boot_reads_services(handheld, fake_bins):
    assert actions.zram_boot_enabled() is True


def test_logger_install_and_remove(handheld, fake_bins):
    assert not actions.logger_installed()
    ok, msg = actions.logger_install()
    assert ok and msg == "updated"
    dst = os.path.join(str(handheld), "userdata/system/services/fieldterminal_log")
    assert os.path.exists(dst) and os.access(dst, os.X_OK) and actions.logger_current()
    calls = fake_bins.read_text()
    assert "knulli-services enable fieldterminal_log" in calls and "knulli-services start fieldterminal_log" in calls
    ok, msg = actions.logger_install()
    assert ok and msg == "on"                          # same version: file not rewritten
    actions.logger_remove()
    assert not os.path.exists(dst)
    assert "knulli-services disable fieldterminal_log" in fake_bins.read_text()
    # nothing else was created next to it
    assert os.listdir(os.path.dirname(dst)) == []
