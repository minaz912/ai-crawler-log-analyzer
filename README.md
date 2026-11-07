# aicrawl: AI crawler log analyzer

Answer-engine tools look at what AI models *say*. This one looks at what they *read*. Give it a site's access logs and it tells you:

- **which AI bots crawl the site** (GPTBot, OAI-SearchBot, ChatGPT-User, ClaudeBot, Claude-SearchBot, Claude-User, PerplexityBot, Perplexity-User, Bytespider, CCBot, Meta, Amazonbot, Applebot, DuckAssistBot, Mistral…), with Googlebot and Bingbot as a baseline
- **whether they're genuine**, checked against each vendor's published IP ranges or forward-confirmed reverse DNS
- **what kind of fetch it is**: training crawler, AI search indexer, or live user-triggered fetch
- **which pages they fetch and how often**, as a daily series
- **whether they're allowed to**, using an RFC 9309 robots.txt evaluator and linter: pages blocked by mistake, rules that do nothing, and bots ignoring robots.txt
- **coverage gaps**: pages people visit (or that are in your sitemap / llms.txt) that no AI bot has fetched

It's a Python CLI on DuckDB. Its only dependency is `duckdb`.

## Quick start

```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/aicrawl demo          # download IP ranges, generate 30 days of sample logs, ingest, report
xdg-open sample/report.html     # charts + tables
```

With your own logs:

```bash
aicrawl ranges                                   # download vendor IP lists (re-run now and then)
aicrawl ingest /var/log/nginx/access.log*.gz     # nginx/Apache, Cloudflare Logpush, CloudFront; auto-detected
aicrawl report --site https://www.example.com --html report.html
# or point at local copies:
aicrawl report --robots robots.txt --sitemap sitemap.xml --llms llms.txt --since 2026-10-01
```

Data lives in `aicrawl.duckdb` (change it with `--db`). Ingesting the same file again replaces its rows.

### Commands

| command | what it does |
|---|---|
| `ranges` | Download the published IP lists (OpenAI ×3, Anthropic, Perplexity ×2, Google, Bing, Apple, Common Crawl, DuckDuckGo, Mistral) |
| `ingest FILE…` | Parse logs into DuckDB, classify user agents, verify IPs. `--format`, `--host`, `--append`, `--rdns` |
| `verify [--rdns]` | Re-run verification (after `ranges`, or to add reverse-DNS checks) |
| `report` | Terminal report. `--html` adds a self-contained chart page. `--site` fetches robots.txt / llms.txt / sitemap |
| `robots FILE PATH…` | Show which rule decides each path for each bot: handy for testing edge cases |
| `sample DIR` | Generate sample logs (`--format combined/cloudflare/cloudfront`, `--days`, `--scale`) |
| `demo` | `ranges` + `sample` + `ingest` + `report` |

## How it works

**Ingestion** (`ingest.py`). Everything is parsed inside DuckDB. Combined-format lines are read as single-column CSV and split with one `regexp_extract`. Cloudflare uses `read_json`, and CloudFront uses `read_csv` mapped through its `#Fields:` header. Gzip is handled transparently. About 2.2M lines (469 MB) load in roughly 2.5s on a laptop. Timestamps are stored as UTC.

**Classification** (`bots.py`). User agents are classified once per *distinct* UA in Python (thousands, not millions), then joined back. The `hits` view in `db.py` puts it all together.

**Verification** (`verify.py`). Vendor IP lists are merged into sorted integer intervals for binary-search lookup. Reverse DNS has to be *forward-confirmed* (PTR → hostname → A/AAAA back to the same IP), otherwise anyone who controls their PTR record could claim to be `googlebot.com`. Verdicts:

| verdict | meaning |
|---|---|
| `verified` | IP is in the vendor's published ranges, or rDNS is confirmed |
| `spoofed` | the vendor publishes a method and this IP fails it |
| `unverifiable` | the vendor publishes nothing (Bytespider, Meta, Cohere…) |
| `unchecked` | a method exists but wasn't run (`aicrawl ranges` / `--rdns`) |

Spoofed requests are excluded from volume, page and coverage numbers and reported separately. In the sample data, every "GPTBot" request that ignores robots.txt comes from a scraper, not OpenAI.

**robots.txt** (`robots.py`). Python's `urllib.robotparser` gets several things wrong (first match instead of longest match, no `*`/`$`, no group merging), so this is a separate implementation. Edge cases it handles and tests:

- A bot that has its own group ignores the `*` group entirely. The linter warns when that silently lets it into `/admin`, `/cart` and so on.
- Several `User-agent` lines can share one group. Repeated groups for the same bot merge. Blank lines don't end a group.
- Longest match wins and Allow wins ties. `*` and `$` wildcards. Percent-encoding normalisation (`/café` = `/caf%C3%A9`).
- Paths are case-sensitive: `Disallow: /Docs/` doesn't block `/docs/`. The linter catches this from real URLs.
- Prefix overreach: `Disallow: /admin` also blocks `/administration-guide`.
- Misspelled directives (`Disalow`) are honoured only for Googlebot, which documents that tolerance. They're reported as errors, along with which pages slipped through.
- An empty `Disallow:` allows everything. `/robots.txt` is always fetchable. BOM, CRLF, rules before any `User-agent` line, and the 500 KiB limit.
- `Content-Usage: train-ai=n` (IETF aipref draft) is collected per group. Anthropic now honours it for ClaudeBot when ClaudeBot isn't named explicitly.
- Control-only tokens (`Google-Extended`, `Applebot-Extended`) are shown in the policy table.

**"Blocked by mistake"** is a heuristic: popular pages (by human traffic) that Googlebot may crawl but an AI search indexer or assistant may not. Pages blocked for everyone (cart, search) are treated as deliberate.

**Coverage gaps**: the page universe is human-visited pages plus sitemap URLs. A gap is a page that robots.txt allows for some active training or search bot but no genuine bot of that type fetched. User-triggered fetchers are left out because they only fetch what people ask about.

**llms.txt**: parsed per llmstxt.org (H1 title, `>` summary, `##` link sections). The report shows who fetched it and which linked pages no AI bot has read.

## Caveats

- Bot categories reflect each vendor's own description. Vendors say user-triggered fetchers act for a person and may not follow robots.txt the way crawlers do, so the report labels those requests rather than calling them violations.
- Amazonbot and CCBot support reverse DNS. That's opt-in (`--rdns`) because it does network lookups.
- `sample` takes bot IPs from the real vendor ranges if `aicrawl ranges` has run. Offline, it falls back to synthetic ranges in `198.18.0.0/15` and the report warns about it. Run `aicrawl ranges` before analysing real logs in the same database, so the real lists replace the synthetic ones.

## Tests

```bash
.venv/bin/pytest -q
```
