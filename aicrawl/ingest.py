"""Log ingestion. All parsing happens inside DuckDB so multi-GB files stay fast.

Supported formats (auto-detected, .gz works too):
  combined    nginx / Apache "combined" format (also accepts "common" format without referer/UA)
  cloudflare  Cloudflare Logpush HTTP requests dataset, NDJSON
  cloudfront  AWS CloudFront standard logs (W3C, tab separated, '#Fields:' header)
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import duckdb

from .bots import classify_ua

# One quoted field that may contain backslash-escaped quotes (Apache escapes " as \").
_Q = r'"((?:[^"\\]|\\.)*)"'
COMBINED_RE = (r'^(\S+) \S+ \S+ \[([^\]]+)\] ' + _Q + r' (\d{3}) (\S+)(?: ' + _Q + ' ' + _Q + ')?')

INSERT_COLS = "ts, ip, method, host, path, query, status, bytes, ua, source"


def _open_text(path: Path):
    return gzip.open(path, "rt", errors="replace") if path.suffix == ".gz" else open(path, errors="replace")


def detect_format(path: Path) -> str:
    with _open_text(path) as f:
        for _ in range(20):
            line = f.readline()
            if not line:
                break
            s = line.strip()
            if not s:
                continue
            if s.startswith("#Version") or s.startswith("#Fields"):
                return "cloudfront"
            if s.startswith("{"):
                try:
                    obj = json.loads(s)
                except json.JSONDecodeError:
                    pass
                else:
                    if "ClientRequestURI" in obj or "ClientIP" in obj:
                        return "cloudflare"
            if " [" in s and '"' in s:
                return "combined"
    raise ValueError(f"could not detect log format of {path}; pass --format")


def _split_uri(expr: str) -> str:
    """SQL producing path and query columns from a request-target expression."""
    return (f"split_part({expr}, '?', 1) AS path, "
            f"NULLIF(substr({expr}, length(split_part({expr}, '?', 1)) + 2), '') AS query")


def _ingest_combined(con, path: str, host: str | None) -> int:
    con.execute(f"""
        INSERT INTO requests ({INSERT_COLS})
        WITH raw AS (
            SELECT regexp_extract(line, ?, ['ip','ts','req','status','bytes','ref','ua']) AS m
            FROM read_csv(?, columns={{'line': 'VARCHAR'}}, delim=E'\\x1f', quote='', escape='',
                          header=false, auto_detect=false, strict_mode=false)
        ), parsed AS (
            SELECT m.ip, m.ua, m.status, m.bytes,
                   timezone('UTC', try_strptime(m.ts, '%d/%b/%Y:%H:%M:%S %z')) AS ts,
                   split_part(m.req, ' ', 1) AS method,
                   split_part(m.req, ' ', 2) AS target
            FROM raw WHERE m.ip <> ''
        )
        SELECT ts, ip, method, ?, {_split_uri('target')},
               TRY_CAST(status AS SMALLINT), TRY_CAST(NULLIF(bytes, '-') AS BIGINT),
               NULLIF(replace(ua, '\\"', '"'), '-'), ?
        FROM parsed WHERE ts IS NOT NULL
    """, [COMBINED_RE, path, host, path])
    return _count_rows(con, path)


def _count_rows(con, path: str) -> int:
    return con.execute("SELECT count(*) FROM requests WHERE source = ?", [path]).fetchone()[0]


def _ingest_cloudflare(con, path: str, host: str | None) -> int:
    # EdgeStartTimestamp may be RFC3339, unix seconds, or unix nanoseconds depending on Logpush settings.
    con.execute(f"""
        INSERT INTO requests ({INSERT_COLS})
        WITH j AS (
            SELECT * FROM read_json(?, format='newline_delimited', ignore_errors=true, columns={{
                'ClientIP': 'VARCHAR', 'ClientRequestHost': 'VARCHAR', 'ClientRequestMethod': 'VARCHAR',
                'ClientRequestURI': 'VARCHAR', 'ClientRequestUserAgent': 'VARCHAR',
                'EdgeResponseStatus': 'INTEGER', 'EdgeResponseBytes': 'BIGINT', 'EdgeStartTimestamp': 'VARCHAR'}})
        )
        SELECT CASE
                 WHEN regexp_full_match(EdgeStartTimestamp, '\\d{{16,}}')
                   THEN make_timestamp(CAST(CAST(EdgeStartTimestamp AS HUGEINT) // 1000 AS BIGINT))
                 WHEN regexp_full_match(EdgeStartTimestamp, '\\d+')
                   THEN make_timestamp(CAST(EdgeStartTimestamp AS BIGINT) * 1000000)
                 ELSE timezone('UTC', TRY_CAST(EdgeStartTimestamp AS TIMESTAMPTZ))
               END,
               ClientIP, ClientRequestMethod, COALESCE(?, ClientRequestHost),
               {_split_uri('ClientRequestURI')},
               EdgeResponseStatus, EdgeResponseBytes, ClientRequestUserAgent, ?
        FROM j WHERE ClientIP IS NOT NULL
    """, [path, host, path])
    return _count_rows(con, path)


def _cloudfront_fields(path: Path) -> list[str]:
    with _open_text(path) as f:
        for line in f:
            if line.startswith("#Fields:"):
                return line.split(":", 1)[1].split()
            if not line.startswith("#"):
                break
    # CloudFront standard log default field order
    return ("date time x-edge-location sc-bytes c-ip cs-method cs(Host) cs-uri-stem sc-status "
            "cs(Referer) cs(User-Agent) cs-uri-query").split()


def _ingest_cloudfront(con, path: str, host: str | None) -> int:
    fields = _cloudfront_fields(Path(path))
    col = {name: f"column{i:02d}" for i, name in enumerate(fields)}
    need = ["date", "time", "c-ip", "cs-method", "cs-uri-stem", "sc-status", "cs(User-Agent)"]
    missing = [n for n in need if n not in col]
    if missing:
        raise ValueError(f"CloudFront log is missing fields: {missing}")
    width = len(fields)
    columns = "{" + ", ".join(f"'column{i:02d}': 'VARCHAR'" for i in range(width)) + "}"
    g = lambda n, default="NULL": col.get(n, default)  # noqa: E731
    # CloudFront URL-encodes the user agent (and sometimes double-encodes it).
    con.execute(f"""
        INSERT INTO requests ({INSERT_COLS})
        SELECT CAST({g('date')} || ' ' || {g('time')} AS TIMESTAMP),
               {g('c-ip')}, {g('cs-method')}, COALESCE(?, {g('cs(Host)')}),
               {g('cs-uri-stem')}, NULLIF({g('cs-uri-query')}, '-'),
               TRY_CAST({g('sc-status')} AS SMALLINT), TRY_CAST({g('sc-bytes')} AS BIGINT),
               url_decode(url_decode({g('cs(User-Agent)')})), ?
        FROM read_csv(?, delim=E'\\t', header=false, comment='#', quote='', escape='',
                      auto_detect=false, strict_mode=false, null_padding=true, columns={columns})
    """, [host, path, path])
    return _count_rows(con, path)


INGESTERS = {"combined": _ingest_combined, "cloudflare": _ingest_cloudflare, "cloudfront": _ingest_cloudfront}


def ingest(con: duckdb.DuckDBPyConnection, path: Path, fmt: str = "auto", host: str | None = None,
           replace: bool = True) -> tuple[str, int]:
    fmt = detect_format(path) if fmt == "auto" else fmt
    src = str(path)
    if replace:
        con.execute("DELETE FROM requests WHERE source = ?", [src])
    n = INGESTERS[fmt](con, src, host)
    classify_new_user_agents(con)
    return fmt, n


def classify_new_user_agents(con: duckdb.DuckDBPyConnection) -> int:
    """Classify each distinct user agent once in Python; there are far fewer UAs than requests."""
    new = con.execute("SELECT DISTINCT ua FROM requests WHERE ua IS NOT NULL "
                      "AND ua NOT IN (SELECT ua FROM ua_class)").fetchall()
    rows = [(ua, classify_ua(ua)) for (ua,) in new]
    if rows:
        con.executemany("INSERT INTO ua_class VALUES (?, ?)", rows)
    return len(rows)
