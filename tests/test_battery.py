import os

import battery as bt


T0 = 1_760_000_000


def discharge(t0, minutes, pct0, ma, cap, up0=100.0, status="Discharging", step=60):
    rows, pct = [], float(pct0)
    for i in range(minutes):
        t = t0 + i * step
        rows.append(bt.Row(t, up0 + i * step, status, 3400 + 8 * pct, ma, round(pct), round(pct), 45.0, 30))
        pct -= ma * step / 3600.0 / cap * 100.0
    return rows


def test_parse_line_and_comments():
    assert bt.parse_line("# header") is None
    assert bt.parse_line("garbage") is None
    r = bt.parse_line("1700000000,120,Discharging,3790,575,57,60,50.3,31\n")
    assert r.t == 1700000000 and r.mv == 3790.0 and r.ma == 575.0 and r.pct == 57 and r.bright == 31
    r = bt.parse_line("1700000000,120,Discharging,3790,,57,60,,\n")
    assert r.ma is None and r.temp is None


def test_health_from_one_long_discharge():
    rows = discharge(T0, 300, 95, 300.0, 3000.0)     # 5 h at 300 mA on a 3000 mAh battery
    h = bt.health(rows, 3200)
    assert h["samples"] == 1
    assert abs(h["mah"] - 3000) < 60
    assert abs(h["pct"] - 93.75) < 2


def test_health_learning_when_runs_too_short():
    rows = discharge(T0, 20, 95, 300.0, 3000.0)
    h = bt.health(rows, 3200)
    assert "learning" in h and 0 < h["learning"] < 1


def test_gap_splits_runs_and_short_pieces_do_not_count():
    a = discharge(T0, 40, 95, 300.0, 3000.0)
    b = discharge(a[-1].t + 3600, 40, a[-1].pct, 300.0, 3000.0, up0=a[-1].up + 3600)
    assert len(bt.runs(a + b)) == 2
    assert bt.capacity_samples(a + b, 3200) == []            # neither piece used 30 %


def test_reboot_detected_and_last_shutdown():
    a = discharge(T0, 10, 20, 300.0, 3000.0)
    b = discharge(a[-1].t + 600, 10, 18, 300.0, 3000.0, up0=30.0)
    t, mv = bt.last_shutdown(a + b)
    assert t == a[-1].t and mv == a[-1].mv


def test_last_full_discharge_and_since_charge():
    chg = [bt.Row(T0 - 3600 + i * 60, 50.0 + i * 60, "Charging", 4100, 1500, 99, 99, 35.0, 30) for i in range(5)]
    full = discharge(chg[-1].t + 60, 600, 100, 330.0, 3000.0, up0=chg[-1].up + 60)
    rows = chg + full
    lf = bt.last_full_discharge(rows)
    assert lf and lf["start"] == full[0].t and lf["dur"] > 9 * 3600
    assert bt.since_charge(rows) == full[0].t


def test_outliers_are_dropped():
    rows = discharge(T0, 300, 95, 300.0, 3000.0)
    assert bt.capacity_samples(rows, 1000) == []             # 3000 mAh is >150 % of a 1000 design


def test_time_left_methods():
    secs, how = bt.time_left(50, 300.0, 3000.0)
    assert how == "by current draw" and abs(secs - 5 * 3600) < 1
    secs, how = bt.time_left(50, None, 3000.0, pct_per_h=10.0)
    assert how == "by % drop" and abs(secs - 5 * 3600) < 1
    assert bt.time_left(50, None, 3000.0)[0] is None
    assert bt.time_to_full(50, 1500.0, 3000.0) == 3600.0
    assert bt.time_to_full(50, 10.0, 3000.0) is None


def test_load_reads_monthly_files(tmp_path, monkeypatch):
    import sysinfo as si
    monkeypatch.setattr(si, "ROOT", str(tmp_path))
    d = tmp_path / "userdata/system/fieldterminal"
    d.mkdir(parents=True)
    (d / "battery-202610.csv").write_text("# h\n1761000000,1,Discharging,3800,300,80,80,40.0,30\n")
    (d / "battery-202611.csv").write_text("1763000000,2,Discharging,3790,300,79,79,40.0,30\nbroken line\n")
    rows = bt.load()
    assert [r.t for r in rows] == [1761000000, 1763000000]
    assert bt.last_line_age(now=os.path.getmtime(d / "battery-202611.csv") + 5) == 5


def test_bad_lines_are_dropped_or_cleaned():
    assert bt.parse_line("17631000601763100120,1,Discharging,3790,575,57,60,50.3,31") is None   # glued epochs
    assert bt.parse_line("100,1,Discharging,3790,575,57,60,50.3,31") is None                    # clock not set
    r = bt.parse_line("1763100060,1,Weird,99999,-5,250,60,50.3,31")
    assert r.status is None and r.mv is None and r.ma is None and r.pct is None and r.kpct == 60


def test_charge_while_switched_off_counts():
    a = discharge(T0, 60, 40, 300.0, 3000.0)
    b = discharge(a[-1].t + 4 * 3600, 30, 95, 300.0, 3000.0, up0=30.0)     # off, charged, back on at 95 %
    assert bt.since_charge(a + b) == b[0].t


def test_health_never_above_120_percent_of_design():
    rows = discharge(T0, 300, 95, 300.0, 3000.0)
    assert bt.capacity_samples(rows, 2400) == []            # 3000 mAh would be 125 % of design
