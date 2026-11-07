"""Generate a realistic sample site: access log, robots.txt (with deliberate mistakes),
llms.txt and sitemap.xml.

Genuine bot traffic uses IPs drawn from the vendors' real published ranges (if `aicrawl ranges`
has been run). Without network access it falls back to synthetic ranges in 198.18.0.0/15
(the benchmarking block) and stores them so verification still works offline.
"""

from __future__ import annotations

import ipaddress
import json
import random
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path

import duckdb

from .bots import BY_NAME, IP_LISTS
from .verify import RangeIndex

HOST = "www.example.com"

UAS = {
    "GPTBot": "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; GPTBot/1.3; +https://openai.com/gptbot)",
    "OAI-SearchBot": "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; OAI-SearchBot/1.3; +https://openai.com/searchbot",
    "ChatGPT-User": "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; ChatGPT-User/1.0; +https://openai.com/bot",
    "ClaudeBot": "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; ClaudeBot/1.0; +claudebot@anthropic.com)",
    "Claude-SearchBot": "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; Claude-SearchBot/1.0; +searchbot@anthropic.com)",
    "Claude-User": "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; Claude-User/1.0; +Claude-User@anthropic.com)",
    "PerplexityBot": "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; PerplexityBot/1.0; +https://perplexity.ai/perplexitybot)",
    "Perplexity-User": "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; Perplexity-User/1.0; +https://perplexity.ai/perplexity-user)",
    "Bytespider": "Mozilla/5.0 (Linux; Android 5.0) AppleWebKit/537.36 (KHTML, like Gecko) Mobile Safari/537.36 (compatible; Bytespider; spider-feedback@bytedance.com)",
    "CCBot": "CCBot/2.0 (https://commoncrawl.org/faq/)",
    "Meta-ExternalAgent": "meta-externalagent/1.1 (+https://developers.facebook.com/docs/sharing/webmasters/crawler)",
    "Amazonbot": "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; Amazonbot/0.1; +https://developer.amazon.com/support/amazonbot) Chrome/119.0.6045.214 Safari/537.36",
    "Applebot": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15 (Applebot/0.1; +http://www.apple.com/go/applebot)",
    "DuckAssistBot": "DuckAssistBot/1.2; (+http://duckduckgo.com/duckassistbot.html)",
    "Googlebot": "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; Googlebot/2.1; +http://www.google.com/bot.html) Chrome/130.0.6723.69 Safari/537.36",
    "bingbot": "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm) Chrome/116.0.1938.76 Safari/537.36",
}
OTHER_BOT_UAS = [
    "Mozilla/5.0 (compatible; AhrefsBot/7.0; +http://ahrefs.com/robot/)",
    "Mozilla/5.0 (compatible; SemrushBot/7~bl; +http://www.semrush.com/bot.html)",
    "curl/8.5.0",
    "python-requests/2.32.3",
]
BROWSER_UAS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_6) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.2 Safari/605.1.15",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.2 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (X11; Linux x86_64; rv:144.0) Gecko/20100101 Firefox/144.0",
    "Mozilla/5.0 (Linux; Android 15; Pixel 9) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Mobile Safari/537.36",
]

ROBOTS_TXT = """\
# robots.txt for www.example.com
User-agent: *
Disallow: /admin
Disallow: /cart
Disallow: /account/
Disallow: /search
Content-Usage: train-ai=n

# No training crawlers
User-agent: GPTBot
User-agent: CCBot
User-agent: Bytespider
Disallow: /

# ChatGPT search: keep pricing out of answers, hide internal docs
User-agent: OAI-SearchBot
Disallow: /pricing
Disallow: /Docs/internal/

User-agent: PerplexityBot
Disalow: /blog/drafts/

User-agent: Google-Extended
Disallow: /

Sitemap: https://www.example.com/sitemap.xml
"""


def site_pages(rng: random.Random) -> list[str]:
    words = ["ai", "search", "crawler", "pricing", "seo", "guide", "llm", "content", "brand", "visibility",
             "agents", "analytics", "growth", "launch", "teams", "data", "metrics", "answers", "trust", "logs"]
    pages = ["/", "/pricing", "/about", "/contact", "/careers", "/legal/privacy", "/legal/terms",
             "/administration-guide", "/admin/login", "/cart", "/account/settings", "/search"]
    seen = set()
    while len(seen) < 60:
        seen.add("-".join(rng.sample(words, 3)))
    pages += [f"/blog/{s}" for s in sorted(seen)]
    pages += [f"/blog/drafts/draft-{i}" for i in range(1, 4)]
    for sec in ["getting-started", "api", "guides", "integrations", "internal"]:
        pages += [f"/docs/{sec}/page-{i}" for i in range(1, 16)]
    pages += [f"/products/{p}" for p in ["monitor", "insights", "alerts", "reports", "api", "connectors",
                                         "teams", "enterprise", "starter", "audit", "benchmarks", "studio"]]
    return pages


def _zipf_weights(n: int, s: float = 1.1) -> list[float]:
    return [1 / (i + 1) ** s for i in range(n)]


def _ensure_ranges(con: duckdb.DuckDBPyConnection) -> RangeIndex:
    have = {r[0] for r in con.execute("SELECT DISTINCT list_name FROM ip_ranges").fetchall()}
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    synth = ipaddress.ip_network("198.18.0.0/15").subnets(new_prefix=24)
    for name in IP_LISTS:
        net = next(synth)
        if name not in have:
            con.execute("INSERT INTO ip_ranges VALUES (?, ?, ?, 'synthetic')", [name, str(net), now])
    return RangeIndex(con.execute("SELECT list_name, cidr FROM ip_ranges").fetchall())


def _ips_from_list(con, rng: random.Random, list_name: str, k: int) -> list[str]:
    cidrs = [ipaddress.ip_network(c) for (c,) in con.execute(
        "SELECT cidr FROM ip_ranges WHERE list_name = ? AND cidr NOT LIKE '%:%'", [list_name]).fetchall()]
    if not cidrs:
        cidrs = [ipaddress.ip_network(c) for (c,) in con.execute(
            "SELECT cidr FROM ip_ranges WHERE list_name = ?", [list_name]).fetchall()]
    out = []
    for _ in range(k):
        net = rng.choice(cidrs)
        out.append(str(net.network_address + rng.randrange(max(1, min(net.num_addresses, 2**16)))))
    return out


def _random_public_ips(rng: random.Random, k: int, index: RangeIndex) -> list[str]:
    out = []
    while len(out) < k:
        ip = ipaddress.ip_address(rng.randrange(0x0B000000, 0xDF000000))
        if ip.is_global and not any(index.contains(name, str(ip)) for name in index.lists):
            out.append(str(ip))
    return out


def generate(con: duckdb.DuckDBPyConnection, outdir: Path, days: int = 30, fmt: str = "combined",
             seed: int = 7, end: datetime | None = None, scale: float = 1.0) -> dict[str, str]:
    rng = random.Random(seed)
    index = _ensure_ranges(con)
    pages = site_pages(rng)
    end = (end or datetime.now(timezone.utc)).replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
    start = end - timedelta(days=days)

    content = [p for p in pages if not p.startswith(("/admin/", "/cart", "/account", "/search", "/blog/drafts"))]
    human_order = ["/", "/pricing"] + rng.sample([p for p in content if p not in ("/", "/pricing")],
                                                 len(content) - 2)
    human_w = _zipf_weights(len(human_order))
    # AI crawlers never discover the integrations docs (only linked from a JS-rendered menu),
    # which gives the coverage-gap report something to find.
    ai_pool = [p for p in pages if not p.startswith("/docs/integrations/")]
    no_internal = [p for p in ai_pool if not p.startswith(("/docs/internal/", "/admin/", "/cart", "/account", "/search"))]
    popular = human_order[:25]
    old_urls = [f"/old-blog/post-{i}" for i in range(1, 30)]
    assets = ["/static/app.js", "/static/style.css", "/img/logo.svg", "/img/hero.webp", "/favicon.ico"]

    # (bot, per-day volume, ip pool, path pool, trend) - trend: 'flat' | 'grow' | 'bursty'
    def pool(name, k=12):
        return _ips_from_list(con, rng, BY_NAME[name].ip_list, k)

    spoof_ips = _random_public_ips(rng, 60, index)
    plain_ips = _random_public_ips(rng, 40, index)  # for vendors that publish nothing
    actors = [
        ("GPTBot", 0, pool("GPTBot"), [], "flat"),
        ("OAI-SearchBot", 140, pool("OAI-SearchBot"),
         [p for p in ai_pool if p != "/pricing" and not p.startswith(("/cart", "/account", "/search"))], "grow"),
        ("ChatGPT-User", 70, pool("ChatGPT-User"), popular, "grow"),
        ("ClaudeBot", 260, pool("ClaudeBot"), no_internal, "bursty"),
        ("Claude-SearchBot", 60, pool("Claude-SearchBot"), no_internal, "grow"),
        ("Claude-User", 40, pool("Claude-User"), popular, "grow"),
        ("PerplexityBot", 90, pool("PerplexityBot"), ai_pool, "flat"),
        ("Perplexity-User", 35, pool("Perplexity-User"), popular, "grow"),
        ("Bytespider", 180, plain_ips[:8], ai_pool, "bursty"),
        ("CCBot", 0, pool("CCBot"), [], "flat"),
        ("Meta-ExternalAgent", 70, plain_ips[8:14], no_internal, "bursty"),
        ("Amazonbot", 45, plain_ips[14:20], no_internal, "flat"),
        ("Applebot", 40, pool("Applebot"), no_internal, "flat"),
        ("DuckAssistBot", 10, pool("DuckAssistBot"), popular, "grow"),
        ("Googlebot", 380, pool("Googlebot", 20), [p for p in pages if not p.startswith(
            ("/admin/", "/cart", "/account", "/search"))], "flat"),
        ("bingbot", 140, pool("bingbot"), no_internal, "flat"),
        # Impostors: scrapers wearing well-known bot user agents
        ("spoof:Googlebot", 90, spoof_ips[:25], ["/pricing"] + [p for p in pages if p.startswith("/products/")], "flat"),
        ("spoof:GPTBot", 30, spoof_ips[25:40], [p for p in pages if p.startswith("/blog/")] + ["/pricing"], "bursty"),
        ("spoof:ClaudeBot", 20, spoof_ips[40:], ai_pool, "flat"),
    ]

    events: list[tuple[datetime, str, str, int, int, str, str]] = []

    def stamp(day: int) -> datetime:
        return start + timedelta(days=day, seconds=rng.randrange(86400))

    def status_for(path: str) -> int:
        r = rng.random()
        if path.startswith("/old-blog/"):
            return 404
        if r < 0.004:
            return 503
        if r < 0.015:
            return 304
        return 200

    for day in range(days):
        frac = day / max(1, days - 1)
        # bots fetch robots.txt (well-behaved ones several times a day)
        for name in ["GPTBot", "OAI-SearchBot", "ClaudeBot", "Claude-SearchBot", "PerplexityBot", "CCBot",
                     "Googlebot", "bingbot", "Applebot", "Amazonbot", "Meta-ExternalAgent"]:
            ips = next(a[2] for a in actors if a[0] == name)
            for _ in range(rng.randint(1, 4)):
                events.append((stamp(day), rng.choice(ips), "/robots.txt", 200, 812, "-", UAS[name]))
        for name, ua in [("OAI-SearchBot", UAS["OAI-SearchBot"]), ("ClaudeBot", UAS["ClaudeBot"]),
                         ("PerplexityBot", UAS["PerplexityBot"])]:
            if rng.random() < 0.3:
                ips = next(a[2] for a in actors if a[0] == name)
                events.append((stamp(day), rng.choice(ips), "/llms.txt", 200, 2304, "-", ua))

        for name, volume, ips, path_pool, trend in actors:
            if not volume or not path_pool:
                continue
            mult = {"flat": 1.0, "grow": 0.4 + 1.4 * frac,
                    "bursty": (3.5 if rng.random() < 0.15 else 0.6)}[trend]
            n = int(rng.gauss(volume * mult, volume * 0.1) * scale)
            ua = UAS[name.split(":", 1)[-1]]
            w = _zipf_weights(len(path_pool), 0.6)
            for _ in range(max(0, n)):
                path = rng.choices(path_pool, w)[0] if rng.random() > 0.04 else rng.choice(old_urls)
                events.append((stamp(day), rng.choice(ips), path, status_for(path),
                               rng.randrange(8000, 90000), "-", ua))

        # people
        for _ in range(int(rng.gauss(3800, 300) * (1 + 0.15 * frac) * scale)):
            ip = f"{rng.choice([24, 73, 98, 172, 81, 92, 109])}.{rng.randrange(256)}.{rng.randrange(256)}.{rng.randrange(1, 255)}"
            path = rng.choices(human_order, human_w)[0]
            ts = stamp(day)
            ua = rng.choice(BROWSER_UAS)
            ref = rng.choice(["-", "https://www.google.com/", "https://chatgpt.com/", "https://www.perplexity.ai/",
                              "https://claude.ai/", f"https://{HOST}/"])
            events.append((ts, ip, path, status_for(path), rng.randrange(15000, 120000), ref, ua))
            if rng.random() < 0.3:
                events.append((ts + timedelta(seconds=1), ip, rng.choice(assets), 200, 4000, f"https://{HOST}{path}", ua))
            if rng.random() < 0.02:
                events.append((ts, ip, "/search?q=" + rng.choice(["pricing", "api", "llm"]), 200, 9000, "-", ua))
        for _ in range(int(150 * scale)):
            events.append((stamp(day), rng.choice(plain_ips[20:]), rng.choice(pages), 200, 30000, "-",
                           rng.choice(OTHER_BOT_UAS)))

    events.sort(key=lambda e: e[0])
    outdir.mkdir(parents=True, exist_ok=True)
    log_path = outdir / {"combined": "access.log", "cloudflare": "cloudflare.ndjson",
                         "cloudfront": "cloudfront.log"}[fmt]
    with open(log_path, "w") as f:
        if fmt == "cloudfront":
            f.write("#Version: 1.0\n#Fields: date time x-edge-location sc-bytes c-ip cs-method cs(Host) "
                    "cs-uri-stem sc-status cs(Referer) cs(User-Agent) cs-uri-query\n")
        for ts, ip, target, status, size, ref, ua in events:
            path, _, query = target.partition("?")
            if fmt == "combined":
                f.write(f'{ip} - - [{ts:%d/%b/%Y:%H:%M:%S} +0000] "GET {target} HTTP/1.1" {status} {size} '
                        f'"{ref}" "{ua}"\n')
            elif fmt == "cloudflare":
                f.write(json.dumps({
                    "ClientIP": ip, "ClientRequestHost": HOST, "ClientRequestMethod": "GET",
                    "ClientRequestURI": target, "ClientRequestUserAgent": ua, "EdgeResponseStatus": status,
                    "EdgeResponseBytes": size,
                    "EdgeStartTimestamp": int(ts.replace(tzinfo=timezone.utc).timestamp() * 1e9)}) + "\n")
            else:
                f.write("\t".join([f"{ts:%Y-%m-%d}", f"{ts:%H:%M:%S}", "FRA56-P1", str(size), ip, "GET", HOST,
                                   path, str(status), urllib.parse.quote(ref, safe=":/"),
                                   urllib.parse.quote(ua, safe=""), query or "-"]) + "\n")

    (outdir / "robots.txt").write_text(ROBOTS_TXT)
    (outdir / "llms.txt").write_text(
        "# Example Co\n\n> Example Co builds AI visibility analytics.\n\n## Docs\n\n"
        + "".join(f"- [{p.rsplit('/', 1)[-1]}](https://{HOST}{p})\n" for p in pages if p.startswith(
            ("/docs/getting-started/", "/docs/integrations/")))
        + "\n## Product\n\n- [Pricing](https://www.example.com/pricing): plans and limits\n")
    sitemap_urls = [p for p in pages if not p.startswith(("/admin/", "/cart", "/account", "/search",
                                                            "/blog/drafts", "/docs/internal"))]
    (outdir / "sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "".join(f"  <url><loc>https://{HOST}{p}</loc></url>\n" for p in sitemap_urls) + "</urlset>\n")
    origin = {r[0] for r in con.execute("SELECT DISTINCT origin FROM ip_ranges").fetchall()}
    return {"log": str(log_path), "events": str(len(events)), "ranges": ",".join(sorted(origin))}
