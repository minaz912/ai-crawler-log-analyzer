"""robots.txt parsing per RFC 9309, plus a linter for the mistakes that quietly block or admit bots.

Why not urllib.robotparser? It uses first-match instead of longest-match, ignores the `*` and
`$` wildcards, and doesn't merge repeated groups, so it gives wrong answers on real files.

Rules implemented here:
  * A group is one or more consecutive `User-agent` lines followed by rules. A `User-agent`
    line after a rule starts a new group. Blank lines and comments do not end a group.
  * A crawler obeys only the group(s) naming its product token (case-insensitive). All groups
    naming it are merged. Only if none name it does it fall back to `*` groups. The specific
    group does NOT inherit `*` rules - a frequent source of accidental access.
  * Longest matching pattern wins; on a tie, Allow wins. `*` matches any run of characters,
    a trailing `$` anchors the end. Paths are compared case-sensitively after
    percent-encoding normalisation.
  * An empty `Disallow:` allows everything. `/robots.txt` itself is always allowed.
  * Misspelled directive names (e.g. `useragent`, `disalow`) are honoured only for Googlebot,
    which documents that tolerance; other crawlers are treated as skipping the line. Either
    way it is reported as a lint error.
  * `Content-Usage` lines (IETF aipref draft, e.g. `Content-Usage: train-ai=n`) are collected.
"""

from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass, field

MAX_BYTES = 500 * 1024  # RFC 9309 says crawlers must parse at least 500 KiB; Google stops there

_KEY_ALIASES = {
    "user-agent": "user-agent", "useragent": "user-agent", "user agent": "user-agent",
    "allow": "allow",
    "disallow": "disallow", "dissallow": "disallow", "dissalow": "disallow",
    "disalow": "disallow", "diasllow": "disallow", "disallaw": "disallow",
    "sitemap": "sitemap", "site-map": "sitemap",
    "crawl-delay": "crawl-delay",
    "content-usage": "content-usage",
    "host": "host", "clean-param": "clean-param", "noindex": "noindex", "request-rate": "request-rate",
    "visit-time": "visit-time",
}
_CANONICAL = {"user-agent", "allow", "disallow", "sitemap", "crawl-delay", "content-usage"}
# Crawlers known to accept misspelled directive names. Everyone else skips those lines.
TYPO_TOLERANT = {"googlebot"}


@dataclass
class Rule:
    allow: bool
    pattern: str
    line: int
    typo: bool = False  # directive name was misspelled; only Google-style parsers honour it
    _rx: re.Pattern | None = field(default=None, repr=False, compare=False)

    def regex(self) -> re.Pattern:
        if self._rx is None:
            p = normalize(self.pattern)
            anchored = p.endswith("$")
            if anchored:
                p = p[:-1]
            body = ".*".join(re.escape(part) for part in p.split("*"))
            self._rx = re.compile(body + ("$" if anchored else ""), re.S)
        return self._rx

    def matches(self, path: str) -> bool:
        return self.regex().match(path) is not None

    @property
    def specificity(self) -> int:
        return len(normalize(self.pattern))


@dataclass
class Group:
    agents: list[str]
    rules: list[Rule] = field(default_factory=list)
    content_usage: list[tuple[str | None, str, int]] = field(default_factory=list)  # (path, value, line)
    line: int = 0


@dataclass
class Lint:
    severity: str  # 'error' | 'warning' | 'info'
    line: int | None
    message: str


@dataclass
class Verdict:
    allowed: bool
    rule: Rule | None
    group: str  # which user-agent group decided: token, '*', or '(none)'


_UNRESERVED = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")


def normalize(path: str) -> str:
    """Percent-encoding normalisation: decode unreserved chars, encode non-ASCII as UTF-8,
    upper-case hex digits. Both patterns and request paths go through this."""
    out = []
    i = 0
    while i < len(path):
        ch = path[i]
        if ch == "%" and i + 2 < len(path) and re.fullmatch(r"[0-9A-Fa-f]{2}", path[i + 1:i + 3]):
            c = chr(int(path[i + 1:i + 3], 16))
            out.append(c if c in _UNRESERVED else "%" + path[i + 1:i + 3].upper())
            i += 3
            continue
        if ord(ch) > 127 or ch == " ":
            out.append(urllib.parse.quote(ch, safe=""))
        else:
            out.append(ch)
        i += 1
    return "".join(out)


def product_token(ua_value: str) -> str:
    """'GPTBot/1.1 (+https://...)' -> 'gptbot'. robots.txt matches the product token only."""
    m = re.match(r"\s*([A-Za-z0-9_*-]+)", ua_value)
    return m.group(1).lower() if m else ua_value.strip().lower()


class Robots:
    def __init__(self, text: str):
        self.raw = text
        self.groups: list[Group] = []
        self.sitemaps: list[str] = []
        self.lint: list[Lint] = []
        self._parse(text)

    # -------------------------------------------------------------- parsing
    def _parse(self, text: str) -> None:
        if len(text.encode("utf-8", "replace")) > MAX_BYTES:
            self.lint.append(Lint("warning", None, f"File is over {MAX_BYTES // 1024} KiB; Google and "
                                  "others ignore everything past that point."))
        if text.startswith("﻿"):
            text = text[1:]
        current: Group | None = None
        seen_rule = False
        for n, raw_line in enumerate(text.splitlines(), 1):
            line = raw_line.split("#", 1)[0].strip()
            if not line:
                continue
            if ":" not in line:
                self.lint.append(Lint("warning", n, f"Ignored line without ':' -> {raw_line.strip()!r}"))
                continue
            key_raw, value = line.split(":", 1)
            key_l = key_raw.strip().lower()
            value = value.strip()
            key = _KEY_ALIASES.get(key_l)
            if key is None:
                self.lint.append(Lint("warning", n, f"Unknown directive {key_raw.strip()!r} is ignored."))
                continue
            typo = key_l not in _CANONICAL and key in _CANONICAL
            if typo:
                self.lint.append(Lint("error", n, f"Typo {key_raw.strip()!r}: Google reads it as "
                                      f"'{key.title()}', most other crawlers (incl. AI bots) skip the line."))
            if key == "user-agent":
                if current is None or seen_rule:
                    current = Group(agents=[], line=n)
                    self.groups.append(current)
                    seen_rule = False
                current.agents.append(product_token(value))
            elif key in ("allow", "disallow"):
                if current is None:
                    self.lint.append(Lint("warning", n, "Rule appears before any User-agent line and is ignored."))
                    continue
                seen_rule = True
                if not value:
                    if key == "allow":
                        continue
                    # empty Disallow = allow everything; keep nothing
                    continue
                if not value.startswith(("/", "*")):
                    self.lint.append(Lint("warning", n, f"Path {value!r} does not start with '/'; "
                                          "crawlers may ignore or misread it."))
                    value = "/" + value
                current.rules.append(Rule(key == "allow", value, n, typo))
            elif key == "content-usage":
                if current is None:
                    self.lint.append(Lint("warning", n, "Content-Usage before any User-agent line is ignored."))
                    continue
                seen_rule = True
                parts = value.split(None, 1)
                if parts and parts[0].startswith("/"):
                    current.content_usage.append((parts[0], parts[1] if len(parts) > 1 else "", n))
                else:
                    current.content_usage.append((None, value, n))
            elif key == "sitemap":
                self.sitemaps.append(value)
            elif key == "crawl-delay":
                seen_rule = True
                self.lint.append(Lint("info", n, "Crawl-delay is non-standard; Googlebot and most AI "
                                      "crawlers ignore it."))
            else:
                self.lint.append(Lint("info", n, f"'{key_raw.strip()}' is not part of RFC 9309 and is "
                                      "ignored by most crawlers."))
        for g in self.groups:
            if not g.rules and not g.content_usage:
                self.lint.append(Lint("info", g.line, f"Group for {', '.join(g.agents)} has no rules, "
                                      "so it allows everything."))
        tokens: dict[str, list[int]] = {}
        for g in self.groups:
            for a in g.agents:
                tokens.setdefault(a, []).append(g.line)
        for a, lines in tokens.items():
            if len(lines) > 1:
                self.lint.append(Lint("info", lines[1], f"'{a}' appears in {len(lines)} groups "
                                      f"(lines {', '.join(map(str, lines))}); they are merged."))

    # -------------------------------------------------------------- evaluation
    def groups_for(self, token: str) -> tuple[list[Group], str]:
        t = token.lower()
        named = [g for g in self.groups if t in g.agents]
        if named:
            return named, token
        star = [g for g in self.groups if "*" in g.agents]
        return star, ("*" if star else "(none)")

    def names(self, token: str) -> bool:
        return any(token.lower() in g.agents for g in self.groups)

    def check(self, token: str, path: str, query: str | None = None) -> Verdict:
        target = normalize(path + ("?" + query if query else ""))
        if path == "/robots.txt":
            return Verdict(True, None, "(always allowed)")
        groups, label = self.groups_for(token)
        best: Rule | None = None
        for g in groups:
            for r in g.rules:
                if r.typo and token.lower() not in TYPO_TOLERANT:
                    continue
                if r.matches(target):
                    if (best is None or r.specificity > best.specificity
                            or (r.specificity == best.specificity and r.allow and not best.allow)):
                        best = r
        return Verdict(best is None or best.allow, best, label)

    def content_usage_for(self, token: str) -> list[tuple[str | None, str, int]]:
        groups, _ = self.groups_for(token)
        return [cu for g in groups for cu in g.content_usage]

    # -------------------------------------------------------------- linting against real traffic
    def lint_against_paths(self, paths: list[str], star_tokens_affected: list[str]) -> list[Lint]:
        """Checks that need the site's real URLs: case mistakes, prefix overreach, lost * rules."""
        out: list[Lint] = []
        lower_paths = {}
        for p in paths:
            lower_paths.setdefault(p.lower(), p)
        for g in self.groups:
            for r in g.rules:
                hits = [p for p in paths if r.matches(normalize(p))]
                verb = "Allow" if r.allow else "Disallow"
                if not hits and "*" not in r.pattern:
                    probe = r.pattern.rstrip("$").lower()
                    ci = [p for p in paths if p.lower().startswith(probe)]
                    if ci:
                        out.append(Lint("error", r.line, f"{verb}: {r.pattern} matches no URL on the site, but "
                                        f"{len(ci)} URL(s) match ignoring case (e.g. {ci[0]}). Paths are "
                                        "case-sensitive, so this rule does nothing."))
                if not r.allow and hits and not r.pattern.endswith(("/", "$", "*")):
                    over = [p for p in hits if len(p) > len(r.pattern) and p[len(r.pattern)] not in "/?."]
                    if over:
                        out.append(Lint("warning", r.line, f"Disallow: {r.pattern} is a prefix match and also "
                                        f"blocks {len(over)} other URL(s) such as {over[0]}. Add a trailing "
                                        "'/' or '$' if only the directory was meant."))
        star_rules = [r for g in self.groups if "*" in g.agents for r in g.rules if not r.allow]
        if star_rules:
            for token in star_tokens_affected:
                if self.names(token):
                    own = self.groups_for(token)[0]
                    own_patterns = {r.pattern for g in own for r in g.rules}
                    lost = [r.pattern for r in star_rules if r.pattern not in own_patterns]
                    if lost and not any(not r.allow and r.pattern == "/" for g in own for r in g.rules):
                        out.append(Lint("warning", own[0].line, f"{token} has its own group, so the '*' "
                                        f"rules don't apply to it: it may crawl {', '.join(lost[:4])}"
                                        f"{'...' if len(lost) > 4 else ''}. Repeat them in its group if "
                                        "that wasn't intended."))
        return out


# ------------------------------------------------------------------ llms.txt

@dataclass
class LlmsTxt:
    title: str | None
    summary: str | None
    sections: dict[str, list[tuple[str, str]]]  # section -> [(link text, url)]
    problems: list[str]

    @property
    def links(self) -> list[str]:
        return [u for items in self.sections.values() for _, u in items]


def parse_llms_txt(text: str) -> LlmsTxt:
    """llms.txt (llmstxt.org): '# Title', optional '> summary', then '## Section' lists of
    '- [name](url): notes'."""
    title = summary = None
    sections: dict[str, list[tuple[str, str]]] = {}
    problems: list[str] = []
    current = None
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("# ") and title is None:
            title = s[2:].strip()
        elif s.startswith("> ") and summary is None and not sections:
            summary = s[2:].strip()
        elif s.startswith("## "):
            current = s[3:].strip()
            sections.setdefault(current, [])
        else:
            for name, url in re.findall(r"\[([^\]]+)\]\(([^)\s]+)\)", s):
                sections.setdefault(current or "(no section)", []).append((name, url))
    if not title:
        problems.append("No H1 title ('# Site name') - it is the only required element.")
    if text.lstrip().startswith("<"):
        problems.append("Looks like HTML, not markdown (maybe a soft-404 page).")
    if not any(sections.values()):
        problems.append("No links found.")
    return LlmsTxt(title, summary, sections, problems)
