# aicrawl: AI crawler log analyzer

aicrawl reads a website's access logs and reports which AI bots fetched the site, which pages they read and how often, whether each bot is who it claims to be, and whether robots.txt allowed the visit. It is a Python CLI on DuckDB, and `duckdb` is its only dependency.

For each run it reports:

- Which AI bots crawl the site: GPTBot, OAI-SearchBot, ChatGPT-User, ClaudeBot, Claude-SearchBot, Claude-User, PerplexityBot, Perplexity-User, Bytespider, CCBot, Meta, Amazonbot, Applebot, DuckAssistBot, Mistral and others. Googlebot and Bingbot are included as a baseline.
- Whether each request is genuine, checked against the vendor's published IP ranges or forward-confirmed reverse DNS.
- What kind of fetch it is. Training crawlers collect data for future models, AI search indexers build the index behind AI answers, and user-triggered fetches happen when someone asks an assistant about a page.
- Which pages each bot fetches and how often, per day.
- Whether robots.txt allows the fetch. An RFC 9309 evaluator decides each request, and a linter finds rules that block pages by mistake, rules that block nothing, and bots that ignore robots.txt.
- Coverage gaps: pages that people visit, or that appear in your sitemap or llms.txt, which no AI bot has fetched.

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

Data goes into `aicrawl.duckdb`, or the file you pass with `--db`. Ingesting a file a second time replaces its earlier rows.

### Commands

| command | what it does |
|---|---|
| `ranges` | Download the published IP lists (OpenAI ×3, Anthropic, Perplexity ×2, Google, Bing, Apple, Common Crawl, DuckDuckGo, Mistral) |
| `ingest FILE…` | Parse logs into DuckDB, classify user agents, verify IPs. `--format`, `--host`, `--append`, `--rdns` |
| `verify [--rdns]` | Re-run verification (after `ranges`, or to add reverse-DNS checks) |
| `report` | Terminal report. `--html` adds a self-contained chart page. `--site` fetches robots.txt, llms.txt and the sitemap |
| `robots FILE PATH…` | Show which rule decides each path for each bot, for testing edge cases |
| `sample DIR` | Generate sample logs (`--format combined/cloudflare/cloudfront`, `--days`, `--scale`) |
| `demo` | `ranges` + `sample` + `ingest` + `report` |

## How it works

### Ingestion

`ingest.py` does all parsing inside DuckDB. It reads combined-format lines as single-column CSV and splits them with one `regexp_extract`. Cloudflare logs go through `read_json`, and CloudFront logs through `read_csv`, with columns mapped from the `#Fields:` header. DuckDB decompresses gzip on its own. On a laptop, 2.2M lines (469 MB) load in about 2.5 seconds. Timestamps are stored in UTC.

### Classification

`bots.py` classifies each distinct user agent once in Python. A log has thousands of distinct user agents and millions of lines, so this is cheap. The results are joined back to the requests in the `hits` view in `db.py`.

### Verification

`verify.py` merges each vendor's IP list into sorted integer intervals and looks addresses up with binary search. A reverse-DNS match only counts when it is forward-confirmed (PTR to hostname, then A/AAAA back to the same IP). Without that check, anyone who controls their own PTR record could claim to be `googlebot.com`. Each IP and bot pair gets one verdict:

| verdict | meaning |
|---|---|
| `verified` | IP is in the vendor's published ranges, or rDNS is confirmed |
| `spoofed` | the vendor publishes a method and this IP fails it |
| `unverifiable` | the vendor publishes nothing (Bytespider, Meta, Cohere…) |
| `unchecked` | a method exists but wasn't run (`aicrawl ranges` / `--rdns`) |

The report lists spoofed requests separately and leaves them out of the volume, page and coverage numbers. In the sample data, every "GPTBot" request that ignores robots.txt comes from a scraper rather than OpenAI.

### robots.txt

Python's `urllib.robotparser` uses the first matching rule instead of the longest, ignores `*` and `$`, and doesn't merge groups, so `robots.py` has its own implementation. It handles and tests these cases:

- A bot with its own group ignores the `*` group entirely. The linter warns when that lets the bot into `/admin`, `/cart` and similar paths.
- Several `User-agent` lines can share one group, repeated groups for the same bot merge, and blank lines don't end a group.
- The longest match wins and Allow wins ties. It supports `*` and `$` wildcards and percent-encoding normalisation (`/café` = `/caf%C3%A9`).
- Paths are case-sensitive, so `Disallow: /Docs/` doesn't block `/docs/`. The linter finds these by comparing rules with real URLs from the logs.
- `Disallow: /admin` is a prefix match and also blocks `/administration-guide`.
- Googlebot documents that it accepts misspelled directives such as `Disalow`, so only Googlebot gets that tolerance. The linter reports the typo as an error and lists the pages other bots fetched because of it.
- An empty `Disallow:` allows everything, and `/robots.txt` is always fetchable. It also handles a BOM, CRLF line endings, rules before any `User-agent` line, and the 500 KiB size limit.
- `Content-Usage: train-ai=n` lines (IETF aipref draft) are collected per group. Anthropic honours this signal for ClaudeBot when robots.txt doesn't name ClaudeBot.
- The policy table includes `Google-Extended` and `Applebot-Extended`, which only appear in robots.txt and are never sent as user agents.

The "blocked by mistake" check flags popular pages (by human traffic) that Googlebot may crawl but an AI search indexer or assistant may not. Pages blocked for every bot, such as the cart, count as deliberate.

### Coverage gaps

The set of known pages is every page people visited plus every sitemap URL. A page is a gap when robots.txt allows it for an active training or search bot and no genuine bot of that type fetched it. User-triggered fetchers only load pages people ask about, so they aren't counted.

### llms.txt

aicrawl parses llms.txt according to llmstxt.org: an H1 title, a `>` summary, and `##` sections of links. The report shows which bots fetched the file and which linked pages no AI bot has read.

## Caveats

- Bot categories follow each vendor's own description. Vendors say user-triggered fetchers act for a person and may not follow robots.txt as crawlers do, so the report labels those requests instead of calling them violations.
- Amazonbot and CCBot can be checked with reverse DNS. That check is opt-in (`--rdns`) because it makes network lookups.
- `sample` takes bot IPs from the real vendor ranges once `aicrawl ranges` has run. Offline, it falls back to made-up ranges in `198.18.0.0/15`, and the report warns about it. Run `aicrawl ranges` before analysing real logs in the same database, so the real lists replace the made-up ones.

## Tests

```bash
.venv/bin/pytest -q
```
