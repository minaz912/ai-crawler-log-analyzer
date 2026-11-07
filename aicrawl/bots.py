"""Registry of known AI (and reference search) crawlers.

Categories, which is the split a marketer cares about:
  training  - bulk crawlers collecting data to train models
  search    - indexers that build an AI search / answer index
  user      - live fetches triggered by a person asking an assistant something
  classic   - traditional search engines, kept as a baseline for comparison

Verification sources are each vendor's published IP list (JSON in the
Google "prefixes" format) and/or reverse-DNS suffixes. Some vendors publish
nothing, so those bots can only be reported as "unverifiable".
"""

from __future__ import annotations

from dataclasses import dataclass, field

CATEGORIES = {
    "training": "Training crawler",
    "search": "AI search indexer",
    "user": "User-triggered fetch",
    "classic": "Classic search (baseline)",
}

# Published IP lists. Several bots can share one list (Anthropic publishes a single file).
IP_LISTS: dict[str, str] = {
    "openai-gptbot": "https://openai.com/gptbot.json",
    "openai-searchbot": "https://openai.com/searchbot.json",
    "openai-chatgpt-user": "https://openai.com/chatgpt-user.json",
    "anthropic": "https://claude.com/crawling/bots.json",
    "perplexity-bot": "https://www.perplexity.com/perplexitybot.json",
    "perplexity-user": "https://www.perplexity.com/perplexity-user.json",
    "google-googlebot": "https://developers.google.com/static/search/apis/ipranges/googlebot.json",
    "bing-bingbot": "https://www.bing.com/toolbox/bingbot.json",
    "apple-applebot": "https://search.developer.apple.com/applebot.json",
    "commoncrawl-ccbot": "https://index.commoncrawl.org/ccbot.json",
    "duckduckgo-duckassistbot": "https://duckduckgo.com/duckassistbot.json",
    "mistral-user": "https://mistral.ai/mistralai-user-ips.json",
}


@dataclass(frozen=True)
class Bot:
    name: str                 # canonical name, also the substring searched for in the user agent
    vendor: str
    category: str
    ip_list: str | None = None
    rdns: tuple[str, ...] = ()  # accepted reverse-DNS suffixes (forward-confirmed)
    robots_token: str | None = None  # token used in robots.txt, if different from name
    ua_patterns: tuple[str, ...] = field(default=())  # extra substrings that identify the bot

    @property
    def token(self) -> str:
        return self.robots_token or self.name

    @property
    def verifiable(self) -> bool:
        return bool(self.ip_list or self.rdns)


BOTS: list[Bot] = [
    # OpenAI
    Bot("GPTBot", "OpenAI", "training", "openai-gptbot"),
    Bot("OAI-SearchBot", "OpenAI", "search", "openai-searchbot"),
    Bot("ChatGPT-User", "OpenAI", "user", "openai-chatgpt-user"),
    # Anthropic
    Bot("ClaudeBot", "Anthropic", "training", "anthropic"),
    Bot("Claude-SearchBot", "Anthropic", "search", "anthropic"),
    Bot("Claude-User", "Anthropic", "user", "anthropic"),
    Bot("anthropic-ai", "Anthropic", "training", "anthropic"),
    # Perplexity
    Bot("PerplexityBot", "Perplexity", "search", "perplexity-bot"),
    Bot("Perplexity-User", "Perplexity", "user", "perplexity-user"),
    # Others
    Bot("CCBot", "Common Crawl", "training", "commoncrawl-ccbot", rdns=("crawl.commoncrawl.org",)),
    Bot("Bytespider", "ByteDance", "training"),
    Bot("Meta-ExternalAgent", "Meta", "training", ua_patterns=("meta-externalagent",)),
    Bot("Meta-ExternalFetcher", "Meta", "user", ua_patterns=("meta-externalfetcher",)),
    Bot("Amazonbot", "Amazon", "search", rdns=("crawl.amazonbot.amazon",)),
    Bot("Applebot", "Apple", "search", "apple-applebot", rdns=("applebot.apple.com",)),
    Bot("DuckAssistBot", "DuckDuckGo", "user", "duckduckgo-duckassistbot"),
    Bot("MistralAI-User", "Mistral", "user", "mistral-user"),
    Bot("cohere-ai", "Cohere", "user"),
    Bot("Diffbot", "Diffbot", "training"),
    Bot("YouBot", "You.com", "search"),
    # Baselines
    Bot("Googlebot", "Google", "classic", "google-googlebot", rdns=("googlebot.com", "google.com")),
    Bot("bingbot", "Microsoft", "classic", "bing-bingbot", rdns=("search.msn.com",)),
]

# Tokens that only exist in robots.txt (no crawler sends them as a user agent).
# They control whether content already fetched by the parent crawler can be used for AI.
CONTROL_TOKENS = {
    "Google-Extended": "Googlebot content used for Gemini training/grounding",
    "Applebot-Extended": "Applebot content used for Apple AI training",
}

BY_NAME = {b.name: b for b in BOTS}

# Longest first, so a more specific token wins if one contains another.
_MATCHERS = sorted(
    ((p.lower(), b) for b in BOTS for p in (b.name, *b.ua_patterns)),
    key=lambda pb: -len(pb[0]),
)

_GENERIC_BOT_HINTS = ("bot", "crawl", "spider", "slurp", "curl/", "wget", "python-requests",
                      "python-urllib", "go-http-client", "httpclient", "scrapy", "headless")


def classify_ua(ua: str | None) -> str | None:
    """Return the bot name for a user agent, '~other-bot' for generic automation, or None for humans."""
    if not ua:
        return "~other-bot"
    low = ua.lower()
    for pattern, bot in _MATCHERS:
        if pattern in low:
            return bot.name
    if any(h in low for h in _GENERIC_BOT_HINTS):
        return "~other-bot"
    return None
