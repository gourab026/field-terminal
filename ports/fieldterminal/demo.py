"""Field Terminal - demo mode: synthetic data on a made-up 192.168.1.0/24 network.

Used for `--demo` and for rendering preview screenshots off the device. The traffic
generator builds *real* packet bytes and pushes them through the real decoder.
"""
import math
import os
import random
import socket
import struct
import sys
import time

import capture as cap

MY_MAC, GW_MAC = "a4:2b:8c:51:07:3d", "a8:6e:84:20:11:9c"
MY_IP, GW = "192.168.1.42", "192.168.1.1"
SITES = ["www.wikipedia.org", "github.com", "i.ytimg.com", "www.youtube.com", "fonts.gstatic.com",
         "api.steampowered.com", "time.cloudflare.com", "docs.python.org", "www.retroarch.com"]
PUBLIC = ["142.250.74.46", "140.82.121.4", "185.15.59.224", "104.16.132.229", "151.101.1.69"]

NETS = [
    ("HomeNet", "a8:6e:84:20:11:9c", -46, 2437, "WPA2", True),
    ("HomeNet-5G", "a8:6e:84:20:11:9d", -55, 5180, "WPA2/WPA3", False),
    ("Neighbor_2.4", "c4:e9:84:3a:77:01", -67, 2412, "WPA2", False),
    ("CafeFreeWiFi", "00:1d:7e:55:a0:2c", -72, 2462, "Open", False),
    ("DIRECT-HP-Printer", "3e:2a:f4:8b:01:77", -70, 2437, "WPA2", False),
    ("", "f2:9b:11:c0:44:6e", -80, 2462, "WPA2", False),
    ("IoT-Hub", "50:02:91:aa:31:00", -77, 2412, "WPA", False),
    ("OldRouter", "00:14:6c:2d:9e:10", -84, 2422, "WEP", False),
    ("Office-5G", "70:4f:57:1b:c3:52", -78, 5745, "WPA3", False),
]
DEVICES = [
    ("192.168.1.1", "router", GW_MAC, 1.8, ["Gateway"]),
    ("192.168.1.12", "living-room-tv", "5c:aa:fd:3e:90:12", 6.4, []),
    ("192.168.1.20", "hp-printer", "3c:2a:f4:8b:01:77", 12.1, []),
    ("192.168.1.31", "pixel-7", "9a:1f:3c:02:5d:e4", 48.0, ["Random MAC"]),
    ("192.168.1.42", "knulli", MY_MAC, 0.0, ["This device"]),
    ("192.168.1.55", "study-laptop", "f0:2f:74:9a:33:10", 3.3, []),
    ("192.168.1.77", "chromecast", "d4:f5:47:21:8a:0b", 9.7, []),
    ("192.168.1.88", "iphone", "b6:43:d2:7e:19:a0", 61.2, ["Random MAC"]),
    ("192.168.1.103", "", "50:02:91:aa:31:5c", 22.5, []),
]
OPEN_PORTS = {
    "192.168.1.1": [(53, 2.1, ""), (80, 2.4, "HTTP/1.1 200 OK  |  lighttpd/1.4.59"), (443, 2.6, ""),
                    (5000, 3.1, "HTTP/1.1 404 Not Found  |  Linux UPnP/1.0 MiniUPnPd/2.2")],
    "192.168.1.20": [(80, 9.0, "HTTP/1.1 200 OK  |  HP HTTP Server"), (443, 9.4, ""), (515, 10.1, ""),
                     (631, 9.8, "HTTP/1.1 200 OK  |  CUPS/2.4 IPP/2.1"), (9100, 11.0, "")],
    "192.168.1.55": [(22, 2.9, "SSH-2.0-OpenSSH_9.6p1 Debian-4"), (445, 3.4, ""), (8000, 3.1, "HTTP/1.0 200 OK  |  SimpleHTTP/0.6 Python/3.12.8")],
}


# ---------------------------------------------------------------- frame builders

def _mac(m):
    return bytes(int(x, 16) for x in m.split(":"))


def eth(src, dst, etype, payload):
    return _mac(dst) + _mac(src) + struct.pack("!H", etype) + payload


def ipv4(src, dst, proto, payload):
    return struct.pack("!BBHHHBBH4s4s", 0x45, 0, 20 + len(payload), random.randint(0, 65535), 0x4000, 64, proto, 0,
                       socket.inet_aton(src), socket.inet_aton(dst)) + payload


def ipv6(src, dst, nh, payload):
    return (struct.pack("!IHBB", 6 << 28, len(payload), nh, 255) + socket.inet_pton(socket.AF_INET6, src)
            + socket.inet_pton(socket.AF_INET6, dst) + payload)


def udp(sp, dp, data):
    return struct.pack("!HHHH", sp, dp, 8 + len(data), 0) + data


def tcp(sp, dp, flags, data=b""):
    return struct.pack("!HHIIBBHHH", sp, dp, random.getrandbits(32), random.getrandbits(32), 5 << 4, flags, 64240, 0, 0) + data


def qname(n):
    return b"".join(bytes([len(p)]) + p.encode() for p in n.split(".")) + b"\0"


def dns(name, qtype=1, resp=False):
    body = struct.pack("!HHHHHH", random.randint(0, 65535), 0x8180 if resp else 0x0100, 1, 1 if resp else 0, 0, 0)
    body += qname(name) + struct.pack("!HH", qtype, 1)
    if resp:
        body += b"\xc0\x0c" + struct.pack("!HHIH", 1, 1, 300, 4) + socket.inet_aton(random.choice(PUBLIC))
    return body


def tls_hello(sni):
    name = sni.encode()
    ext = struct.pack("!HH", 0, len(name) + 5) + struct.pack("!HBH", len(name) + 3, 0, len(name)) + name
    body = b"\x03\x03" + os.urandom(32) + b"\x00" + struct.pack("!H", 2) + b"\x13\x01" + b"\x01\x00"
    body += struct.pack("!H", len(ext)) + ext
    hs = b"\x01" + len(body).to_bytes(3, "big") + body
    return b"\x16\x03\x01" + struct.pack("!H", len(hs)) + hs


def arp(op, sha, spa, tpa):
    return (struct.pack("!HHBBH", 1, 0x0800, 6, 4, op) + _mac(sha) + socket.inet_aton(spa)
            + (b"\0" * 6 if op == 1 else _mac(MY_MAC)) + socket.inet_aton(tpa))


def dhcp(kind):
    b = bytearray(240)
    b[0:3] = b"\x01\x01\x06"
    b[236:240] = b"\x63\x82\x53\x63"
    return bytes(b) + bytes([53, 1, kind, 255])


def random_frame():
    """One plausible packet of everyday traffic -> (frame, outgoing)."""
    k = random.random()
    sport = random.randint(40000, 60999)
    if k < 0.22:
        site, ip = random.choice(SITES), random.choice(PUBLIC)
        return eth(MY_MAC, GW_MAC, 0x0800, ipv4(MY_IP, ip, 6, tcp(sport, 443, 0x18, tls_hello(site)))), True
    if k < 0.45:
        ip = random.choice(PUBLIC)
        data = b"\x17\x03\x03" + os.urandom(random.randint(80, 1300))
        return eth(GW_MAC, MY_MAC, 0x0800, ipv4(ip, MY_IP, 6, tcp(443, sport, 0x18, data))), False
    if k < 0.58:
        site = random.choice(SITES)
        if random.random() < 0.5:
            return eth(MY_MAC, GW_MAC, 0x0800, ipv4(MY_IP, GW, 17, udp(sport, 53, dns(site)))), True
        return eth(GW_MAC, MY_MAC, 0x0800, ipv4(GW, MY_IP, 17, udp(53, sport, dns(site, resp=True)))), False
    if k < 0.66:
        return eth(MY_MAC, GW_MAC, 0x0800, ipv4(MY_IP, random.choice(PUBLIC), 17, udp(sport, 443, os.urandom(1200)))), True
    if k < 0.72:
        return eth(GW_MAC, "ff:ff:ff:ff:ff:ff", 0x0806, arp(1, GW_MAC, GW, random.choice(DEVICES)[0])), False
    if k < 0.78:
        return eth("d4:f5:47:21:8a:0b", "01:00:5e:00:00:fb", 0x0800,
                   ipv4("192.168.1.77", "224.0.0.251", 17, udp(5353, 5353, dns("_googlecast._tcp.local", 12)))), False
    if k < 0.83:
        notify = b"NOTIFY * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\nNT: upnp:rootdevice\r\n\r\n"
        return eth(GW_MAC, "01:00:5e:7f:ff:fa", 0x0800, ipv4(GW, "239.255.255.250", 17, udp(1900, 1900, notify))), False
    if k < 0.87:
        icmp = struct.pack("!BBHHH", 8, 0, 0, 7, random.randint(1, 999)) + b"x" * 32
        return eth(MY_MAC, GW_MAC, 0x0800, ipv4(MY_IP, GW, 1, icmp)), True
    if k < 0.90:
        req = b"GET / HTTP/1.1\r\nHost: neverssl.com\r\nUser-Agent: curl/8.5\r\n\r\n"
        return eth(MY_MAC, GW_MAC, 0x0800, ipv4(MY_IP, "34.223.124.45", 6, tcp(sport, 80, 0x18, req))), True
    if k < 0.93:
        return eth(MY_MAC, GW_MAC, 0x0800, ipv4(MY_IP, "162.159.200.1", 17, udp(123, 123, b"\x23" + b"\0" * 47))), True
    if k < 0.96:
        ra = struct.pack("!BBH", 134, 0, 0) + b"\0" * 12
        return eth(GW_MAC, "33:33:00:00:00:01", 0x86DD, ipv6("fe80::aa6e:84ff:fe20:119c", "ff02::1", 58, ra)), False
    if k < 0.98:
        return eth(GW_MAC, MY_MAC, 0x0800, ipv4(GW, MY_IP, 17, udp(67, 68, dhcp(5)))), False
    return eth(MY_MAC, GW_MAC, 0x0800, ipv4(MY_IP, "192.168.1.55", 6, tcp(sport, 22, 0x02))), True


# ---------------------------------------------------------------- demo collectors

def build(log, M):
    """-> (monitor, wifi, discovery, portscan, tools, capture) fed with synthetic data."""

    class DemoMonitor(M.Monitor):
        def __init__(self, log):
            self._t = 0
            super().__init__(log)
            for _ in range(self.N):
                self._step()

        def refresh_info(self):
            self.iface, self.gateway = "wlan0", GW
            self.info = {"iface": "wlan0", "ip": MY_IP, "mask": "255.255.255.0", "mac": MY_MAC, "state": "up",
                         "wireless": True, "cidr": "192.168.1.0/24", "prefix": 24}
            self.dns = [GW, "1.1.1.1"]

        def _step(self):
            self._t += 1
            t = self._t
            burst = 2_400_000 * max(0.0, math.sin(t / 9.0)) ** 6 + random.uniform(0, 120_000)
            rx = 18_000 + burst + random.uniform(0, 30_000)
            tx = 6_000 + burst * 0.04 + random.uniform(0, 12_000)
            sig = int(-47 + 3 * math.sin(t / 13.0) + random.uniform(-2, 2))
            lost = t % 47 == 0
            rtt = None if lost else 3.0 + abs(random.gauss(0, 1.2)) + (35 if t % 29 == 0 else 0)
            inet = None if t % 61 == 0 else 14.0 + abs(random.gauss(0, 2.5)) + (rtt or 3.0) * 0.5
            self.station = {"tx_packets": 1000 * t, "tx_retries": 31 * t, "beacon_loss": 0, "connected": 5400 + t}
            self.retry_pct = 3.1 + random.uniform(-0.4, 0.4)
            self.dns_ok, self.dns_ms = True, 18.0 + random.uniform(0, 6)
            with self.lock:
                self.rx_hist.append(rx)
                self.tx_hist.append(tx)
                self.rx_rate, self.tx_rate = rx, tx
                self.rx_total += int(rx)
                self.tx_total += int(tx)
                self.link = {"bssid": GW_MAC, "ssid": "HomeNet", "freq": 2437.0, "signal": sig, "txrate": 72.2,
                             "rxrate": 65.0, "channel": 6, "band": "2.4 GHz"}
                self.sig_hist.append(sig)
                self.gw.add(rtt)
                self.inet.add(inet)

        def run(self):
            while not self.stop_ev.wait(1.0):
                self._step()

        def _internet_loop(self):
            pass

    class DemoWifi(M.WifiScan):
        def _run(self):
            time.sleep(1.0)
            nets = []
            for ssid, bssid, sig, freq, sec, conn in NETS:
                nets.append({"ssid": ssid, "bssid": bssid, "signal": float(sig + random.randint(-2, 2)),
                             "freq": float(freq), "security": sec, "connected": conn, "privacy": sec != "Open",
                             "seen_ms": random.randint(10, 900), "channel": M.nc.freq_channel(freq),
                             "band": M.nc.freq_band(freq)})
            nets.sort(key=lambda n: -n["signal"])
            self.nets, self.when = nets, time.time()
            self.known.update(n["bssid"] for n in nets)
            self.log.add(f"Wi-Fi scan: {len(nets)} networks, 2 open/WEP (insecure)")

    class DemoDiscovery(M.Discovery):
        def _run(self):
            self.cidr, self.method, self.stage = "192.168.1.0/24", "ARP sweep", "sweeping"
            for i in range(0, 255, 17):
                self._progress(i, 254)
                time.sleep(0.03)
            self.stage = "names"
            time.sleep(0.2)
            self.devices = [{"ip": ip, "mac": mac, "rtt": rtt, "name": name, "fqdn": f"{name}.lan" if name else "",
                             "notes": notes} for ip, name, mac, rtt, notes in DEVICES]
            self.when, self.stage = time.time(), "done"
            self.known.update(d["ip"] for d in self.devices)
            self.log.add(f"Device scan (ARP sweep): {len(self.devices)} devices on 192.168.1.0/24")

    class DemoPortScan(M.PortScan):
        def _run(self, ip):
            total = len(M.nc.TOP_PORTS)
            for i in range(0, total + 1, 4):
                self._progress(min(i, total), total)
                time.sleep(0.03)
            self.stage = "banners"
            svc = dict(M.nc.TOP_PORTS)
            for port, ms, banner in OPEN_PORTS.get(ip, []):
                self.results = self.results + [(port, svc.get(port, "?"), ms, banner)]
                time.sleep(0.05)
            self.duration, self.when, self.stage = 3.8, time.time(), "done"
            self.history[ip] = list(self.results)
            self.log.add(f"Port scan {ip}: {len(self.results)} open of {total}")

    class DemoTools(M.Tools):
        def _ping(self, host, count=20):
            self.add(f"PING {host}  (raw ICMP, {count} probes)")
            base = 2.0 if host.startswith("192.168.") else 14.0
            for i in range(count):
                if self.stop_ev.is_set():
                    break
                self.sent += 1
                if i == 13:
                    self.lost += 1
                    self.rtts.append(-1.0)
                    self.add(f"seq={i + 1:<3d} request timed out")
                else:
                    rtt = base + abs(random.gauss(0, base * 0.25)) + (22 if i == 7 else 0)
                    self.rtts.append(rtt)
                    self.add(f"reply from {host}  seq={i + 1:<3d} time={rtt:7.2f} ms")
                time.sleep(0.06)
            ok = [r for r in self.rtts if r >= 0]
            self.add(f"--- {self.sent} sent, {self.lost} lost, min/avg/max {min(ok):.1f}/{sum(ok) / len(ok):.1f}/{max(ok):.1f} ms")

        def _trace(self, host, max_hops=20):
            self.add(f"TRACEROUTE {host} ({host}), max {max_hops} hops")
            hops = [(GW, 1.9), ("100.64.0.1", 8.4), (None, 0), ("10.20.1.5", 11.2), ("195.66.224.10", 13.7), (host, 14.1)]
            for ttl, (ip, ms) in enumerate(hops, 1):
                time.sleep(0.1)
                if ip is None:
                    self.add(f"{ttl:2d}  *              (no reply)")
                else:
                    self.add(f"{ttl:2d}  {ip:<15s} {ms:7.1f} ms   {self.where(ip)}")
            self.add(f"--- reached {host} in {len(hops)} hops")

        def _dns(self, _target=None):
            self.add("DNS server(s): 192.168.1.1, 1.1.1.1")
            for name, ms in (("example.com", 21.4), ("wikipedia.org", 18.2), ("github.com", 3.1),
                             ("cloudflare.com", 2.7), ("google.com", 9.9)):
                time.sleep(0.08)
                self.rtts.append(ms)
                self.add(f"{name:<15s} {ms:6.1f} ms  {random.choice(PUBLIC)}")

    class DemoCapture(cap.Capture):
        def __init__(self, iface, my_ip, log):
            super().__init__(iface, my_ip, log)
            for i in range(self.HIST):
                self.pps_hist.append(int(25 + 20 * max(0, math.sin(i / 9.0)) ** 2 + random.uniform(0, 12)))
            now = time.time() - 120
            for i in range(400):   # some history so the lists are not empty at first draw
                frame, out = random_frame()
                self.ingest(frame, out, now + i * 0.3)
            self._sec_pkts = self._sec_bytes = 0
            self._sec_t = time.time()

        def run(self):
            while not self.stop_ev.is_set():
                time.sleep(random.uniform(0.01, 0.05))
                now = time.time()
                with self.lock:
                    self._tick(now)
                    if self.capturing.is_set():
                        frame, out = random_frame()
                        self.ingest(frame, out, now)

    mon = DemoMonitor(log)
    scan = DemoPortScan(log)
    svc = dict(M.nc.TOP_PORTS)
    scan.history["192.168.1.1"] = [(p, svc.get(p, "?"), ms, b) for p, ms, b in OPEN_PORTS["192.168.1.1"]]
    scan.history["192.168.1.55"] = [(p, svc.get(p, "?"), ms, b) for p, ms, b in OPEN_PORTS["192.168.1.55"]]
    scan.history["192.168.1.12"] = []
    return (mon, DemoWifi(mon, log), DemoDiscovery(mon, log), scan, DemoTools(mon, log),
            DemoCapture("wlan0", MY_IP, log))


# ---------------------------------------------------------------- system demo

GAMES = [("psp", "PSP", 5, 5.9e9), ("psx", "PlayStation", 9, 3.3e9), ("nds", "Nintendo DS", 38, 2.1e9),
         ("dreamcast", "Dreamcast", 4, 1.9e9), ("ports", "Ports", 6, 1.2e9), ("gba", "Game Boy Advance", 212, 1.1e9),
         ("snes", "Super Nintendo", 96, 0.31e9), ("nes", "NES", 38, 0.05e9), ("gb", "Game Boy", 4, 0.01e9)]
PROCS = [("This app", 3.2, 38, True), ("Menu", 0.6, 96, False), ("Samba", 0.2, 11, False), ("Wi-Fi", 0.1, 4, False),
         ("Network", 0.1, 9, False), ("System log", 0.0, 6, False), ("SSH", 0.0, 5, False),
         ("Battery tracker", 0.0, 3, False), ("Battery logger", 0.0, 2, False), ("Controls", 0.0, 3, False),
         ("Clock sync", 0.0, 3, False), ("Network names", 0.0, 2, False), ("System bus", 0.0, 2, False)]


def fake_log(now, design=3200):
    """Battery-log rows: a full discharge three days ago, a charge, then today's use."""
    import battery as bt
    rows, t, up = [], now - 3 * 86400, 600.0
    cap = design * 0.94                                   # a battery at 94 % health
    pct, mv = 100.0, 4150.0
    while pct > 2:                                         # full discharge, ~9 h 50 min
        ma = 300 + 25 * math.sin(t / 900.0) + random.uniform(-10, 10)
        rows.append(bt.Row(int(t), up, "Discharging", round(mv), round(ma), round(pct), round(pct + 4), 48.0, 31))
        pct -= ma / 60.0 / cap * 100.0
        mv = 3380 + 770 * (pct / 100) ** 0.8
        t += 60
        up += 60
    t += 3600                                              # off, then charging after a reboot
    up = 30.0
    while pct < 100:
        rows.append(bt.Row(int(t), up, "Charging", round(mv), 1500, round(pct), round(pct), 38.0, 31))
        pct = min(100, pct + 1.3)
        mv = 3600 + 600 * pct / 100
        t += 60
        up += 60
    rows.append(bt.Row(int(t), up, "Full", 4180, 0, 100, 100, 35.0, 31))
    t = now - (2 * 86400 + 7 * 3600)                       # unplugged 2 d 7 h ago, used now and then
    up = 60.0
    pct = 100.0
    while t < now - 60:
        on = (t // 3600) % 9 == 0 and pct > 45             # an hour of play, then eight asleep
        if on:
            ma = 280 + random.uniform(-15, 15)
            rows.append(bt.Row(int(t), up, "Discharging", round(3400 + 760 * (pct / 100) ** 0.8), round(ma),
                               round(pct), round(pct + 3), 50.0, 31))
            pct -= ma / 60.0 / cap * 100.0
            up += 60
        else:
            pct = max(1.0, pct - 0.004)
        t += 60
    return rows


def build_sys(log, cache_path):
    import battery as bt
    import sysmon

    class DemoSysMonitor(sysmon.SysMonitor):
        def __init__(self, log, cache_path):
            super().__init__(log, cache_path)
            self.cpus = [0, 1, 2, 3]
            self.core_hist = {c: sysmon.deque([None] * sysmon.N_CPU_HIST, maxlen=sysmon.N_CPU_HIST) for c in self.cpus}
            self.mhz_hist = {c: sysmon.deque([None] * sysmon.N_CPU_HIST, maxlen=sysmon.N_CPU_HIST) for c in self.cpus}
            self.bdir = "demo"
            self.design = 3200
            self._t = 0
            self._pct = 57.4
            self.policy = {"governor": "schedutil", "max_mhz": 1512.0, "hw_max_mhz": 1512.0, "min_mhz": 480.0,
                           "available": ["conservative", "ondemand", "userspace", "powersave", "performance",
                                         "schedutil"]}
            self.freq = {"gpu": 432.0, "mem": 672.0}
            self.bright, self.uptime = 31, 4320.0
            self.run_start = time.time() - 3 * 3600
            for _ in range(sysmon.N_BAT_V):
                self._fake(time.time())
            self.refresh_analysis()
            self.scan_games()

        def _fake(self, now):
            self._t += 1
            t = self._t
            loads = {c: max(2.0, min(100.0, 30 + 22 * math.sin(t / 7.0 + c) + random.uniform(-8, 8)))
                     for c in self.cpus}
            mhz = {c: 1200.0 if loads[c] < 45 else 1512.0 for c in self.cpus}
            self._pct -= 0.0015
            ma = 575 + random.uniform(-25, 25)
            mv = 3790 + 8 * math.sin(t / 15.0) + random.uniform(-3, 3)
            used = 552 * 1048576 + int(18e6 * math.sin(t / 30.0))
            with self.lock:
                self.bat = {"present": True, "status": "Discharging", "pct": int(self._pct), "kpct": 60, "mv": mv,
                            "ma": ma, "design_mah": None, "full_mah": None, "cycles": None, "temp": None,
                            "plugged": False}
                self.v_hist.append(mv)
                self.i_hist.append(ma)
                self.pct_hist.append((now - (sysmon.N_BAT_V - t) * 20, self._pct + 0.6 * (sysmon.N_BAT_V - t) / 180))
                self.cpu_total = sum(loads.values()) / 4
                self.core_load, self.core_mhz = loads, mhz
                self.cpu_temp = 50.6 + 1.5 * math.sin(t / 20.0)
                self.gpu_temp = 48.1 + math.sin(t / 25.0)
                self.load_hist.append(self.cpu_total)
                self.temp_hist.append(self.cpu_temp)
                for c in self.cpus:
                    self.core_hist[c].append(loads[c])
                    self.mhz_hist[c].append(mhz[c])
                self.mem = {"total": 898 * 1048576, "available": 898 * 1048576 - used, "used": used,
                            "free": 120 * 1048576, "cache": 210 * 1048576, "swap_total": 0, "swap_free": 0}
                self.zram = {"size": 449 * 1048576, "stored": 0, "compressed": 0, "in_ram": 0, "active": True,
                             "gained": 0, "algo": "lz4"}
                self.mem_hist.append(100.0 * used / (898 * 1048576))
                self.disks = [{"label": "Games", "mount": "/userdata", "dev": "/dev/mmcblk1p4", "fs": "exfat",
                               "total": 116.7e9, "free": 79.2e9, "used": 37.5e9},
                              {"label": "System", "mount": "/boot", "dev": "/dev/mmcblk1p3", "fs": "vfat",
                               "total": 5.37e9, "free": 3.36e9, "used": 2.01e9}]
                procs = []
                for i, (name, cpu, mb, me) in enumerate(PROCS):
                    procs.append({"pid": 100 + i, "comm": name, "name": name, "cpu": max(0.0, cpu + random.uniform(-0.1, 0.1)),
                                  "rss": mb * 1048576, "me": me, "count": 3 if name == "Samba" else 1})
                self.procs, self.nprocs = procs, 38

        def tick(self, now):
            self._fake(now)

        def refresh_analysis(self):
            self.analysis = bt.Analysis(fake_log(time.time(), 3200), 3200)

        def scan_games(self):
            sy = [{"name": n, "full": f, "games": g, "bytes": int(b)} for n, f, g, b in GAMES]
            self.games = {"total": sum(x["games"] for x in sy), "systems": sy, "ports": 6, "when": time.time(),
                          "nsys": len(sy)}

        def _slow_jobs(self):
            pass

    return DemoSysMonitor(log, cache_path)


class DemoActions:
    """Same calls as actions.py, but only changes the demo's own state."""

    def __init__(self, sm):
        self.sm, self.logger, self.zram_boot = sm, True, False

    def set_mode(self, mode):
        import actions
        gov = actions.governor_for(mode, self.sm.policy["available"])
        self.sm.policy = dict(self.sm.policy, governor=gov)
        return True, f"{actions.MODES[mode][0]} ({gov})"

    def zram_set_boot(self, on):
        self.zram_boot = on
        return True, ""

    def zram_start(self):
        return True, ""

    def zram_stop(self, mem, z):
        return True, ""


# ---------------------------------------------------------------- previews

def render_screens(AppCls, out):
    import pygame
    os.makedirs(out, exist_ok=True)
    app = AppCls(demo=True, screens=out)

    def settle(secs, cond=None):
        end = time.time() + secs
        while time.time() < end and not (cond and cond()):
            pygame.event.pump()
            if hasattr(app.page, "tick"):
                app.page.tick(app)
            time.sleep(0.05)

    def shot(name):
        app.draw()
        path = os.path.join(out, f"{name}.png")
        pygame.image.save(app.canvas, path)
        print("saved", path)

    names = [p.name.lower() for p in app.pages]
    settle(1.5)
    shot("1-dash")
    net = app.pages[1]
    app.switch(1)
    for i, page in enumerate(("overview", "wifi", "devices", "traffic", "tools")):
        net.page = i
        net._page_jobs(app)
        settle(1.3)
        settle(3.0, lambda: (page != "wifi" or app.wifi.nets) and (page != "devices" or (app.disc.devices and
                                                                                         not app.disc.busy)))
        if page == "wifi":
            net.sel["wifi"] = 3
        shot(f"2{'abcde'[i]}-net-{page}")
    net.page = 2
    net.sel["dev"] = 2
    net.handle(app, "a")
    settle(5.0, lambda: app.scan.stage == "done")
    shot("2f-net-portscan")
    net.dev_view = "list"
    net.page = 3
    net.side = "dns"
    shot("2g-net-traffic-dns")
    net.page = 4
    net.sel["tools"] = 1
    net.handle(app, "a")
    settle(3.0, lambda: not app.tools.busy and app.tools.lines)
    shot("2h-net-tools-ping")
    for i in range(2, len(app.pages)):
        app.switch(i)
        settle(0.3)
        shot(f"{i + 1}-{names[i]}")
    app.switch(3)
    app.handle("down")
    app.handle("a")
    shot("4b-cpu-confirm")
    app.confirm = None
    app.switch(0)
    app.notify("Battery logger on", "ok", "records one line a minute", 60)
    shot("1b-dash-toast")
    app.toast = None
    app.calibrating = True
    shot("0-first-start")
    app.calibrating = False
    app.shutdown()
