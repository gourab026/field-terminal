"""Every screen draws without errors: with full demo data, with a real sampler on the fake
handheld, and with a real sampler on an empty machine (no sensors at all)."""
import time

import pytest

import main as M
import sysmon


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setattr(M, "CONF_PATH", str(tmp_path / "config.json"))
    monkeypatch.setattr(M, "CACHE_PATH", str(tmp_path / "cache.json"))
    monkeypatch.setattr(M, "LOG_DIR", str(tmp_path / "logs"))
    a = M.App(demo=True, screens=str(tmp_path))
    yield a
    a.shutdown()


def draw_all(a):
    for i in range(len(a.pages)):
        a.switch(i)
        a.draw()
        if a.page.name == "NET":
            for p in range(5):
                a.page.page = p
                a.draw()
    a.switch(0)


def test_demo_draws_every_screen(app):
    time.sleep(0.3)
    draw_all(app)


def real_sampler(app, tmp_path):
    app.sm.stop_ev.set()
    sm = sysmon.SysMonitor(app.log, str(tmp_path / "cache2.json"))
    sm.want_disk = sm.want_procs = True
    sm.tick(time.time())
    sm.tick(time.time() + 1.1)
    sm.scan_games()
    sm.refresh_analysis()
    app.sm = sm
    return sm


def test_real_sampler_on_fake_handheld(app, handheld, tmp_path):
    sm = real_sampler(app, tmp_path)
    assert sm.bat["pct"] == 57 and sm.cpu_temp == 50.6 and sm.games["total"] == 6
    draw_all(app)


def test_real_sampler_with_no_sensors(app, empty_root, tmp_path):
    sm = real_sampler(app, tmp_path)
    assert sm.bat == {"present": False, "status": None, "pct": None, "kpct": None, "mv": None, "ma": None,
                      "design_mah": None, "full_mah": None, "cycles": None, "temp": None, "plugged": None}
    draw_all(app)


def test_buttons_move_between_tabs_and_pages(app):
    assert app.page.name == "DASH"
    app.handle("r1")
    assert app.page.name == "NET"
    app.handle("right")                      # inside NET: next page, not next tab
    assert app.page.name == "NET" and app.page.page == 1
    app.handle("r1")
    assert app.page.name == "POWER"
    app.handle("l1")
    app.handle("l1")
    assert app.page.name == "DASH"
    app.handle("left")
    assert app.page.name == "LOG"


def test_capture_runs_only_on_net(app):
    assert not app.cap.running
    app.switch(1)
    time.sleep(0.1)
    assert app.cap.running and app.mon.active
    app.switch(2)
    time.sleep(0.4)
    assert not app.cap.running and not app.mon.active


def test_maximum_mode_asks_first(app):
    app.switch(3)
    app.handle("down")                        # Normal -> Maximum
    app.handle("a")
    assert app.confirm and "full speed" in app.confirm[0]
    app.handle("b")
    assert app.confirm is None
    assert app.sm.policy["governor"] == "schedutil"


def test_logger_toggle_failure_does_not_crash(app, monkeypatch):
    import actions

    def boom():
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(actions, "logger_install", boom)
    monkeypatch.setattr(app, "can_change_system", lambda: True)
    app.demo = False
    app.conf.pop("logger", None)
    app._logger_set(True)
    assert "logger" not in app.conf
    assert app.toast and app.toast[0] == "Logger not changed"
    app.demo = True


def test_hostile_text_never_crashes(app):
    import theme as th
    for bad in ("a\x00bc", "\u200b", "\u202e", "x\x07y", "\U0001F600 emoji", "\t", ""):
        th.text(app.canvas, app.fonts, app.fonts.m, bad, (255, 255, 255), (10, 10), maxw=50)
        th.label(app.canvas, app.fonts, bad, (255, 255, 255), (10, 10))
    with app.cap.lock:
        app.cap.dns.append((time.time(), "evil\x00name.local"))
        app.cap.names["10.0.0.9"] = "\u200b"
    app.switch(1)
    for p in range(5):
        app.page.page = p
        app.page.side = "dns"
        app.draw()


def test_page_error_is_shown_not_fatal(app, monkeypatch):
    app.switch(2)
    monkeypatch.setattr(app.page, "draw", lambda a, s: 1 / 0)
    app.draw()
    assert any("POWER page error" in m for _t, _l, m in app.log.snapshot())


def test_fit_trims_to_width(app):
    import theme as th
    f = app.fonts.m
    out = th.fit(f, "x" * 500, 100)
    assert out.endswith("…") and f.size(out)[0] <= 100 and f.size(out[:-1] + "x…")[0] > 100


def test_capture_pause_survives_leaving_net(app):
    app.switch(1)
    app.page.page = 3
    app.handle("a")                                   # pause
    app.switch(2)
    app.switch(1)
    assert not app.cap.capturing.is_set()


def test_time_left_and_full_need_enough_data(app, handheld, tmp_path):
    from conftest import put
    sm = real_sampler(app, tmp_path)
    assert sm.ma_avg() is None                        # 2 samples: not enough for a time-left
    sm.i_hist.clear()
    for _ in range(70):
        sm.i_hist.append(500.0)
    assert sm.ma_avg() == 500.0
    put(handheld, "/sys/class/power_supply/axp2202-battery/status", "Charging\n")
    sm.tick(time.time() + 3)
    assert len(sm.i_hist) <= 1                        # discharge samples dropped on the change
    put(handheld, "/tmp/battery.percent", "85\n")
    sm.tick(time.time() + 4)
    assert sm.time_to_full() is None                  # above 80 % no straight-line estimate


def test_interrupted_game_scan_saves_nothing(app, handheld, tmp_path):
    import json
    sm = sysmon.SysMonitor(app.log, str(tmp_path / "c3.json"))
    sm.stop_ev.set()
    sm.scan_games()
    assert sm.games is None and not (tmp_path / "c3.json").exists()
    sm.stop_ev.clear()
    sm.scan_games()
    assert sm.games["total"] == 6
    from conftest import put
    put(handheld, "/userdata/roms/gba/x/y/z.gba", "x")
    sm.scan_games()
    assert sm.games["total"] == 7                     # a game added deep inside is counted
    assert json.load(open(tmp_path / "c3.json"))["games"]


def test_no_system_list_means_count_unknown(app, handheld, tmp_path):
    import os
    os.remove(os.path.join(str(handheld), "usr/share/emulationstation/es_systems.cfg"))
    sm = real_sampler(app, tmp_path)
    assert sm.games["total"] is None and sm.games["systems"]
    draw_all(app)


def test_stopped_port_scan_is_not_a_result(app, monkeypatch):
    import netmon
    ps = netmon.PortScan(app.log)

    def fake_scan(ip, ports, progress=None, stop=None, **kw):
        ps.stop_ev.set()
        return []
    monkeypatch.setattr(netmon.nc, "port_scan", fake_scan)
    ps.start({"ip": "192.168.1.20"})
    for _ in range(50):
        if not ps.busy:
            break
        time.sleep(0.02)
    assert ps.stage == "stopped" and "192.168.1.20" not in ps.history
