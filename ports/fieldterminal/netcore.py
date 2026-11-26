"""Field Terminal - network data collection (Linux, Python standard library only).

Everything here only *reads* system state or talks to hosts on the network the
handheld is connected to. Raw-socket features (ARP sweep, traceroute, raw ping)
need root, which Ports launchers have on Knulli; each one falls back gracefully.
"""
import errno
import fcntl
import ipaddress
import os
import random
import re
import select
import socket
import struct
import subprocess
import threading
import time

SIOCGIFADDR = 0x8915
SIOCGIFNETMASK = 0x891B


def run(cmd, timeout=8):
    """Run a command, return (returncode, stdout, stderr); never raises."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout, r.stderr
    except (OSError, subprocess.SubprocessError) as exc:
        return -1, "", str(exc)


def is_root():
    return hasattr(os, "geteuid") and os.geteuid() == 0


# --------------------------------------------------------------------------- interfaces

def default_route():
    """(interface, gateway) of the default IPv4 route, from /proc/net/route."""
    try:
        with open("/proc/net/route") as f:
            next(f)
            for line in f:
                p = line.split()
                if len(p) >= 4 and p[1] == "00000000" and int(p[3], 16) & 2:
                    return p[0], socket.inet_ntoa(struct.pack("<L", int(p[2], 16)))
    except (OSError, StopIteration, ValueError):
        pass
    return None, None


def pick_iface():
    iface, gw = default_route()
    if iface:
        return iface, gw
    try:
        names = sorted(os.listdir("/sys/class/net"))
    except OSError:
        return "wlan0", None
    for n in names:
        if n.startswith("wl"):
            return n, None
    for n in names:
        if n != "lo":
            return n, None
    return "lo", None


def _ioctl_addr(iface, req):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        res = fcntl.ioctl(s.fileno(), req, struct.pack("256s", iface[:15].encode()))
        return socket.inet_ntoa(res[20:24])
    except OSError:
        return None
    finally:
        s.close()


def _read(path, default=None):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return default


def iface_info(iface):
    info = {"iface": iface,
            "ip": _ioctl_addr(iface, SIOCGIFADDR),
            "mask": _ioctl_addr(iface, SIOCGIFNETMASK),
            "mac": _read(f"/sys/class/net/{iface}/address"),
            "state": _read(f"/sys/class/net/{iface}/operstate", "?"),
            "wireless": os.path.isdir(f"/sys/class/net/{iface}/wireless")
            or os.path.isdir(f"/sys/class/net/{iface}/phy80211")}
    if info["ip"] and info["mask"]:
        net = ipaddress.IPv4Network(f"{info['ip']}/{info['mask']}", strict=False)
        info["cidr"], info["prefix"] = str(net), net.prefixlen
    return info


def dns_servers():
    out = []
    try:
        with open("/etc/resolv.conf") as f:
            for line in f:
                p = line.split()
                if len(p) >= 2 and p[0] == "nameserver":
                    out.append(p[1])
    except OSError:
        pass
    return out


def iface_counters(iface):
    """(rx_bytes, rx_packets, tx_bytes, tx_packets, rx_errs, rx_drop, tx_errs, tx_drop) from /proc/net/dev."""
    try:
        with open("/proc/net/dev") as f:
            for line in f:
                if ":" in line:
                    name, data = line.split(":", 1)
                    if name.strip() == iface:
                        v = [int(x) for x in data.split()]
                        return v[0], v[1], v[8], v[9], v[2], v[3], v[10], v[11]
    except (OSError, ValueError, IndexError):
        pass
    return None


def station_stats(iface):
    """Link-level counters of the access point we are associated with (`iw ... station dump`).
    Retries and failures show how hard the radio link is struggling."""
    rc, out, _ = run(["iw", "dev", iface, "station", "dump"], 4)
    d = {}
    if rc != 0:
        return d
    for key, pat in (("tx_packets", r"tx packets:\s*(\d+)"), ("tx_retries", r"tx retries:\s*(\d+)"),
                     ("tx_failed", r"tx failed:\s*(\d+)"), ("beacon_loss", r"beacon loss:\s*(\d+)"),
                     ("connected", r"connected time:\s*(\d+)"), ("signal_avg", r"signal avg:\s*(-?\d+)")):
        m = re.search(pat, out)
        if m:
            d[key] = int(m.group(1))
    return d


# --------------------------------------------------------------------------- Wi-Fi

def freq_channel(freq):
    try:
        f = int(float(freq))
    except (TypeError, ValueError):
        return 0
    if f == 2484:
        return 14
    if 2412 <= f <= 2472:
        return (f - 2407) // 5
    if 5150 <= f <= 5900:
        return (f - 5000) // 5
    if 5955 <= f <= 7115:
        return (f - 5950) // 5
    return 0


def freq_band(freq):
    try:
        f = float(freq)
    except (TypeError, ValueError):
        return "?"
    return "2.4 GHz" if f < 3000 else ("5 GHz" if f < 5925 else "6 GHz")


def signal_quality(dbm):
    """-100 dBm -> 0 %, -50 dBm or better -> 100 % (the usual Linux mapping)."""
    if dbm is None:
        return 0
    return max(0, min(100, int(2 * (dbm + 100))))


def wifi_link(iface):
    """Current association from `iw dev <iface> link` (empty dict when not connected)."""
    rc, out, _ = run(["iw", "dev", iface, "link"], 4)
    if rc != 0 or "Connected to" not in out:
        return {}
    d = {}
    m = re.search(r"Connected to ([0-9a-fA-F:]{17})", out)
    d["bssid"] = m.group(1).lower() if m else None
    for key, pat in (("ssid", r"SSID: (.*)"), ("freq", r"freq: ([\d.]+)"),
                     ("signal", r"signal: (-?\d+) dBm"), ("txrate", r"tx bitrate: ([\d.]+) MBit/s"),
                     ("rxrate", r"rx bitrate: ([\d.]+) MBit/s")):
        m = re.search(pat, out)
        if m:
            d[key] = m.group(1).strip()
    for key in ("signal",):
        if key in d:
            d[key] = int(d[key])
    for key in ("freq", "txrate", "rxrate"):
        if key in d:
            d[key] = float(d[key])
    if "freq" in d:
        d["channel"] = freq_channel(d["freq"])
        d["band"] = freq_band(d["freq"])
    return d


def wifi_scan(iface):
    """Nearby networks from `iw dev <iface> scan` -> (list, error)."""
    rc, out, err = run(["iw", "dev", iface, "scan"], 20)
    if rc != 0 or "BSS " not in out:
        rc2, out2, err2 = run(["iw", "dev", iface, "scan", "dump"], 8)
        if rc2 == 0 and "BSS " in out2:
            out = out2
        else:
            msg = (err or err2 or "scan failed").strip().splitlines()
            return None, (msg[-1] if msg else "scan failed")
    nets, cur = [], None
    for line in out.splitlines():
        m = re.match(r"^BSS ([0-9a-fA-F:]{17})", line)
        if m:
            cur = {"bssid": m.group(1).lower(), "ssid": "", "signal": None, "freq": None,
                   "sec": set(), "privacy": False, "connected": "associated" in line, "seen_ms": None}
            nets.append(cur)
            continue
        if cur is None:
            continue
        s = line.strip()
        if s.startswith("freq:"):
            cur["freq"] = float(s.split()[1])
        elif s.startswith("signal:"):
            cur["signal"] = float(s.split()[1])
        elif s.startswith("SSID:"):
            cur["ssid"] = s[5:].strip()
        elif s.startswith("RSN"):
            cur["sec"].add("WPA2")
        elif s.startswith("WPA:"):
            cur["sec"].add("WPA")
        elif "Authentication suites" in s:
            if "SAE" in s:
                cur["sec"].add("WPA3")
            if "802.1X" in s or "IEEE 802.1X" in s:
                cur["sec"].add("ENT")
        elif s.startswith("capability:") and "Privacy" in s:
            cur["privacy"] = True
        elif s.startswith("last seen:") and "ms ago" in s:
            try:
                cur["seen_ms"] = int(s.split()[2])
            except (ValueError, IndexError):
                pass
    for n in nets:
        sec = n.pop("sec")
        if "ENT" in sec:
            n["security"] = "WPA2-Enterprise"
        elif "WPA3" in sec and "WPA2" in sec and n.get("sae_only") is None:
            n["security"] = "WPA2/WPA3"
        elif "WPA3" in sec:
            n["security"] = "WPA3"
        elif "WPA2" in sec:
            n["security"] = "WPA2"
        elif "WPA" in sec:
            n["security"] = "WPA"
        elif n["privacy"]:
            n["security"] = "WEP"
        else:
            n["security"] = "Open"
        if not n["ssid"] or n["ssid"].replace("\\x00", "") == "":
            n["ssid"] = ""
        n["channel"] = freq_channel(n["freq"])
        n["band"] = freq_band(n["freq"])
    return nets, None


# --------------------------------------------------------------------------- ICMP ping / traceroute

def _checksum(data):
    if len(data) % 2:
        data += b"\0"
    s = sum(struct.unpack("!%dH" % (len(data) // 2), data))
    s = (s >> 16) + (s & 0xFFFF)
    s += s >> 16
    return ~s & 0xFFFF


class Pinger:
    """ICMP echo with a raw socket (root), an unprivileged ICMP socket, or `ping`."""

    def __init__(self):
        self.ident = random.randint(1, 0xFFFF)
        self.seq = 0
        self.mode, self.sock = "cmd", None
        for kind, mode in ((socket.SOCK_RAW, "raw"), (socket.SOCK_DGRAM, "dgram")):
            try:
                self.sock = socket.socket(socket.AF_INET, kind, socket.IPPROTO_ICMP)
                self.mode = mode
                break
            except OSError:
                continue

    def close(self):
        if self.sock:
            self.sock.close()

    def ping(self, host, timeout=1.0, ttl=64):
        """-> (rtt_ms | None, replying_ip | None, icmp_type | None). type 0 = echo reply,
        11 = time exceeded (a router on the way, used by traceroute), 3 = unreachable."""
        try:
            addr = socket.gethostbyname(host)
        except OSError:
            return None, None, None
        sock = self.sock
        if self.mode == "cmd" or sock is None:
            _rc, out, _ = run(["ping", "-c", "1", "-W", str(max(1, round(timeout))), addr], int(timeout) + 2)
            m = re.search(r"time[=<]([\d.]+)", out)
            return (float(m.group(1)), addr, 0) if m else (None, None, None)
        self.seq = (self.seq + 1) & 0xFFFF
        payload = struct.pack("!d", time.time()) + b"FieldTerminal-ping-"
        hdr = struct.pack("!BBHHH", 8, 0, 0, self.ident, self.seq)
        pkt = struct.pack("!BBHHH", 8, 0, _checksum(hdr + payload), self.ident, self.seq) + payload
        try:
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_TTL, ttl)
            t0 = time.perf_counter()
            sock.sendto(pkt, (addr, 0))
        except OSError:
            return None, None, None
        end = t0 + timeout
        while True:
            left = end - time.perf_counter()
            if left <= 0:
                return None, None, None
            r, _, _ = select.select([sock], [], [], left)
            if not r:
                return None, None, None
            try:
                data, (src, _) = sock.recvfrom(4096)
            except OSError:
                return None, None, None
            rtt = (time.perf_counter() - t0) * 1000.0
            icmp = data[(data[0] & 0x0F) * 4:] if self.mode == "raw" else data
            if len(icmp) < 8:
                continue
            typ, _code, _sum, rid, rseq = struct.unpack("!BBHHH", icmp[:8])
            if typ == 0 and rseq == self.seq and src == addr and (self.mode == "dgram" or rid == self.ident):
                return rtt, src, 0
            if self.mode == "raw" and typ in (3, 11) and len(icmp) >= 36:
                inner = icmp[8:]
                ihl = (inner[0] & 0x0F) * 4
                if len(inner) >= ihl + 8:
                    _t, _c, _s, iid, iseq = struct.unpack("!BBHHH", inner[ihl:ihl + 8])
                    if iid == self.ident and iseq == self.seq:
                        return rtt, src, typ


# --------------------------------------------------------------------------- LAN discovery

def _mac_bytes(mac):
    return bytes(int(x, 16) for x in mac.split(":"))


def mac_str(b):
    return ":".join(f"{x:02x}" for x in b)


def is_random_mac(mac):
    """Locally administered bit set: phones and laptops use these for privacy."""
    try:
        return bool(int(mac.split(":")[0], 16) & 0x02)
    except (ValueError, AttributeError, IndexError):
        return False


def subnet_hosts(ip, mask):
    net = ipaddress.IPv4Network(f"{ip}/{mask}", strict=False)
    if net.prefixlen < 24:  # never sweep more than a /24
        net = ipaddress.IPv4Network(f"{ip}/24", strict=False)
    return [str(h) for h in net.hosts() if str(h) != ip]


def arp_sweep(iface, my_ip, my_mac, hosts, timeout=2.0, progress=None):
    """Send one ARP who-has per host, collect replies -> {ip: (mac, rtt_ms)}. Needs root."""
    s = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(0x0806))
    found, sent = {}, {}
    try:
        s.bind((iface, 0))
        s.setblocking(False)
        src_mac, src_ip = _mac_bytes(my_mac), socket.inet_aton(my_ip)

        def drain():
            while True:
                try:
                    data = s.recv(2048)
                except (BlockingIOError, InterruptedError):
                    return
                except OSError:
                    return
                if len(data) < 42 or struct.unpack("!H", data[20:22])[0] != 2:
                    continue
                spa = socket.inet_ntoa(data[28:32])
                if spa in sent and spa not in found:
                    found[spa] = (mac_str(data[22:28]), (time.perf_counter() - sent[spa]) * 1000.0)

        for i, ip in enumerate(hosts):
            frame = (b"\xff" * 6 + src_mac + b"\x08\x06" + struct.pack("!HHBBH", 1, 0x0800, 6, 4, 1)
                     + src_mac + src_ip + b"\0" * 6 + socket.inet_aton(ip))
            try:
                s.send(frame)
            except OSError:
                pass
            sent[ip] = time.perf_counter()
            if i % 16 == 15:
                time.sleep(0.012)
                drain()
            if progress:
                progress(i + 1, len(hosts))
        end = time.time() + timeout
        while time.time() < end:
            select.select([s], [], [], 0.1)
            drain()
    finally:
        s.close()
    return found


def read_arp_table(iface=None):
    out = {}
    try:
        with open("/proc/net/arp") as f:
            for line in f.readlines()[1:]:
                p = line.split()
                if (len(p) >= 6 and p[3] != "00:00:00:00:00:00" and p[2] != "0x0"
                        and (iface is None or p[5] == iface)):
                    out[p[0]] = p[3].lower()
    except OSError:
        pass
    return out


def ping_sweep(hosts, iface=None, progress=None):
    """Unprivileged fallback: ping everything in parallel, then read the ARP cache."""
    rtts, lock, done = {}, threading.Lock(), [0]
    queue = list(hosts)

    def worker():
        while True:
            with lock:
                if not queue:
                    return
                ip = queue.pop()
            rc, out, _ = run(["ping", "-c", "1", "-W", "1", ip], 3)
            m = re.search(r"time[=<]([\d.]+)", out)
            with lock:
                if m:
                    rtts[ip] = float(m.group(1))
                done[0] += 1
                if progress:
                    progress(done[0], len(hosts))

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(min(48, len(hosts) or 1))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    arp = read_arp_table(iface)
    return {ip: (arp.get(ip, "??:??:??:??:??:??"), rtts.get(ip)) for ip in set(rtts) | (set(arp) & set(hosts))}


def resolve_names(ips, deadline=3.0):
    """Reverse-DNS names (your router usually knows them), with an overall time limit."""
    names, lock = {}, threading.Lock()

    def one(ip):
        try:
            n = socket.gethostbyaddr(ip)[0]
        except (OSError, UnicodeError):
            return
        with lock:
            names[ip] = n

    threads = [threading.Thread(target=one, args=(ip,), daemon=True) for ip in ips]
    for t in threads:
        t.start()
    end = time.time() + deadline
    for t in threads:
        t.join(max(0.0, end - time.time()))
    with lock:
        return dict(names)


# --------------------------------------------------------------------------- port scan

TOP_PORTS = [
    (21, "ftp"), (22, "ssh"), (23, "telnet"), (25, "smtp"), (53, "dns"), (80, "http"), (81, "http-alt"),
    (88, "kerberos"), (110, "pop3"), (111, "rpcbind"), (135, "msrpc"), (139, "netbios"), (143, "imap"),
    (389, "ldap"), (443, "https"), (445, "smb"), (465, "smtps"), (515, "printer"), (548, "afp"),
    (554, "rtsp"), (587, "submission"), (631, "ipp"), (853, "dns-over-tls"), (873, "rsync"),
    (902, "vmware"), (993, "imaps"), (995, "pop3s"), (1080, "socks"), (1194, "openvpn"),
    (1433, "mssql"), (1521, "oracle"), (1723, "pptp"), (1883, "mqtt"), (2049, "nfs"), (2121, "ftp-alt"),
    (3000, "dev-http"), (3128, "proxy"), (3306, "mysql"), (3389, "rdp"), (3689, "daap"),
    (4000, "dev-http"), (5000, "upnp/http"), (5001, "http-alt"), (5060, "sip"), (5222, "xmpp"),
    (5357, "wsd"), (5432, "postgres"), (5555, "adb"), (5900, "vnc"), (6379, "redis"),
    (6667, "irc"), (7000, "airplay"), (7547, "tr-069"), (8000, "http-alt"), (8008, "http-alt"),
    (8009, "chromecast"), (8080, "http-proxy"), (8081, "http-alt"), (8088, "http-alt"),
    (8123, "home-assistant"), (8200, "dlna"), (8443, "https-alt"), (8888, "http-alt"),
    (9000, "http-alt"), (9090, "web-admin"), (9100, "jetdirect"), (9200, "elasticsearch"),
    (10000, "webmin"), (11211, "memcached"), (27017, "mongodb"), (32400, "plex"),
    (49152, "upnp"), (62078, "iphone-sync"),
]
HTTP_PORTS = {80, 81, 3000, 4000, 5000, 5001, 7000, 8000, 8008, 8080, 8081, 8088, 8123, 8200, 8888, 9000, 9090}


def is_local(ip):
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return a.is_private or a.is_link_local


def port_scan(ip, ports, timeout=1.0, concurrency=48, progress=None, stop=None):
    """TCP connect() scan -> sorted [(port, ms)]. Only for hosts on the local network."""
    if not is_local(ip):
        raise ValueError("port scans are limited to your local network")
    found, pending, active, done = [], list(ports), {}, 0
    while pending or active:
        if stop is not None and stop.is_set():
            break
        while pending and len(active) < concurrency:
            port = pending.pop(0)
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.setblocking(False)
            err = s.connect_ex((ip, port))
            if err in (0, errno.EINPROGRESS, errno.EWOULDBLOCK, errno.EAGAIN):
                active[s] = (port, time.time())
            else:
                s.close()
                done += 1
        if not active:
            continue
        _, writable, _ = select.select([], list(active), [], 0.1)
        now = time.time()
        for s in writable:
            port, t0 = active.pop(s)
            if s.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR) == 0:
                found.append((port, (now - t0) * 1000.0))
            s.close()
            done += 1
        for s, (port, t0) in list(active.items()):
            if now - t0 > timeout:
                active.pop(s)
                s.close()
                done += 1
        if progress:
            progress(done, len(ports))
    for s in active:
        s.close()
    return sorted(found)


def grab_banner(ip, port, timeout=1.2):
    """What the service says about itself (SSH/FTP/SMTP greet first; HTTP gets a HEAD request)."""
    try:
        with socket.create_connection((ip, port), timeout=timeout) as s:
            s.settimeout(timeout)
            if port in HTTP_PORTS:
                s.sendall(b"HEAD / HTTP/1.0\r\nHost: " + ip.encode() + b"\r\nUser-Agent: FieldTerminal\r\n\r\n")
            data = s.recv(512)
    except OSError:
        return ""
    text = data.decode("latin-1", "replace")
    if text.startswith("HTTP/"):
        status = text.split("\r\n", 1)[0]
        m = re.search(r"(?im)^server:\s*(.+?)\s*$", text)
        return status + (f"  |  {m.group(1)}" if m else "")
    first = text.strip().splitlines()[0] if text.strip() else ""
    return re.sub(r"[^\x20-\x7e]", ".", first)[:90]


# --------------------------------------------------------------------------- DNS

def dns_lookup(name, timeout=4.0):
    """-> (addresses | None, error | None, ms)"""
    result = {}

    def go():
        try:
            result["addrs"] = sorted({ai[4][0] for ai in socket.getaddrinfo(name, None)})
        except (OSError, UnicodeError) as exc:
            result["err"] = str(exc)

    t0 = time.perf_counter()
    th = threading.Thread(target=go, daemon=True)
    th.start()
    th.join(timeout)
    ms = (time.perf_counter() - t0) * 1000.0
    if "addrs" in result:
        return result["addrs"], None, ms
    return None, result.get("err", "timed out"), ms
