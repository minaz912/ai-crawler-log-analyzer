import gzip
import json
from datetime import datetime

import pytest

from aicrawl import analysis, ingest, sample, verify
from aicrawl.bots import classify_ua
from aicrawl.db import connect
from aicrawl.robots import Robots
from aicrawl.verify import RangeIndex, parse_ranges

GPTBOT_UA = "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; GPTBot/1.3; +https://openai.com/gptbot)"


def test_classify_ua():
    assert classify_ua(GPTBOT_UA) == "GPTBot"
    assert classify_ua("Mozilla/5.0 ... ChatGPT-User/1.0; +https://openai.com/bot") == "ChatGPT-User"
    assert classify_ua("Claude-SearchBot/1.0") == "Claude-SearchBot"
    assert classify_ua("meta-externalagent/1.1") == "Meta-ExternalAgent"
    assert classify_ua("Mozilla/5.0 (compatible; AhrefsBot/7.0)") == "~other-bot"
    assert classify_ua("Mozilla/5.0 (Windows NT 10.0) Chrome/141.0") is None


def test_ranges():
    cidrs = parse_ranges(json.dumps({"prefixes": [{"ipv4Prefix": "10.0.0.0/24"}, {"ipv6Prefix": "2001:db8::/32"}]}))
    idx = RangeIndex([("l", c) for c in cidrs])
    assert idx.contains("l", "10.0.0.255") and not idx.contains("l", "10.0.1.0")
    assert idx.contains("l", "2001:db8::1") and idx.contains("l", "::ffff:10.0.0.5")
    assert not idx.contains("l", "not-an-ip")


@pytest.fixture
def con(tmp_path):
    c = connect(str(tmp_path / "t.duckdb"))
    yield c
    c.close()


def _seed_ranges(con):
    now = datetime(2026, 1, 1)
    con.execute("INSERT INTO ip_ranges VALUES ('openai-gptbot', '20.0.0.0/24', ?, 'vendor')", [now])


def test_combined_cloudflare_cloudfront_agree(con, tmp_path):
    _seed_ranges(con)
    (tmp_path / "a.log").write_text(
        f'20.0.0.5 - - [01/Nov/2026:10:00:00 +0100] "GET /blog/a?x=1 HTTP/1.1" 200 512 "-" "{GPTBOT_UA}"\n'
        f'9.9.9.9 - - [01/Nov/2026:10:00:01 +0000] "GET /blog/b HTTP/1.1" 404 - "-" "{GPTBOT_UA}"\n'
        'garbage line\n')
    with gzip.open(tmp_path / "b.ndjson.gz", "wt") as f:
        f.write(json.dumps({"ClientIP": "20.0.0.5", "ClientRequestHost": "h", "ClientRequestMethod": "GET",
                            "ClientRequestURI": "/blog/a?x=1", "ClientRequestUserAgent": GPTBOT_UA,
                            "EdgeResponseStatus": 200, "EdgeResponseBytes": 512,
                            "EdgeStartTimestamp": 1793523600000000000}) + "\n")
        f.write(json.dumps({"ClientIP": "9.9.9.9", "ClientRequestURI": "/blog/b", "ClientRequestUserAgent": GPTBOT_UA,
                            "EdgeResponseStatus": 404, "EdgeStartTimestamp": "2026-11-01T10:00:01Z"}) + "\n")
    (tmp_path / "c.log").write_text(
        "#Version: 1.0\n#Fields: date time x-edge-location sc-bytes c-ip cs-method cs(Host) cs-uri-stem sc-status "
        "cs(Referer) cs(User-Agent) cs-uri-query\n"
        "2026-11-01\t09:00:00\tFRA\t512\t20.0.0.5\tGET\th\t/blog/a\t200\t-\t"
        + GPTBOT_UA.replace(" ", "%20") + "\tx=1\n")
    for name, fmt in [("a.log", "combined"), ("b.ndjson.gz", "cloudflare"), ("c.log", "cloudfront")]:
        assert ingest.detect_format(tmp_path / name) == fmt
        ingest.ingest(con, tmp_path / name)
    verify.verify(con)
    rows = con.execute("SELECT source, ts, ip, path, query, status, bot, verdict FROM hits "
                       "ORDER BY source, ts").fetchall()
    by_src = {}
    for r in rows:
        by_src.setdefault(r[0].rsplit("/", 1)[-1], []).append(r[1:])
    assert len(by_src["a.log"]) == 2  # garbage line skipped
    first = (datetime(2026, 11, 1, 9, 0), "20.0.0.5", "/blog/a", "x=1", 200, "GPTBot", "verified")
    assert by_src["a.log"][0] == by_src["b.ndjson.gz"][0] == by_src["c.log"][0] == first
    assert by_src["a.log"][1][-1] == "spoofed"


def test_sample_end_to_end(con, tmp_path):
    info = sample.generate(con, tmp_path, days=5, scale=0.1)
    ingest.ingest(con, tmp_path / "access.log")
    counts = verify.verify(con)
    assert counts["verified"] > 0 and counts["spoofed"] > 0
    robots = Robots((tmp_path / "robots.txt").read_text())
    R = analysis.analyze(con, robots, analysis.sitemap_paths((tmp_path / "sitemap.xml").read_text()))
    assert R["summary"]["requests"] == int(info["events"])
    assert R["synthetic_ranges"]  # no network in tests: generator fell back to synthetic ranges
    claimed = {s["claimed"] for s in R["spoofing"]}
    assert {"Googlebot", "GPTBot"} <= claimed
    lint = " ".join(l["message"] for l in R["robots"]["lint"])
    assert "Disalow" in lint and "case-sensitive" in lint and "own group" in lint
    assert any(v["bot"] == "Bytespider" and not v["spoofed"] for v in R["robots"]["violations"])
    assert any(b["path"] == "/pricing" for b in R["robots"]["blocked_popular"])
    assert any(g["path"].startswith("/docs/integrations/") for g in R["gaps"])
    from aicrawl import htmlreport, textreport
    assert "AI crawler report" in textreport.render(R)
    assert "<svg" not in htmlreport.render(R)  # charts are drawn client-side
