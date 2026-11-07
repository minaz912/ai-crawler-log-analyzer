"""Check that a request claiming to be a bot really comes from that bot's operator.

User agents are trivial to fake. Vendors publish either IP ranges (JSON) or a reverse-DNS
suffix; reverse DNS must be forward-confirmed (PTR -> hostname -> A/AAAA back to the same
IP), otherwise anyone controlling their own PTR record could claim to be googlebot.com.

Verdicts:
  verified      IP inside the vendor's published ranges, or forward-confirmed rDNS matches
  spoofed       vendor publishes a method and this IP fails it
  unverifiable  vendor publishes nothing, so we cannot tell
  unchecked     a method exists but wasn't run (ranges not downloaded / --rdns not enabled)
"""

from __future__ import annotations

import bisect
import ipaddress
import json
import re
import socket
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import duckdb

from .bots import BY_NAME, IP_LISTS

USER_AGENT = "aicrawl-log-analyzer/0.1 (+https://github.com/)"
_CIDR_RE = re.compile(r"^[0-9a-fA-F:.]+/\d{1,3}$")


# ---------------------------------------------------------------- range download

def parse_ranges(payload: str) -> list[str]:
    """Extract CIDRs from a vendor file. Most use Google's {"prefixes": [{"ipv4Prefix": ...}]}
    shape, but walk the whole document so small format variations still work."""
    found: list[str] = []

    def walk(node):
        if isinstance(node, dict):
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
        elif isinstance(node, str) and _CIDR_RE.match(node.strip()):
            try:
                found.append(str(ipaddress.ip_network(node.strip(), strict=False)))
            except ValueError:
                pass

    walk(json.loads(payload))
    return found


def fetch_ranges(con: duckdb.DuckDBPyConnection, timeout: float = 15) -> dict[str, str]:
    """Download every vendor list into ip_ranges. Returns {list_name: status message}."""
    status: dict[str, str] = {}
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for name, url in IP_LISTS.items():
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                cidrs = parse_ranges(resp.read().decode("utf-8", "replace"))
            if not cidrs:
                raise ValueError("no CIDRs in response")
        except Exception as e:  # keep previously cached ranges on any failure
            status[name] = f"FAILED ({e.__class__.__name__}: {e}) - kept cached copy"
            continue
        con.execute("DELETE FROM ip_ranges WHERE list_name = ?", [name])
        con.executemany("INSERT INTO ip_ranges VALUES (?, ?, ?, 'vendor')", [(name, c, now) for c in cidrs])
        status[name] = f"{len(cidrs)} prefixes"
    return status


# ---------------------------------------------------------------- range lookup

class RangeIndex:
    """Merged, sorted integer intervals per list for O(log n) membership tests."""

    def __init__(self, rows: list[tuple[str, str]]):
        raw: dict[tuple[str, int], list[tuple[int, int]]] = {}
        for list_name, cidr in rows:
            net = ipaddress.ip_network(cidr, strict=False)
            raw.setdefault((list_name, net.version), []).append(
                (int(net.network_address), int(net.broadcast_address)))
        self.lists = {name for name, _ in raw}
        self._idx: dict[tuple[str, int], tuple[list[int], list[int]]] = {}
        for key, spans in raw.items():
            spans.sort()
            merged: list[list[int]] = []
            for lo, hi in spans:
                if merged and lo <= merged[-1][1] + 1:
                    merged[-1][1] = max(merged[-1][1], hi)
                else:
                    merged.append([lo, hi])
            self._idx[key] = ([m[0] for m in merged], [m[1] for m in merged])

    def contains(self, list_name: str, ip: str) -> bool:
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            return False
        if addr.version == 6 and addr.ipv4_mapped:
            addr = addr.ipv4_mapped
        idx = self._idx.get((list_name, addr.version))
        if not idx:
            return False
        starts, ends = idx
        i = bisect.bisect_right(starts, int(addr)) - 1
        return i >= 0 and int(addr) <= ends[i]


# ---------------------------------------------------------------- reverse DNS

def forward_confirmed_rdns(ip: str) -> tuple[str | None, bool]:
    try:
        host = socket.gethostbyaddr(ip)[0].rstrip(".").lower()
    except (OSError, UnicodeError):
        return None, False
    try:
        infos = socket.getaddrinfo(host, None)
    except (OSError, UnicodeError):
        return host, False
    return host, any(info[4][0] == ip for info in infos)


def _rdns_lookup(con, ips: list[str], workers: int = 32) -> dict[str, tuple[str | None, bool]]:
    cached = {ip: (h, c) for ip, h, c in con.execute(
        "SELECT ip, hostname, confirmed FROM rdns_cache").fetchall()}
    todo = [ip for ip in ips if ip not in cached]
    if todo:
        socket.setdefaulttimeout(5)
        with ThreadPoolExecutor(workers) as pool:
            results = list(pool.map(forward_confirmed_rdns, todo))
        con.executemany("INSERT OR REPLACE INTO rdns_cache VALUES (?, ?, ?)",
                        [(ip, h, c) for ip, (h, c) in zip(todo, results)])
        cached.update(zip(todo, results))
    return cached


def _suffix_ok(host: str | None, suffixes: tuple[str, ...]) -> bool:
    return bool(host) and any(host == s or host.endswith("." + s) for s in suffixes)


# ---------------------------------------------------------------- main entry

def verify(con: duckdb.DuckDBPyConnection, use_rdns: bool = False) -> dict[str, int]:
    """Recompute ip_verdict for every (ip, bot) pair seen in the logs."""
    index = RangeIndex(con.execute("SELECT list_name, cidr FROM ip_ranges").fetchall())
    pairs = con.execute("""
        SELECT DISTINCT r.ip, u.bot FROM requests r JOIN ua_class u USING (ua)
        WHERE u.bot IS NOT NULL AND u.bot <> '~other-bot'
    """).fetchall()

    rdns: dict[str, tuple[str | None, bool]] = {}
    if use_rdns:
        need = sorted({ip for ip, bot in pairs if BY_NAME[bot].rdns and not (
            BY_NAME[bot].ip_list and index.contains(BY_NAME[bot].ip_list, ip))})
        rdns = _rdns_lookup(con, need)

    rows = []
    for ip, name in pairs:
        bot = BY_NAME[name]
        verdict, method, detail = "unverifiable", None, "vendor publishes no IP list or rDNS domain"
        list_loaded = bot.ip_list in index.lists if bot.ip_list else False
        if list_loaded and index.contains(bot.ip_list, ip):
            verdict, method, detail = "verified", "ip-list", bot.ip_list
        elif bot.rdns and use_rdns:
            host, confirmed = rdns.get(ip, (None, False))
            if _suffix_ok(host, bot.rdns) and confirmed:
                verdict, method, detail = "verified", "rdns", host
            else:
                why = "no PTR record" if not host else (
                    f"{host} not forward-confirmed" if _suffix_ok(host, bot.rdns) else f"PTR is {host}")
                verdict, method, detail = "spoofed", "rdns", why
        elif list_loaded:
            verdict, method, detail = "spoofed", "ip-list", f"not in {bot.ip_list}"
        elif bot.verifiable:
            verdict, method = "unchecked", None
            detail = "run `aicrawl ranges`" if bot.ip_list else "run verify with --rdns"
        rows.append((ip, name, verdict, method, detail))

    con.execute("DELETE FROM ip_verdict")
    if rows:
        con.executemany("INSERT INTO ip_verdict VALUES (?, ?, ?, ?, ?)", rows)
    counts: dict[str, int] = {}
    for r in rows:
        counts[r[2]] = counts.get(r[2], 0) + 1
    return counts
