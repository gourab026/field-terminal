"""Field Terminal - live packet capture and decoding (AF_PACKET, standard library only).

Captures on the Wi-Fi interface. In managed mode a Wi-Fi card only hands the OS the
handheld's own traffic plus broadcasts/multicast, so that is what is shown and saved.
"""
import ipaddress
import socket
import struct
import threading
import time
from collections import Counter, deque

ETH_P_ALL = 0x0003
PACKET_OUTGOING = 4

DHCP_TYPES = {1: "DISCOVER", 2: "OFFER", 3: "REQUEST", 4: "DECLINE", 5: "ACK", 6: "NAK", 7: "RELEASE", 8: "INFORM"}
DNS_TYPES = {1: "A", 2: "NS", 5: "CNAME", 6: "SOA", 12: "PTR", 15: "MX", 16: "TXT", 28: "AAAA",
             33: "SRV", 47: "NSEC", 64: "SVCB", 65: "HTTPS", 255: "ANY"}
ICMP_TYPES = {0: "Echo reply", 3: "Destination unreachable", 5: "Redirect", 8: "Echo request", 11: "Time exceeded"}
ICMP6_TYPES = {1: "Destination unreachable", 3: "Time exceeded", 128: "Echo request", 129: "Echo reply",
               133: "Router solicitation", 134: "Router advertisement", 135: "Neighbor solicitation",
               136: "Neighbor advertisement", 143: "MLDv2 report"}
TCP_PORT_PROTO = {22: "SSH", 23: "Telnet", 25: "SMTP", 53: "DNS", 80: "HTTP", 110: "POP3", 139: "SMB",
                  143: "IMAP", 443: "TLS", 445: "SMB", 853: "TLS", 993: "TLS", 3389: "RDP", 5900: "VNC",
                  8080: "HTTP", 8443: "TLS", 1883: "MQTT"}
UDP_PORT_PROTO = {53: "DNS", 67: "DHCP", 68: "DHCP", 123: "NTP", 137: "NetBIOS", 138: "NetBIOS",
                  443: "QUIC", 1900: "SSDP", 3702: "WSD", 5353: "mDNS", 5355: "LLMNR"}


class Pkt:
    __slots__ = ("t", "src", "dst", "proto", "length", "info", "out")

    def __init__(self, t, src, dst, proto, length, info, out):
        self.t, self.src, self.dst, self.proto, self.length, self.info, self.out = t, src, dst, proto, length, info, out


def _mac(b):
    return ":".join(f"{x:02x}" for x in b)


def _dns_name(buf, off):
    labels, jumped, end = [], False, None
    for _ in range(80):
        if off >= len(buf):
            break
        ln = buf[off]
        if ln == 0:
            off += 1
            break
        if ln & 0xC0 == 0xC0:
            if off + 1 >= len(buf):
                break
            if not jumped:
                end = off + 2
            jumped, off = True, ((ln & 0x3F) << 8) | buf[off + 1]
            continue
        labels.append(buf[off + 1:off + 1 + ln].decode("ascii", "replace"))
        off += 1 + ln
    return ".".join(labels), (end if end is not None else off)


def parse_dns(p):
    """-> (name, qtype, is_response, answer_count) or None."""
    if len(p) < 17:
        return None
    _tid, flags, qd, an = struct.unpack("!HHHH", p[:8])
    if qd == 0:
        return None
    name, off = _dns_name(p, 12)
    qtype = struct.unpack("!H", p[off:off + 2])[0] if off + 2 <= len(p) else 0
    return name or "<root>", DNS_TYPES.get(qtype, str(qtype)), bool(flags & 0x8000), an


def dns_answers(p):
    """Addresses in a DNS response -> (query name, [ip, ...])."""
    try:
        _tid, flags, qd, an = struct.unpack("!HHHH", p[:8])
        if not flags & 0x8000 or an == 0:
            return None, []
        off, qname = 12, None
        for _ in range(min(qd, 4)):
            name, off = _dns_name(p, off)
            off += 4
            qname = qname or name
        ips = []
        for _ in range(min(an, 32)):
            _n, off = _dns_name(p, off)
            typ, _cls, _ttl, rdlen = struct.unpack("!HHIH", p[off:off + 10])
            off += 10
            rd = p[off:off + rdlen]
            off += rdlen
            if typ == 1 and rdlen == 4:
                ips.append(socket.inet_ntoa(rd))
            elif typ == 28 and rdlen == 16:
                ips.append(str(ipaddress.IPv6Address(rd)))
        return qname, ips
    except (struct.error, IndexError, ValueError):
        return None, []


def parse_sni(p):
    """Server name from a TLS ClientHello (visible even though the rest is encrypted)."""
    try:
        if len(p) < 43 or p[0] != 0x16 or p[5] != 0x01:
            return None
        i = 9 + 2 + 32
        i += 1 + p[i]                                   # session id
        i += 2 + struct.unpack("!H", p[i:i + 2])[0]     # cipher suites
        i += 1 + p[i]                                   # compression methods
        end = min(len(p), i + 2 + struct.unpack("!H", p[i:i + 2])[0])
        i += 2
        while i + 4 <= end:
            etype, elen = struct.unpack("!HH", p[i:i + 4])
            i += 4
            if etype == 0 and i + 5 <= end:
                nlen = struct.unpack("!H", p[i + 3:i + 5])[0]
                return p[i + 5:i + 5 + nlen].decode("ascii", "replace")
            i += elen
    except (IndexError, struct.error):
        return None
    return None


def _tcp_flags(f):
    names = [(0x02, "SYN"), (0x10, "ACK"), (0x01, "FIN"), (0x04, "RST"), (0x08, "PSH"), (0x20, "URG")]
    return ",".join(n for bit, n in names if f & bit) or "-"


def _l4(proto_num, seg, src, dst):
    """-> (proto, info, dns_query_name | None, learned [(ip, hostname)])"""
    if proto_num == 6 and len(seg) >= 20:
        sport, dport, _seq, _ack, offres, flags = struct.unpack("!HHIIBB", seg[:14])
        payload = seg[(offres >> 4) * 4:]
        proto = TCP_PORT_PROTO.get(dport) or TCP_PORT_PROTO.get(sport) or "TCP"
        base = f"{sport} → {dport} [{_tcp_flags(flags)}]" + (f" len={len(payload)}" if payload else "")
        if proto == "TLS" and payload:
            sni = parse_sni(payload)
            if sni:
                return "TLS", f"ClientHello → {sni}", None, [(dst, sni)]
            if payload[:1] == b"\x17":
                return "TLS", f"{sport} → {dport} encrypted data {len(payload)} B", None, []
        if payload[:4] in (b"GET ", b"POST", b"HEAD", b"PUT ") or payload[:7] == b"OPTIONS":
            text = payload[:400].decode("latin-1", "replace")
            first = text.split("\r\n", 1)[0]
            host = ""
            for line in text.split("\r\n")[1:]:
                if line.lower().startswith("host:"):
                    host = line[5:].strip()
            parts = first.split(" ")
            return "HTTP", f"{parts[0]} {host}{parts[1] if len(parts) > 1 else ''}", None, []
        if payload[:5] == b"HTTP/":
            return "HTTP", payload[:60].decode("latin-1", "replace").split("\r\n", 1)[0], None, []
        if proto == "SSH" and payload[:4] == b"SSH-":
            return "SSH", payload[:60].decode("latin-1", "replace").strip(), None, []
        return proto, base, None, []
    if proto_num == 17 and len(seg) >= 8:
        sport, dport, _ln = struct.unpack("!HHH", seg[:6])
        payload = seg[8:]
        proto = UDP_PORT_PROTO.get(dport) or UDP_PORT_PROTO.get(sport) or "UDP"
        if proto in ("DNS", "mDNS", "LLMNR"):
            d = parse_dns(payload)
            if d:
                name, qtype, resp, an = d
                if resp:
                    learned = []
                    if proto == "DNS":
                        qn, ips = dns_answers(payload)
                        learned = [(ip, qn) for ip in ips if qn]
                    return proto, f"Response {qtype} {name}" + (f" ({an} answers)" if an else " (no answer)"), None, learned
                return proto, f"Query {qtype} {name}", (name if proto == "DNS" else None), []
        if proto == "DHCP" and len(payload) > 240 and payload[236:240] == b"\x63\x82\x53\x63":
            i = 240
            while i + 2 <= len(payload) and payload[i] != 255:
                if payload[i] == 0:
                    i += 1
                    continue
                if payload[i] == 53 and i + 2 < len(payload):
                    return "DHCP", f"DHCP {DHCP_TYPES.get(payload[i + 2], payload[i + 2])}", None, []
                i += 2 + payload[i + 1]
        if proto in ("SSDP", "WSD") and payload:
            return proto, payload[:70].decode("latin-1", "replace").split("\r\n", 1)[0], None, []
        return proto, f"{sport} → {dport} len={len(payload)}", None, []
    if proto_num == 1 and len(seg) >= 4:
        return "ICMP", ICMP_TYPES.get(seg[0], f"type {seg[0]}"), None, []
    if proto_num == 58 and len(seg) >= 4:
        return "ICMPv6", ICMP6_TYPES.get(seg[0], f"type {seg[0]}"), None, []
    if proto_num == 2:
        return "IGMP", "Multicast group membership", None, []
    return "Other", f"IP protocol {proto_num}", None, []


def decode(frame):
    """-> (src, dst, proto, info, ip_src, ip_dst, dns_query, learned) or None."""
    if len(frame) < 14:
        return None
    etype, off = struct.unpack("!H", frame[12:14])[0], 14
    if etype == 0x8100 and len(frame) >= 18:
        etype, off = struct.unpack("!H", frame[16:18])[0], 18
    smac, dmac = _mac(frame[6:12]), _mac(frame[0:6])
    if etype == 0x0806 and len(frame) >= off + 28:
        a = frame[off:]
        op = struct.unpack("!H", a[6:8])[0]
        spa, tpa = socket.inet_ntoa(a[14:18]), socket.inet_ntoa(a[24:28])
        info = f"Who has {tpa}? Tell {spa}" if op == 1 else f"{spa} is at {_mac(a[8:14])}"
        return spa, tpa, "ARP", info, None, None, None, []
    if etype == 0x888E:
        return smac, dmac, "EAPOL", "Wi-Fi key handshake (EAPOL)", None, None, None, []
    if etype == 0x0800 and len(frame) >= off + 20:
        ip = frame[off:]
        ihl = (ip[0] & 0x0F) * 4
        src, dst = socket.inet_ntoa(ip[12:16]), socket.inet_ntoa(ip[16:20])
        proto, info, q, learned = _l4(ip[9], ip[ihl:], src, dst)
        return src, dst, proto, info, src, dst, q, learned
    if etype == 0x86DD and len(frame) >= off + 40:
        ip = frame[off:]
        nh, seg = ip[6], ip[40:]
        src = str(ipaddress.IPv6Address(ip[8:24]))
        dst = str(ipaddress.IPv6Address(ip[24:40]))
        for _ in range(4):  # skip extension headers (hop-by-hop is common for MLD)
            if nh in (0, 43, 60) and len(seg) >= 8:
                nh, seg = seg[0], seg[(seg[1] + 1) * 8:]
            else:
                break
        proto, info, q, learned = _l4(nh, seg, src, dst)
        return src, dst, proto, info, src, dst, q, learned
    return smac, dmac, "Other", f"EtherType 0x{etype:04x}", None, None, None, []


class Capture:
    """Live capture. Runs only between start() and stop() (Field Terminal: while NET is on screen);
    the statistics survive a stop, so coming back to NET continues where it left off."""
    HIST = 120

    def __init__(self, iface, my_ip, log):
        self._thread = None
        self.iface, self.my_ip, self.log = iface, my_ip, log
        self.lock = threading.Lock()
        self.capturing = threading.Event()
        self.stop_ev = threading.Event()
        self.error = None
        self.saved = []
        self.reset()

    def start(self):
        if self._thread and self._thread.is_alive():
            if not self.stop_ev.is_set():
                return
            self._thread.join(1.0)
            if self._thread.is_alive():
                return
        self.stop_ev = threading.Event()
        self.error = None
        self._thread = threading.Thread(target=self.run, daemon=True)
        self._thread.start()

    def stop(self):
        self.stop_ev.set()

    @property
    def running(self):
        return bool(self._thread and self._thread.is_alive() and not self.stop_ev.is_set())

    def reset(self):
        self.packets = deque(maxlen=400)
        self.raw = deque(maxlen=6000)
        self.total_pkts = self.total_bytes = 0
        self.proto_pkts, self.proto_bytes, self.talkers = Counter(), Counter(), Counter()
        self.dns = deque(maxlen=60)
        self.pps_hist = deque([0] * self.HIST, maxlen=self.HIST)
        self.bps_hist = deque([0] * self.HIST, maxlen=self.HIST)
        self.pps = self.bps = 0
        self._sec_pkts = self._sec_bytes = 0
        self._sec_t = time.time()
        self.local_hosts = set()
        self.names = {}
        self.version = 0

    def clear(self):
        with self.lock:
            saved = self.saved
            self.reset()
            self.saved = saved

    def _tick(self, now):
        while now - self._sec_t >= 1.0:
            self.pps, self.bps = self._sec_pkts, self._sec_bytes
            self.pps_hist.append(self._sec_pkts)
            self.bps_hist.append(self._sec_bytes)
            self._sec_pkts = self._sec_bytes = 0
            self._sec_t += 1.0
            self.version += 1
            if now - self._sec_t > 5:  # long pause: catch up in one step
                self._sec_t = now

    def ingest(self, frame, outgoing, now):
        d = decode(frame)
        if d is None:
            return
        src, dst, proto, info, ipsrc, ipdst, q, learned = d
        for ip, name in learned:
            if len(self.names) > 4000:
                self.names.clear()
            self.names[ip] = name
        n = len(frame)
        self.packets.append(Pkt(now, src, dst, proto, n, info, outgoing))
        self.raw.append((now, frame))
        self.total_pkts += 1
        self.total_bytes += n
        self._sec_pkts += 1
        self._sec_bytes += n
        self.proto_pkts[proto] += 1
        self.proto_bytes[proto] += n
        if ipsrc and ipdst:
            other = ipdst if ipsrc == self.my_ip else ipsrc
            self.talkers[other] += n
            for host in (ipsrc, ipdst):
                if host != self.my_ip and ":" not in host and host not in self.local_hosts and _is_lan(host):
                    self.local_hosts.add(host)
                    if len(self.local_hosts) <= 64:
                        self.log.add(f"Traffic: first packet seen from/to {host} ({proto})")
        if q:
            self.dns.append((now, q))
        self.version += 1

    def run(self):
        try:
            sock = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(ETH_P_ALL))
            sock.bind((self.iface, 0))
            sock.settimeout(0.25)
        except OSError as exc:
            self.error = "needs root (raw sockets)" if isinstance(exc, PermissionError) else str(exc)
            self.log.add(f"Packet capture unavailable: {self.error}", "warn")
            return
        while not self.stop_ev.is_set():
            try:
                frame, addr = sock.recvfrom(65535)
            except socket.timeout:
                frame, addr = None, None
            except OSError as exc:
                self.error = str(exc)
                time.sleep(1)
                continue
            now = time.time()
            with self.lock:
                self._tick(now)
                if frame is not None and addr is not None and self.capturing.is_set():
                    self.ingest(frame, addr[2] == PACKET_OUTGOING, now)
        sock.close()

    def save_pcap(self, path):
        """Write the buffered packets as a classic libpcap file (opens in Wireshark)."""
        with self.lock:
            frames = list(self.raw)
        with open(path, "wb") as f:
            f.write(struct.pack("<IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, 1))
            for t, data in frames:
                sec = int(t)
                f.write(struct.pack("<IIII", sec, int((t - sec) * 1e6), len(data), len(data)))
                f.write(data)
        self.saved.append(path)
        return len(frames)


def _is_lan(ip):
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return (a.is_private or a.is_link_local) and not a.is_multicast
