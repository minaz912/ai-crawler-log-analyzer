"""Turn the hits view + robots.txt/sitemap/llms.txt into one report structure.

Both the terminal and HTML renderers consume the dict returned by `analyze`.
"""

from __future__ import annotations

import re
import urllib.parse
from dataclasses import asdict

import duckdb

from .bots import BOTS, BY_NAME, CATEGORIES, CONTROL_TOKENS
from .robots import Lint, LlmsTxt, Robots

AI_CATS = ("training", "search", "user")
ASSET_RE = r"\.(css|js|mjs|map|png|jpe?g|gif|svg|webp|avif|ico|woff2?|ttf|eot|mp4|webm|pdf|zip|xml|txt|json)$"
SPECIAL = ("/robots.txt", "/llms.txt", "/llms-full.txt", "/sitemap.xml")


def _rows(con, sql: str, params=None) -> list[dict]:
    cur = con.execute(sql, params or [])
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def url_to_path(u: str) -> str:
    p = urllib.parse.urlsplit(u)
    return p.path or "/"


def sitemap_paths(xml: str) -> list[str]:
    return [url_to_path(u.strip()) for u in re.findall(r"<loc>\s*([^<]+?)\s*</loc>", xml)]


def analyze(con: duckdb.DuckDBPyConnection, robots: Robots | None = None, sitemap: list[str] | None = None,
            llms: LlmsTxt | None = None, top: int = 15, since: str | None = None,
            until: str | None = None) -> dict:
    where = ["ts IS NOT NULL"]
    params: list = []
    if since:
        where.append("ts >= CAST(? AS TIMESTAMP)")
        params.append(since)
    if until:
        where.append("ts < CAST(? AS TIMESTAMP)")
        params.append(until)
    con.execute(f"CREATE OR REPLACE TEMP VIEW h AS SELECT *, "
                f"(NOT regexp_matches(lower(path), '{ASSET_RE}') AND path NOT IN {SPECIAL!r}) AS is_page "
                f"FROM hits WHERE {' AND '.join(where)}", params)
    # 'genuine' = not shown to be fake. Unverifiable bots stay in, flagged separately.
    G = "verdict IS DISTINCT FROM 'spoofed'"
    R: dict = {"categories": CATEGORIES}

    R["summary"] = _rows(con, f"""
        SELECT count(*) AS requests, min(ts) AS first, max(ts) AS last,
               count(*) FILTER (category = 'human') AS human,
               count(*) FILTER (category IN {AI_CATS!r} AND {G}) AS ai_genuine,
               count(*) FILTER (category IN {AI_CATS!r} AND verdict = 'verified') AS ai_verified,
               count(*) FILTER (category IN {AI_CATS!r} AND verdict = 'spoofed') AS ai_spoofed,
               count(*) FILTER (verdict = 'spoofed') AS spoofed_all,
               count(*) FILTER (category = 'classic' AND {G}) AS classic,
               count(*) FILTER (category = 'other') AS other_bots
        FROM h""")[0]
    R["synthetic_ranges"] = con.execute(
        "SELECT count(*) FROM ip_ranges WHERE origin = 'synthetic'").fetchone()[0] > 0
    R["unchecked_lists"] = sorted({b.ip_list for b in BOTS if b.ip_list} - {
        r[0] for r in con.execute("SELECT DISTINCT list_name FROM ip_ranges").fetchall()})

    R["by_category"] = _rows(con, f"""
        SELECT category, count(*) FILTER ({G}) AS requests, count(DISTINCT path) FILTER ({G} AND is_page) AS pages,
               count(*) FILTER (verdict = 'verified') AS verified, count(*) FILTER (verdict = 'spoofed') AS spoofed
        FROM h WHERE category IN {AI_CATS + ('classic',)!r} GROUP BY ALL ORDER BY requests DESC""")

    R["bots"] = _rows(con, f"""
        SELECT bot, vendor, category,
               count(*) FILTER ({G}) AS requests,
               count(*) FILTER (verdict = 'verified') AS verified,
               count(*) FILTER (verdict = 'spoofed') AS spoofed,
               count(*) FILTER (verdict = 'unverifiable') AS unverifiable,
               count(*) FILTER (verdict = 'unchecked') AS unchecked,
               count(DISTINCT path) FILTER ({G} AND is_page) AS pages,
               count(*) FILTER ({G} AND status >= 400) AS errors,
               count(*) FILTER ({G} AND path = '/robots.txt') AS robots_fetches,
               count(*) FILTER ({G} AND path IN ('/llms.txt', '/llms-full.txt')) AS llms_fetches,
               max(ts) FILTER ({G}) AS last_seen
        FROM h WHERE category IN {AI_CATS + ('classic',)!r}
        GROUP BY ALL ORDER BY requests DESC""")

    R["daily_category"] = _rows(con, f"""
        SELECT CAST(date_trunc('day', ts) AS DATE) AS day, category, count(*) AS requests
        FROM h WHERE category IN {AI_CATS + ('classic',)!r} AND {G}
        GROUP BY ALL ORDER BY day""")
    R["daily_bot"] = _rows(con, f"""
        SELECT CAST(date_trunc('day', ts) AS DATE) AS day, bot, count(*) AS requests
        FROM h WHERE category IN {AI_CATS!r} AND {G} GROUP BY ALL ORDER BY day""")

    R["top_pages"] = {}
    for cat in AI_CATS:
        R["top_pages"][cat] = _rows(con, f"""
            SELECT path, count(*) AS requests, string_agg(DISTINCT bot, ', ' ORDER BY bot) AS bots,
                   (SELECT count(*) FROM h h2 WHERE h2.path = h.path AND h2.category = 'human') AS human
            FROM h WHERE category = ? AND {G} AND is_page
            GROUP BY path ORDER BY requests DESC LIMIT ?""", [cat, top])

    R["spoofing"] = _rows(con, """
        WITH s AS (
            SELECT h.bot, h.ip, count(*) AS n, any_value(v.detail) AS detail
            FROM h JOIN ip_verdict v ON v.ip = h.ip AND v.bot = h.bot
            WHERE h.verdict = 'spoofed' GROUP BY ALL
        )
        SELECT bot AS claimed, sum(n)::BIGINT AS requests, count(*) AS ips,
               array_to_string(list(ip ORDER BY n DESC)[1:3], ', ') AS top_ips, any_value(detail) AS why
        FROM s GROUP BY bot ORDER BY requests DESC""")

    # ---------------------------------------------------------------- page universe
    human_pages = {r["path"]: r["n"] for r in _rows(con, """
        SELECT path, count(*) AS n FROM h WHERE category = 'human' AND is_page AND status < 400
        GROUP BY path""")}
    universe = set(human_pages) | set(sitemap or [])
    R["universe_size"] = len(universe)
    R["sitemap_size"] = len(sitemap or [])

    crawled: dict[str, set[str]] = {}
    for r in _rows(con, f"""SELECT DISTINCT bot, path FROM h WHERE category IN {AI_CATS!r} AND {G} AND is_page
                           AND status < 400"""):
        crawled.setdefault(r["bot"], set()).add(r["path"])
    active_bots = [b for b in BOTS if b.category in AI_CATS and b.name in crawled]

    # ---------------------------------------------------------------- robots.txt
    R["robots"] = None
    if robots is not None:
        R["robots"] = _robots_section(con, robots, universe, human_pages, active_bots, crawled, G, top)

    # ---------------------------------------------------------------- coverage gaps
    gaps = []
    # User-triggered fetchers only fetch what people ask about, so "never fetched" isn't a gap for them.
    for cat in ("training", "search"):
        cat_bots = [b for b in active_bots if b.category == cat]
        if not cat_bots:
            continue
        seen = set().union(*(crawled[b.name] for b in cat_bots))
        for p in universe:
            if p in seen:
                continue
            allowed_for = [b.name for b in cat_bots if robots is None or robots.check(b.token, p).allowed]
            if allowed_for:
                gaps.append({"category": cat, "path": p, "human": human_pages.get(p, 0),
                             "in_sitemap": p in set(sitemap or []), "allowed_for": ", ".join(allowed_for)})
    gaps.sort(key=lambda g: (-g["human"], g["path"]))
    R["gaps"] = gaps
    R["gap_counts"] = {cat: sum(1 for g in gaps if g["category"] == cat) for cat in ("training", "search")}
    R["never_ai"] = sorted((p for p in universe if not any(p in s for s in crawled.values())),
                           key=lambda p: (-human_pages.get(p, 0), p))
    R["per_bot_coverage"] = [
        {"bot": b.name, "category": b.category, "crawled": len(crawled[b.name] & universe),
         "allowed": sum(1 for p in universe if robots is None or robots.check(b.token, p).allowed)}
        for b in active_bots]

    # ---------------------------------------------------------------- llms.txt
    R["llms"] = None
    if llms is not None:
        linked = [url_to_path(u) for u in llms.links]
        fetched = _rows(con, f"""SELECT bot, count(*) AS n FROM h WHERE path IN ('/llms.txt', '/llms-full.txt')
                                AND category IN {AI_CATS + ('classic',)!r} AND {G} GROUP BY bot ORDER BY n DESC""")
        status = {r["path"]: r["s"] for r in _rows(con, "SELECT path, max(status) AS s FROM h GROUP BY path")}
        all_ai = set().union(*crawled.values()) if crawled else set()
        R["llms"] = {
            "title": llms.title, "summary": llms.summary, "problems": llms.problems,
            "sections": {k: len(v) for k, v in llms.sections.items()}, "links": len(linked),
            "fetched_by": fetched,
            "linked_not_crawled": [p for p in linked if p not in all_ai],
            "linked_missing": [p for p in linked if p not in universe and status.get(p, 0) >= 400],
        }
    return R


def _robots_section(con, robots: Robots, universe: set[str], human_pages: dict[str, int], active_bots,
                    crawled, G: str, top: int) -> dict:
    pages = sorted(universe)
    policy = []
    tokens = [(b.token, b.category, b.vendor) for b in BOTS] + [(t, "control", desc) for t, desc in CONTROL_TOKENS.items()]
    for token, cat, vendor in tokens:
        groups, label = robots.groups_for(token)
        allowed = sum(1 for p in pages if robots.check(token, p).allowed) if cat != "control" else None
        cu = robots.content_usage_for(token)
        policy.append({"token": token, "category": cat, "vendor": vendor, "group": label,
                       "named": robots.names(token), "allowed": allowed, "total": len(pages),
                       "content_usage": "; ".join(f"{p or ''} {v}".strip() for p, v, _ in cu)})

    # Requests to disallowed URLs by AI bots, split by whether the bot is real.
    rows = _rows(con, f"""SELECT bot, path, query, verdict, count(*) AS n FROM h
                         WHERE category IN ('training','search','user','classic') AND path <> '/robots.txt'
                         GROUP BY ALL""")
    viol: dict[tuple, dict] = {}
    for r in rows:
        token = BY_NAME[r["bot"]].token
        v = robots.check(token, r["path"], r["query"])
        if v.allowed:
            continue
        key = (r["bot"], r["verdict"] == "spoofed")
        d = viol.setdefault(key, {"bot": r["bot"], "category": BY_NAME[r["bot"]].category,
                                  "spoofed": r["verdict"] == "spoofed", "verdict": r["verdict"],
                                  "requests": 0, "paths": {}, "rule": None})
        d["requests"] += r["n"]
        d["paths"][r["path"]] = d["paths"].get(r["path"], 0) + r["n"]
        d["rule"] = d["rule"] or f"line {v.rule.line}: {'Allow' if v.rule.allow else 'Disallow'}: {v.rule.pattern}"
    violations = sorted(viol.values(), key=lambda d: (d["spoofed"], -d["requests"]))
    for d in violations:
        d["top_paths"] = ", ".join(p for p, _ in sorted(d["paths"].items(), key=lambda kv: -kv[1])[:3])
        del d["paths"]

    # Popular pages that Google may crawl but AI search / assistants may not: likely mistakes.
    popular = sorted(human_pages, key=lambda p: -human_pages[p])[:max(25, top)]
    blocked = []
    for p in popular:
        if not robots.check("Googlebot", p).allowed:
            continue  # blocked for everyone, so probably deliberate (cart, search, admin...)
        who = []
        for b in BOTS:
            if b.category in ("search", "user"):
                v = robots.check(b.token, p)
                if not v.allowed:
                    who.append((b.name, v.rule.line if v.rule else None))
        if who:
            blocked.append({"path": p, "human": human_pages[p], "blocked_for": ", ".join(n for n, _ in who),
                            "lines": ", ".join(sorted({str(ln) for _, ln in who if ln}))})

    tokens_with_groups = [b.token for b in BOTS if robots.names(b.token)]
    lint = robots.lint + robots.lint_against_paths(pages, tokens_with_groups)
    # Typo'd rules: show what they failed to block in practice.
    for g in robots.groups:
        for r in (r for r in g.rules if r.typo):
            for b in active_bots:
                if b.token.lower() in g.agents:
                    hit = sorted(p for p in crawled.get(b.name, ()) if r.matches(p))
                    if hit:
                        lint.append(Lint("error", r.line, f"Because of that typo {b.name} fetched {len(hit)} "
                                         f"URL(s) the rule was meant to block, e.g. {hit[0]}."))
    lint.sort(key=lambda l: ({"error": 0, "warning": 1, "info": 2}[l.severity], l.line or 0))
    return {"policy": policy, "violations": violations, "blocked_popular": blocked,
            "lint": [asdict(l) for l in lint], "sitemaps": robots.sitemaps}
