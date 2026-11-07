"""DuckDB storage: schema plus the derived bot / verification tables."""

from __future__ import annotations

import duckdb

from .bots import BOTS

SCHEMA = """
CREATE TABLE IF NOT EXISTS requests (
    ts      TIMESTAMP,      -- UTC
    ip      VARCHAR,
    method  VARCHAR,
    host    VARCHAR,
    path    VARCHAR,        -- without query string
    query   VARCHAR,
    status  SMALLINT,
    bytes   BIGINT,
    ua      VARCHAR,
    source  VARCHAR         -- file it came from
);
CREATE TABLE IF NOT EXISTS ip_ranges (
    list_name  VARCHAR,
    cidr       VARCHAR,
    fetched_at TIMESTAMP,
    origin     VARCHAR      -- 'vendor' (downloaded) or 'synthetic' (offline sample fallback)
);
CREATE TABLE IF NOT EXISTS ua_class (ua VARCHAR PRIMARY KEY, bot VARCHAR);
CREATE TABLE IF NOT EXISTS ip_verdict (
    ip VARCHAR, bot VARCHAR, verdict VARCHAR, method VARCHAR, detail VARCHAR,
    PRIMARY KEY (ip, bot)
);
CREATE TABLE IF NOT EXISTS rdns_cache (ip VARCHAR PRIMARY KEY, hostname VARCHAR, confirmed BOOLEAN);
"""

# Everything the reports read goes through this view.
HITS_VIEW = """
CREATE OR REPLACE VIEW hits AS
WITH c AS (
    SELECT r.*, COALESCE(u.bot, CASE WHEN r.ua IS NULL THEN '~other-bot' END) AS bot_name
    FROM requests r LEFT JOIN ua_class u ON u.ua = r.ua
)
SELECT c.* EXCLUDE (bot_name),
       COALESCE(c.bot_name, 'human') AS bot,
       COALESCE(b.category, CASE WHEN c.bot_name = '~other-bot' THEN 'other' ELSE 'human' END) AS category,
       b.vendor,
       CASE WHEN b.name IS NOT NULL THEN COALESCE(v.verdict, 'unchecked') END AS verdict
FROM c
LEFT JOIN bots b       ON b.name = c.bot_name
LEFT JOIN ip_verdict v ON v.ip = c.ip AND v.bot = c.bot_name
"""


def connect(path: str) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(path)
    con.execute("SET TimeZone = 'UTC'")
    con.execute(SCHEMA)
    con.execute("CREATE OR REPLACE TABLE bots (name VARCHAR, vendor VARCHAR, category VARCHAR, "
                "ip_list VARCHAR, rdns VARCHAR, robots_token VARCHAR)")
    con.executemany("INSERT INTO bots VALUES (?, ?, ?, ?, ?, ?)",
                    [(b.name, b.vendor, b.category, b.ip_list, ",".join(b.rdns), b.token) for b in BOTS])
    con.execute(HITS_VIEW)
    return con
