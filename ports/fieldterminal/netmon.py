"""Field Terminal - network data layer: event log, live monitor, Wi-Fi scan, device discovery,
port scan and the ping / traceroute / DNS tools. Moved unchanged from Net Analyzer 1.1, except
that the monitor pings less often while the NET tab is not on screen.
"""
import ipaddress
import os
import socket
import threading
import time
from collections import deque

import netcore as nc

INTERNET_HOST = "1.1.1.1"


class EventLog:
    def __init__(self, log_dir):
        os.makedirs(log_dir, exist_ok=True)
        self.path = os.path.join(log_dir, time.strftime("fieldterminal-%Y%m%d.log"))
        self.items = deque(maxlen=500)
        self.lock = threading.Lock()

    def add(self, msg, level="info"):
        t = time.time()
        with self.lock:
            self.items.append((t, level, msg))
        try:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(t))} [{level.upper():5s}] {msg}\n")
        except OSError:
            pass

    def snapshot(self):
        with self.lock:
            return list(self.items)


class PingSeries:
    """Rolling latency history for one target (None = no data yet, -1 = lost)."""
    N = 120

    def __init__(self, target):
        self.target = target
        self.hist = deque([None] * self.N, maxlen=self.N)
        self.vals = deque(maxlen=600)
        self.sent = self.lost = self.streak = 0

    def add(self, rtt):
        self.sent += 1
        if rtt is None:
            self.lost += 1
            self.streak += 1
            self.hist.append(-1.0)
        else:
            self.streak = 0
            self.hist.append(rtt)
            self.vals.append(rtt)

    def last(self):
        return next((v for v in reversed(self.hist) if v is not None), None)

    def stats(self):
        v = list(self.vals)
        if not v:
            return None
        return min(v), sum(v) / len(v), max(v), (100.0 * self.lost / self.sent if self.sent else 0.0)


class Monitor(threading.Thread):
    """Once a second: traffic counters, Wi-Fi link, pings to the gateway and the internet;
    every 5 s the link-level counters; every 20 s a DNS check."""
    N = 120

    def __init__(self, log):
        super().__init__(daemon=True)
        self.log, self.lock, self.stop_ev = log, threading.Lock(), threading.Event()
        self.iface, self.gateway = nc.pick_iface()
        self.info, self.link, self.dns, self.station = {}, {}, [], {}
        self.rx_hist = deque([0.0] * self.N, maxlen=self.N)
        self.tx_hist = deque([0.0] * self.N, maxlen=self.N)
        self.sig_hist = deque([None] * self.N, maxlen=self.N)
        self.gw = PingSeries(None)
        self.inet = PingSeries(INTERNET_HOST)
        self.rx_rate = self.tx_rate = 0.0
        self.rx_total = self.tx_total = 0
        self.errors = {"rx_errs": 0, "rx_drop": 0, "tx_errs": 0, "tx_drop": 0}
        self.retry_pct = None
        self.dns_ok, self.dns_ms, self.dns_when = None, None, 0.0
        self.started = time.time()
        self.active = True           # NET tab on screen: ping every second; otherwise every few seconds
        self._last_gw = 0.0
        self._prev = self._prev_t = None
        self._prev_station = None
        self._last_link = self._last_info = self._last_station = 0.0
        self._last_spike = self._last_weak = 0.0
        self.refresh_info()
        self.gw.target = self.gateway

    def refresh_info(self):
        iface, gw = nc.default_route()
        if iface:
            self.iface, self.gateway = iface, gw
        info = nc.iface_info(self.iface)
        old = self.info.get("ip")
        self.info, self.dns = info, nc.dns_servers()
        if old and info.get("ip") != old:
            self.log.add(f"IP address changed: {old} -> {info.get('ip')}", "warn")

    def run(self):
        threading.Thread(target=self._internet_loop, daemon=True).start()
        pinger = nc.Pinger()
        while not self.stop_ev.is_set():
            t0 = time.time()
            if t0 - self._last_info > 15:
                self.refresh_info()
                self.gw.target = self.gateway
                self._last_info = t0
            self._counters(t0)
            if self.info.get("wireless"):
                if t0 - self._last_link >= (2 if self.active else 10):
                    self._wifi_link()
                    self._last_link = t0
                if self.active and t0 - self._last_station >= 5:
                    self._station()
                    self._last_station = t0
            with self.lock:
                self.sig_hist.append(self.link.get("signal"))
            if self.active or t0 - self._last_gw >= 3:
                self._last_gw = t0
                self._ping_gateway(pinger)
            self.stop_ev.wait(max(0.05, 1.0 - (time.time() - t0)))

    def _counters(self, now):
        cnt = nc.iface_counters(self.iface)
        if cnt and self._prev and self._prev_t:
            dt = max(0.2, now - self._prev_t)
            drx, dtx = max(0, cnt[0] - self._prev[0]), max(0, cnt[2] - self._prev[2])
            with self.lock:
                self.rx_rate, self.tx_rate = drx / dt, dtx / dt
                self.rx_hist.append(self.rx_rate)
                self.tx_hist.append(self.tx_rate)
                self.rx_total += drx
                self.tx_total += dtx
                for i, key in enumerate(("rx_errs", "rx_drop", "tx_errs", "tx_drop"), start=4):
                    self.errors[key] += max(0, cnt[i] - self._prev[i])
        self._prev, self._prev_t = cnt, now

    def _wifi_link(self):
        link = nc.wifi_link(self.iface)
        old = self.link.get("ssid")
        with self.lock:
            self.link = link
        new = link.get("ssid")
        if old and not new:
            self.log.add(f"Wi-Fi disconnected from {old}", "warn")
        elif new and new != old:
            self.log.add(f"Wi-Fi connected to {new} (channel {link.get('channel')}, {link.get('signal')} dBm)")
        sig = link.get("signal")
        if sig is not None and sig < -75 and time.time() - self._last_weak > 60:
            self._last_weak = time.time()
            self.log.add(f"Weak Wi-Fi signal: {sig} dBm", "warn")

    def _station(self):
        st = nc.station_stats(self.iface)
        prev = self._prev_station
        if st and prev and "tx_retries" in st and "tx_packets" in st:
            dp = st["tx_packets"] - prev.get("tx_packets", 0)
            dr = st["tx_retries"] - prev.get("tx_retries", 0)
            if dp > 20:
                self.retry_pct = max(0.0, 100.0 * dr / dp)
        self.station, self._prev_station = st, st

    def _ping_gateway(self, pinger):
        if not self.gateway:
            return
        rtt, _src, typ = pinger.ping(self.gateway, timeout=0.8)
        ok = rtt is not None and typ == 0
        was = self.gw.streak
        with self.lock:
            self.gw.add(rtt if ok else None)
        if ok and was >= 3:
            self.log.add(f"Gateway {self.gateway} answering again")
        elif not ok and self.gw.streak == 3:
            self.log.add(f"Gateway {self.gateway} not answering (3 pings lost)", "warn")
        if ok and rtt > 150 and time.time() - self._last_spike > 30:
            self._last_spike = time.time()
            self.log.add(f"Latency spike to gateway: {rtt:.0f} ms", "warn")

    def _internet_loop(self):
        pinger = nc.Pinger()
        while not self.stop_ev.is_set():
            t0 = time.time()
            rtt, _src, typ = pinger.ping(INTERNET_HOST, timeout=1.5)
            ok = rtt is not None and typ == 0
            was = self.inet.streak
            with self.lock:
                self.inet.add(rtt if ok else None)
            if ok and was >= 3:
                self.log.add("Internet reachable again")
            elif not ok and self.inet.streak == 3:
                self.log.add(f"Internet unreachable ({INTERNET_HOST} not answering)", "warn")
            if t0 - self.dns_when >= (20 if self.active else 60):
                addrs, err, ms = nc.dns_lookup("example.com", 3.0)
                if self.dns_ok and not addrs:
                    self.log.add(f"DNS lookups failing: {err}", "warn")
                elif self.dns_ok is False and addrs:
                    self.log.add("DNS lookups working again")
                self.dns_ok, self.dns_ms, self.dns_when = bool(addrs), ms, time.time()
            self.stop_ev.wait(max(0.05, (1.0 if self.active else 5.0) - (time.time() - t0)))


class Job:
    """A background task with busy/progress flags."""

    def __init__(self, log):
        self.log, self.busy, self.error, self.when = log, False, None, 0.0
        self.prog = (0, 0)
        self.stop_ev = threading.Event()

    def _spawn(self, fn, *args):
        if self.busy:
            return False
        self.busy, self.error, self.prog = True, None, (0, 0)
        self.stop_ev = threading.Event()

        def wrap():
            try:
                fn(*args)
            except Exception as exc:  # keep the UI alive whatever happens
                self.error = str(exc)
                self.log.add(f"{type(self).__name__} error: {exc}", "error")
            finally:
                self.busy = False
        threading.Thread(target=wrap, daemon=True).start()
        return True

    def _progress(self, done, total):
        self.prog = (done, total)


class WifiScan(Job):
    def __init__(self, mon, log):
        super().__init__(log)
        self.mon, self.nets, self.known = mon, [], set()

    def start(self):
        return self._spawn(self._run)

    def _run(self):
        if not self.mon.info.get("wireless"):
            self.error = f"{self.mon.iface} is not a Wi-Fi interface"
            return
        nets, err = nc.wifi_scan(self.mon.iface)
        if nets is None:
            self.error = err
            self.log.add(f"Wi-Fi scan failed: {err}", "warn")
            return
        nets.sort(key=lambda n: -(n["signal"] if n["signal"] is not None else -999))
        cur = self.mon.link.get("bssid")
        for n in nets:
            n["connected"] = n["connected"] or n["bssid"] == cur
        new = [n for n in nets if n["bssid"] not in self.known]
        self.nets, self.when = nets, time.time()
        if self.known and new:
            self.log.add("New Wi-Fi network(s): " + ", ".join(n["ssid"] or "hidden" for n in new[:5]))
        self.known.update(n["bssid"] for n in nets)
        weak = sum(1 for n in nets if n["security"] in ("Open", "WEP"))
        self.log.add(f"Wi-Fi scan: {len(nets)} networks" + (f", {weak} open or WEP" if weak else ""))


class Discovery(Job):
    def __init__(self, mon, log):
        super().__init__(log)
        self.mon, self.devices, self.known = mon, [], set()
        self.first_seen = {}
        self.method, self.cidr, self.stage = "", "", ""

    def start(self):
        return self._spawn(self._run)

    def _run(self):
        info = self.mon.info
        ip, mask, mac = info.get("ip"), info.get("mask"), info.get("mac")
        if not ip or not mask:
            self.error = "no IP address on " + self.mon.iface
            return
        hosts = nc.subnet_hosts(ip, mask)
        self.cidr = str(ipaddress.IPv4Network(f"{ip}/{max(24, info.get('prefix', 24))}", strict=False))
        self.stage, found = "sweeping", None
        if nc.is_root() and mac:
            try:
                found, self.method = nc.arp_sweep(self.mon.iface, ip, mac, hosts, progress=self._progress), "ARP sweep"
            except OSError as exc:
                self.log.add(f"ARP sweep failed ({exc}); using ping sweep", "warn")
        if found is None:
            found, self.method = nc.ping_sweep(hosts, self.mon.iface, progress=self._progress), "ping sweep"
        found[ip] = (mac or "", 0.0)
        self.stage = "names"
        names = nc.resolve_names(list(found))
        now, devs = time.time(), []
        for a, (m, rtt) in found.items():
            notes = []
            if a == self.mon.gateway:
                notes.append("Gateway")
            if a == ip:
                notes.append("This device")
            if nc.is_random_mac(m):
                notes.append("Random MAC")
            self.first_seen.setdefault(a, now)
            fq = names.get(a, "")
            devs.append({"ip": a, "mac": m, "rtt": rtt, "name": fq.split(".")[0] if fq else "", "fqdn": fq,
                         "notes": notes})
        devs.sort(key=lambda d: ipaddress.ip_address(d["ip"]))
        new = [d for d in devs if d["ip"] not in self.known]
        self.devices, self.when, self.stage = devs, time.time(), "done"
        if self.known and new:
            for d in new[:6]:
                self.log.add(f"New device: {d['ip']} {d['name']} ({d['mac']})")
        self.known.update(d["ip"] for d in devs)
        self.log.add(f"Device scan ({self.method}): {len(devs)} devices on {self.cidr}")


class PortScan(Job):
    NO_BANNER = {443, 465, 853, 993, 995, 8443}

    def __init__(self, log):
        super().__init__(log)
        self.target, self.results, self.stage, self.duration = None, [], "", 0.0
        self.history = {}  # ip -> [(port, service, ms, banner)]

    def start(self, dev):
        self.target, self.results, self.stage = dev, [], "scanning"
        return self._spawn(self._run, dev["ip"])

    def _run(self, ip):
        t0, svc = time.time(), dict(nc.TOP_PORTS)
        found = nc.port_scan(ip, [p for p, _ in nc.TOP_PORTS], progress=self._progress, stop=self.stop_ev)
        self.stage, res = "banners", []
        for port, ms in found:
            if self.stop_ev.is_set():
                break
            banner = "" if port in self.NO_BANNER else nc.grab_banner(ip, port)
            res.append((port, svc.get(port, "?"), ms, banner))
            self.results = list(res)
        self.duration, self.when = time.time() - t0, time.time()
        if self.stop_ev.is_set():            # interrupted: never shown or remembered as a full scan
            self.stage = "stopped"
            self.log.add(f"Port scan {ip} stopped after {self.prog[0]} of {self.prog[1]} ports")
            return
        self.stage = "done"
        self.history[ip] = list(res)
        ports = ", ".join(f"{p}/{s}" for p, s, *_ in res[:8]) or "none"
        self.log.add(f"Port scan {ip}: {len(res)} open of {len(nc.TOP_PORTS)} ({ports})")


class Tools(Job):
    def __init__(self, mon, log):
        super().__init__(log)
        self.mon, self.kind, self.title = mon, "", ""
        self.lines = deque(maxlen=300)
        self.rtts = deque(maxlen=60)
        self.sent = self.lost = 0

    def add(self, line):
        self.lines.append(line)

    def where(self, ip):
        """Label a traceroute hop: your own subnet, a private/CGNAT address (ISP), or internet."""
        try:
            a = ipaddress.ip_address(ip)
            cidr = self.mon.info.get("cidr")
            if cidr and a in ipaddress.ip_network(cidr):
                return "your network"
            if a.is_private or a in ipaddress.ip_network("100.64.0.0/10"):
                return "ISP / private"
        except ValueError:
            pass
        return "internet"

    def start(self, kind, target, title):
        if self.busy:
            return False
        self.kind, self.title = kind, title
        self.lines.clear()
        self.rtts.clear()
        self.sent = self.lost = 0
        fn = {"ping": self._ping, "trace": self._trace, "dns": self._dns}[kind]
        return self._spawn(fn, target)

    def _ping(self, host, count=20):
        p = nc.Pinger()
        self.add(f"PING {host}  ({p.mode} ICMP, {count} probes)")
        for i in range(count):
            if self.stop_ev.is_set():
                break
            t0 = time.time()
            rtt, src, typ = p.ping(host, timeout=1.0)
            self.sent += 1
            if rtt is not None and typ == 0:
                self.rtts.append(rtt)
                self.add(f"reply from {src}  seq={i + 1:<3d} time={rtt:7.2f} ms")
            else:
                self.lost += 1
                self.rtts.append(-1.0)
                self.add(f"seq={i + 1:<3d} request timed out")
            self.stop_ev.wait(max(0.0, 1.0 - (time.time() - t0)))
        p.close()
        ok = [r for r in self.rtts if r >= 0]
        if ok:
            s = (f"{self.sent} sent, {self.lost} lost ({100 * self.lost / self.sent:.0f}%), "
                 f"min/avg/max {min(ok):.1f}/{sum(ok) / len(ok):.1f}/{max(ok):.1f} ms")
        else:
            s = f"{self.sent} sent, all lost"
        self.add("--- " + s)
        self.log.add(f"Ping {host}: {s}")

    def _trace(self, host, max_hops=20):
        p = nc.Pinger()
        if p.mode != "raw":
            self.add("Traceroute needs root (raw sockets). Run it from the Ports menu on the handheld.")
            return
        addr = socket.gethostbyname(host)
        self.add(f"TRACEROUTE {host} ({addr}), max {max_hops} hops")
        hops = 0
        for ttl in range(1, max_hops + 1):
            if self.stop_ev.is_set():
                break
            rtt, src, typ = p.ping(addr, timeout=1.5, ttl=ttl)
            hops = ttl
            if src is None:
                self.add(f"{ttl:2d}  *              no reply")
                continue
            self.add(f"{ttl:2d}  {src:<15s} {rtt:7.1f} ms   {self.where(src)}")
            if typ == 0 or src == addr:
                self.add(f"--- reached {addr} in {ttl} hops")
                break
        p.close()
        self.log.add(f"Traceroute {host}: {hops} hops")

    def _dns(self, _target=None):
        self.add(f"DNS servers: {', '.join(self.mon.dns) or '?'}")
        for name in ("example.com", "wikipedia.org", "github.com", "cloudflare.com", "google.com"):
            if self.stop_ev.is_set():
                break
            addrs, err, ms = nc.dns_lookup(name)
            if addrs:
                self.add(f"{name:<15s} {ms:6.1f} ms  {', '.join(addrs[:2])}")
                self.rtts.append(ms)
            else:
                self.add(f"{name:<15s} {ms:6.1f} ms  FAILED: {err}")
        self.log.add("DNS lookups finished")
