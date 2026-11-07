"""aicrawl - which AI bots read your site, what they read, and whether they're allowed to."""

from __future__ import annotations

import argparse
import re
import sys
import time
import urllib.request
from pathlib import Path

from . import analysis, ingest, sample, verify
from .bots import BY_NAME
from .db import connect
from .robots import Robots, parse_llms_txt


def load_text(src: str | None, required: bool = True) -> str | None:
    if not src:
        return None
    try:
        if re.match(r"https?://", src):
            req = urllib.request.Request(src, headers={"User-Agent": verify.USER_AGENT})
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.read().decode("utf-8", "replace")
        return Path(src).read_text(errors="replace")
    except Exception as e:
        if required:
            raise SystemExit(f"could not read {src}: {e}")
        return None


def load_sitemap(src: str, depth: int = 0) -> list[str]:
    """Follow sitemap indexes one level deep."""
    xml = load_text(src, required=depth == 0) or ""
    if "<sitemapindex" in xml and depth == 0:
        paths: list[str] = []
        for loc in re.findall(r"<loc>\s*([^<]+?)\s*</loc>", xml)[:50]:
            paths += load_sitemap(loc, depth + 1)
        return paths
    return analysis.sitemap_paths(xml)


def cmd_ranges(con, a):
    for name, msg in verify.fetch_ranges(con).items():
        print(f"  {name:28} {msg}")
    print("Re-run `aicrawl verify` to apply.")


def cmd_ingest(con, a):
    for f in a.files:
        t = time.perf_counter()
        fmt, n = ingest.ingest(con, Path(f), a.format, a.host, replace=not a.append)
        print(f"  {f}: {n:,} requests ({fmt}) in {time.perf_counter() - t:.1f}s")
    cmd_verify(con, a)


def cmd_verify(con, a):
    counts = verify.verify(con, use_rdns=getattr(a, "rdns", False))
    print("  IP/bot pairs: " + ", ".join(f"{v} {k}" for k, v in sorted(counts.items())))


def cmd_sample(con, a):
    info = sample.generate(con, Path(a.outdir), days=a.days, fmt=a.format, seed=a.seed, scale=a.scale)
    print(f"  wrote {info['events']} log lines to {info['log']} (bot IPs from {info['ranges']} ranges)")
    print(f"  plus robots.txt, llms.txt, sitemap.xml in {a.outdir}/")


def _site_inputs(a):
    robots_src, llms_src, sitemap_src = a.robots, a.llms, a.sitemap
    if a.site:
        base = a.site.rstrip("/")
        robots_src = robots_src or f"{base}/robots.txt"
        llms_src = llms_src or f"{base}/llms.txt"
    robots_txt = load_text(robots_src, required=not a.site)
    robots = Robots(robots_txt) if robots_txt is not None else None
    if a.site and not sitemap_src:
        sitemap_src = (robots.sitemaps[0] if robots and robots.sitemaps else f"{a.site.rstrip('/')}/sitemap.xml")
    sitemap = load_sitemap(sitemap_src) if sitemap_src else None
    llms_txt = load_text(llms_src, required=not a.site)
    llms = parse_llms_txt(llms_txt) if llms_txt else None
    return robots, sitemap, llms


def cmd_report(con, a):
    from . import htmlreport, textreport
    robots, sitemap, llms = _site_inputs(a)
    R = analysis.analyze(con, robots, sitemap, llms, top=a.top, since=a.since, until=a.until)
    if R["summary"]["requests"] == 0:
        raise SystemExit("No requests in the database. Run `aicrawl ingest <logfile>` first.")
    print(textreport.render(R, a.top))
    if a.html:
        Path(a.html).write_text(htmlreport.render(R))
        print(f"HTML report written to {a.html}")


def cmd_demo(con, a):
    out = Path(a.outdir)
    print("1. Downloading vendor IP ranges")
    cmd_ranges(con, a)
    print("2. Generating sample logs")
    a.days, a.format, a.seed, a.scale = 30, "combined", 7, 1.0
    cmd_sample(con, a)
    print("3. Ingesting")
    a.files, a.host, a.append = [str(out / "access.log")], None, False
    cmd_ingest(con, a)
    print("4. Report")
    a.robots, a.llms, a.sitemap, a.site = str(out / "robots.txt"), str(out / "llms.txt"), str(out / "sitemap.xml"), None
    a.html = a.html or str(out / "report.html")
    cmd_report(con, a)


def cmd_robots(con, a):
    robots = Robots(load_text(a.robots))
    tokens = a.bot or ["GPTBot", "OAI-SearchBot", "ChatGPT-User", "ClaudeBot", "Claude-SearchBot", "Claude-User",
                       "PerplexityBot", "Googlebot"]
    for path in a.paths:
        p, _, q = path.partition("?")
        print(path)
        for t in tokens:
            v = robots.check(BY_NAME[t].token if t in BY_NAME else t, p, q or None)
            rule = f"line {v.rule.line}: {'Allow' if v.rule.allow else 'Disallow'}: {v.rule.pattern}" if v.rule else "no rule"
            print(f"  {t:18} {'allow' if v.allowed else 'BLOCK':6} group={v.group:16} {rule}")
    if robots.lint:
        print("\nlint:")
        for l in robots.lint:
            print(f"  {l.severity:7} line {l.line}: {l.message}")


def main(argv=None):
    p = argparse.ArgumentParser(prog="aicrawl", description=__doc__)
    p.add_argument("--db", default="aicrawl.duckdb", help="DuckDB file (default: aicrawl.duckdb)")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("ranges", help="download vendor IP ranges").set_defaults(fn=cmd_ranges)

    s = sub.add_parser("ingest", help="load log files (nginx/Apache, Cloudflare, CloudFront; .gz ok)")
    s.add_argument("files", nargs="+")
    s.add_argument("--format", default="auto", choices=["auto", *ingest.INGESTERS])
    s.add_argument("--host", help="host name to record (combined logs don't carry one)")
    s.add_argument("--append", action="store_true", help="don't replace rows previously loaded from the same file")
    s.add_argument("--rdns", action="store_true", help="also verify with forward-confirmed reverse DNS")
    s.set_defaults(fn=cmd_ingest)

    s = sub.add_parser("verify", help="re-check bot IPs against vendor ranges")
    s.add_argument("--rdns", action="store_true", help="use forward-confirmed reverse DNS where vendors support it")
    s.set_defaults(fn=cmd_verify)

    s = sub.add_parser("sample", help="generate sample logs + robots.txt/llms.txt/sitemap.xml")
    s.add_argument("outdir", nargs="?", default="sample")
    s.add_argument("--days", type=int, default=30)
    s.add_argument("--format", default="combined", choices=list(ingest.INGESTERS))
    s.add_argument("--seed", type=int, default=7)
    s.add_argument("--scale", type=float, default=1.0, help="traffic multiplier (10 = ~2M lines)")
    s.set_defaults(fn=cmd_sample)

    s = sub.add_parser("report", help="print the report (and optionally write an HTML page)")
    s.add_argument("--site", help="fetch robots.txt, llms.txt and sitemap from this base URL")
    s.add_argument("--robots", help="robots.txt path or URL")
    s.add_argument("--sitemap", help="sitemap.xml path or URL")
    s.add_argument("--llms", help="llms.txt path or URL")
    s.add_argument("--html", help="write an HTML report with charts to this file")
    s.add_argument("--top", type=int, default=15)
    s.add_argument("--since", help="YYYY-MM-DD (inclusive)")
    s.add_argument("--until", help="YYYY-MM-DD (exclusive)")
    s.set_defaults(fn=cmd_report)

    s = sub.add_parser("robots", help="test robots.txt rules for given paths")
    s.add_argument("robots", help="robots.txt path or URL")
    s.add_argument("paths", nargs="+")
    s.add_argument("--bot", action="append", help="bot / robots token (repeatable)")
    s.set_defaults(fn=cmd_robots, nodb=True)

    s = sub.add_parser("demo", help="ranges + sample + ingest + report in one go")
    s.add_argument("outdir", nargs="?", default="sample")
    s.add_argument("--html")
    s.add_argument("--top", type=int, default=15)
    s.set_defaults(fn=cmd_demo, since=None, until=None, rdns=False)

    a = p.parse_args(argv)
    con = None if getattr(a, "nodb", False) else connect(a.db)
    try:
        a.fn(con, a)
    finally:
        if con is not None:
            con.close()


if __name__ == "__main__":
    sys.exit(main())
