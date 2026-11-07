from aicrawl.robots import Robots, normalize, parse_llms_txt


def allowed(txt, token, path):
    return Robots(txt).check(token, path.split("?")[0], path.partition("?")[2] or None).allowed


def test_longest_match_wins_and_allow_wins_ties():
    txt = "User-agent: *\nDisallow: /docs/\nAllow: /docs/public/\nAllow: /x\nDisallow: /x\n"
    assert not allowed(txt, "GPTBot", "/docs/secret")
    assert allowed(txt, "GPTBot", "/docs/public/a")
    assert allowed(txt, "GPTBot", "/x")  # same length -> Allow wins


def test_specific_group_replaces_star_group():
    txt = "User-agent: *\nDisallow: /admin\n\nUser-agent: GPTBot\nDisallow: /private\n"
    assert allowed(txt, "GPTBot", "/admin")       # * rules don't apply once GPTBot is named
    assert not allowed(txt, "GPTBot", "/private")
    assert not allowed(txt, "ClaudeBot", "/admin")


def test_multiple_user_agents_share_group_and_groups_merge():
    txt = ("User-agent: GPTBot\nUser-agent: CCBot\nDisallow: /a\n\n"
           "User-agent: gptbot\nDisallow: /b\n")
    assert not allowed(txt, "CCBot", "/a")
    assert not allowed(txt, "GPTBot", "/a") and not allowed(txt, "GPTBot", "/b")
    assert allowed(txt, "CCBot", "/b")


def test_wildcards_and_end_anchor():
    txt = "User-agent: *\nDisallow: /*.pdf$\nDisallow: /*?sessionid=\n"
    assert not allowed(txt, "x", "/files/a.pdf")
    assert allowed(txt, "x", "/files/a.pdf?download=1")
    assert not allowed(txt, "x", "/page?sessionid=1")


def test_empty_disallow_robots_txt_and_case_sensitivity():
    assert allowed("User-agent: *\nDisallow:\n", "x", "/anything")
    assert allowed("User-agent: *\nDisallow: /\n", "x", "/robots.txt")
    assert allowed("User-agent: *\nDisallow: /Docs/\n", "x", "/docs/a")


def test_product_token_matching_ignores_version_and_case():
    txt = "User-agent: gptbot/1.1\nDisallow: /\n"
    assert not allowed(txt, "GPTBot", "/")


def test_blank_lines_and_comments_do_not_end_group():
    txt = "User-agent: GPTBot\n\n# note\nDisallow: /a\n"
    assert not allowed(txt, "GPTBot", "/a")


def test_typo_only_honoured_by_googlebot():
    txt = "User-agent: *\nDisalow: /drafts/\n"
    r = Robots(txt)
    assert r.check("Googlebot", "/drafts/x").allowed is False
    assert r.check("PerplexityBot", "/drafts/x").allowed is True
    assert any(l.severity == "error" and "Disalow" in l.message for l in r.lint)


def test_percent_encoding_normalisation():
    assert normalize("/caf%c3%a9") == normalize("/café") == "/caf%C3%A9"
    assert normalize("/%7Euser") == "/~user"
    assert not allowed("User-agent: *\nDisallow: /café\n", "x", "/caf%C3%A9")


def test_bom_crlf_and_rules_before_user_agent():
    r = Robots("﻿Disallow: /x\r\nUser-agent: *\r\nDisallow: /y\r\n")
    assert r.check("a", "/x").allowed and not r.check("a", "/y").allowed
    assert any("before any User-agent" in l.message for l in r.lint)


def test_content_usage_collected():
    r = Robots("User-agent: *\nContent-Usage: train-ai=n\nContent-Usage: /blog/ train-ai=y\n")
    assert r.content_usage_for("ClaudeBot") == [(None, "train-ai=n", 2), ("/blog/", "train-ai=y", 3)]


def test_lint_against_paths():
    r = Robots("User-agent: *\nDisallow: /admin\nDisallow: /Blog/\n\nUser-agent: OAI-SearchBot\nDisallow: /x\n")
    msgs = [l.message for l in r.lint_against_paths(["/admin", "/administration-guide", "/blog/a"],
                                                     ["OAI-SearchBot"])]
    assert any("prefix match" in m and "/administration-guide" in m for m in msgs)
    assert any("case" in m for m in msgs)
    assert any("OAI-SearchBot has its own group" in m for m in msgs)


def test_llms_txt():
    l = parse_llms_txt("# Site\n\n> Summary\n\n## Docs\n- [A](https://x.com/a): note\n- [B](/b)\n")
    assert l.title == "Site" and l.summary == "Summary" and l.links == ["https://x.com/a", "/b"]
    assert parse_llms_txt("<html>").problems
