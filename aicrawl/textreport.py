"""Plain-text report for the terminal."""

from __future__ import annotations

from .analysis import AI_CATS

PLURAL = {"training": "training crawler", "search": "AI search indexer"}
CAT_SHORT = {"training": "training", "search": "AI search", "user": "user fetch", "classic": "classic"}


def table(rows: list[dict], cols: list[tuple[str, str]], limit: int | None = None) -> str:
    rows = rows[:limit] if limit else rows
    if not rows:
        return "  (none)\n"
    def fmt(v):
        if v is None:
            return ""
        if isinstance(v, bool):
            return "yes" if v else "no"
        return f"{v:,}" if isinstance(v, int) else str(v)
    cells = [[fmt(r.get(k)) for k, _ in cols] for r in rows]
    widths = [min(60, max(len(h), *(len(c[i]) for c in cells))) for i, (_, h) in enumerate(cols)]
    numeric = [all((isinstance(r.get(k), (int, float)) and not isinstance(r.get(k), bool)) or r.get(k) is None
                   for r in rows) for k, _ in cols]

    def line(vals):
        out = []
        for v, w, num in zip(vals, widths, numeric):
            v = v if len(v) <= w else v[: w - 1] + "…"
            out.append(v.rjust(w) if num else v.ljust(w))
        return "  " + "  ".join(out).rstrip()

    return "\n".join([line([h for _, h in cols]), line(["─" * w for w in widths]), *map(line, cells)]) + "\n"


def sparkline(values: list[int]) -> str:
    bars = "▁▂▃▄▅▆▇█"
    hi = max(values) if values else 0
    return "".join(bars[min(7, int(v / hi * 7.999))] if hi else bars[0] for v in values)


def render(R: dict, top: int = 15) -> str:
    s = R["summary"]
    out: list[str] = []
    h = lambda t: out.append(f"\n{t}\n{'═' * len(t)}\n")  # noqa: E731
    pct = lambda a, b: f"{100 * a / b:.0f}%" if b else "–"  # noqa: E731

    h("AI crawler report")
    out.append(f"  Window      {s['first']:%Y-%m-%d} → {s['last']:%Y-%m-%d}\n" if s["first"] else "  No data.\n")
    out.append(f"  Requests    {s['requests']:,} total · {s['human']:,} human · {s['ai_genuine']:,} AI bots "
               f"({pct(s['ai_genuine'], s['requests'])}) · {s['classic']:,} classic search · "
               f"{s['other_bots']:,} other bots\n")
    out.append(f"  Verified    {s['ai_verified']:,} of {s['ai_genuine']:,} AI requests "
               f"({pct(s['ai_verified'], s['ai_genuine'])}) come from vendor IP ranges\n")
    out.append(f"  Spoofed     {s['spoofed_all']:,} requests used a bot user agent from an IP the vendor "
               f"doesn't own (excluded from the numbers below)\n")
    if R["synthetic_ranges"]:
        out.append("  ⚠ Using synthetic IP ranges from the sample generator. Run `aicrawl ranges` for real ones.\n")
    if R["unchecked_lists"]:
        out.append(f"  ⚠ Not downloaded, so not checked: {', '.join(R['unchecked_lists'])}\n")

    h("By category")
    days = sorted({r["day"] for r in R["daily_category"]})
    by = {(r["day"], r["category"]): r["requests"] for r in R["daily_category"]}
    cat_rows = []
    for r in R["by_category"]:
        series = [by.get((d, r["category"]), 0) for d in days]
        cat_rows.append({**r, "label": R["categories"][r["category"]], "trend": sparkline(series)})
    out.append(table(cat_rows, [("label", "category"), ("requests", "requests"), ("pages", "pages"),
                                ("verified", "verified"), ("trend", f"daily trend ({len(days)} days)")]))

    h("By bot")
    bot_rows = [{**r, "cat": CAT_SHORT[r["category"]], "last": f"{r['last_seen']:%m-%d %H:%M}" if r["last_seen"] else "",
                 "check": ("verified" if r["verified"] and not (r["unverifiable"] or r["unchecked"])
                           else "can't verify" if r["unverifiable"] else "unchecked" if r["unchecked"]
                           else "mixed" if r["verified"] else "–")}
                for r in R["bots"] if r["requests"]]
    out.append(table(bot_rows, [("bot", "bot"), ("vendor", "vendor"), ("cat", "type"), ("requests", "requests"),
                                ("check", "IP check"), ("pages", "pages"), ("errors", "4xx/5xx"),
                                ("robots_fetches", "robots.txt"), ("llms_fetches", "llms.txt"), ("last", "last seen")]))

    for cat in AI_CATS:
        rows = R["top_pages"][cat]
        if rows:
            h(f"Top pages · {R['categories'][cat].lower()}")
            out.append(table(rows, [("path", "path"), ("requests", "requests"), ("human", "human views"),
                                    ("bots", "bots")], top))

    h("Spoofed bot traffic")
    out.append(table(R["spoofing"], [("claimed", "claims to be"), ("requests", "requests"), ("ips", "IPs"),
                                     ("top_ips", "top IPs"), ("why", "why it failed")]))

    rb = R["robots"]
    if rb:
        h("robots.txt · who may crawl what")
        rows = [{**p, "share": "—" if p["allowed"] is None else f"{p['allowed']}/{p['total']}",
                 "cat": CAT_SHORT.get(p["category"], p["category"])} for p in rb["policy"]
                if p["named"] or p["category"] in AI_CATS]
        out.append(table(rows, [("token", "token"), ("cat", "type"), ("group", "group used"),
                                ("share", "pages allowed"), ("content_usage", "Content-Usage")]))
        h("robots.txt · problems found")
        for l in rb["lint"]:
            icon = {"error": "✖", "warning": "▲", "info": "·"}[l["severity"]]
            where = f"line {l['line']}: " if l["line"] else ""
            out.append(f"  {icon} {where}{l['message']}\n")
        if rb["blocked_popular"]:
            out.append("\n  Popular pages Google may crawl but AI search / assistants may not (intentional?):\n")
            out.append(table(rb["blocked_popular"], [("path", "path"), ("human", "human views"),
                                                     ("blocked_for", "blocked for"), ("lines", "rule line")]))
        h("robots.txt · requests to disallowed URLs")
        out.append(table([{**v, "who": "spoofed" if v["spoofed"] else v["verdict"]} for v in rb["violations"]],
                         [("bot", "bot"), ("who", "IP check"), ("requests", "requests"), ("rule", "rule"),
                          ("top_paths", "top paths")]))
        out.append("  Note: vendors say user-triggered fetchers (ChatGPT-User, Claude-User, Perplexity-User…) act\n"
                   "  for a person and may not apply robots.txt the way crawlers do.\n")

    h("Coverage gaps")
    out.append(f"  Site pages known: {R['universe_size']:,} (human-visited pages"
               f"{' + ' + str(R['sitemap_size']) + ' sitemap URLs' if R['sitemap_size'] else ''})\n")
    out.append(f"  Never fetched by any AI bot: {len(R['never_ai']):,}\n")
    for cat in R["gap_counts"]:
        out.append(f"  Allowed but never fetched by any {PLURAL[cat]}: {R['gap_counts'][cat]:,}\n")
    out.append("\n" + table(R["per_bot_coverage"], [("bot", "bot"), ("crawled", "pages crawled"),
                                                   ("allowed", "pages allowed")]))
    out.append("\n  Most-visited pages that no AI search indexer has fetched:\n")
    out.append(table([g for g in R["gaps"] if g["category"] == "search"],
                     [("path", "path"), ("human", "human views"), ("in_sitemap", "in sitemap")], top))

    if R["llms"]:
        L = R["llms"]
        h("llms.txt")
        out.append(f"  Title: {L['title'] or '(missing)'} · {L['links']} links in {len(L['sections'])} sections\n")
        for p in L["problems"]:
            out.append(f"  ✖ {p}\n")
        fetched = ", ".join(f"{r['bot']} ×{r['n']}" for r in L["fetched_by"]) or "nobody"
        out.append(f"  Fetched by: {fetched}\n")
        out.append(f"  Linked from llms.txt but never fetched by an AI bot: {len(L['linked_not_crawled'])}"
                   + (f" (e.g. {', '.join(L['linked_not_crawled'][:3])})" if L["linked_not_crawled"] else "") + "\n")
        if L["linked_missing"]:
            out.append(f"  ✖ Links that return errors: {', '.join(L['linked_missing'][:5])}\n")
    return "".join(out)
