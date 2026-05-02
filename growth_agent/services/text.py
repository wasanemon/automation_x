import re
from difflib import SequenceMatcher
from urllib.parse import urlparse

URL_RE = re.compile(r"(https?://\S+|www\.\S+)", re.IGNORECASE)
SPACE_RE = re.compile(r"\s+")
NON_WORD_RE = re.compile(r"[^\w\s#@]", re.UNICODE)
URL_TRAILING_PUNCTUATION = ".,!?)]}'\""
SHORTENER_DOMAINS = {
    "bit.ly",
    "buff.ly",
    "cutt.ly",
    "goo.gl",
    "is.gd",
    "lnkd.in",
    "ow.ly",
    "rebrand.ly",
    "t.co",
    "tiny.cc",
    "tinyurl.com",
}


def contains_url(text: str) -> bool:
    return bool(URL_RE.search(text))


def extract_urls(text: str) -> list[str]:
    return [match.group(0).rstrip(URL_TRAILING_PUNCTUATION) for match in URL_RE.finditer(text)]


def extract_url_domains(text: str) -> list[str]:
    domains: list[str] = []
    for url in extract_urls(text):
        domain = url_domain(url)
        if domain:
            domains.append(domain)
    return domains


def url_domain(url: str) -> str:
    normalized_url = url if url.lower().startswith(("http://", "https://")) else f"https://{url}"
    parsed = urlparse(normalized_url)
    hostname = (parsed.hostname or "").lower().strip(".")
    return hostname.removeprefix("www.")


def is_owned_domain(domain: str, owned_domains: tuple[str, ...]) -> bool:
    normalized = domain.lower().strip(".").removeprefix("www.")
    return any(normalized == owned or normalized.endswith(f".{owned}") for owned in owned_domains)


def is_shortened_domain(domain: str) -> bool:
    normalized = domain.lower().strip(".").removeprefix("www.")
    return normalized in SHORTENER_DOMAINS


def normalize_text(text: str) -> str:
    lowered = text.lower()
    without_urls = URL_RE.sub("", lowered)
    without_punctuation = NON_WORD_RE.sub(" ", without_urls)
    return SPACE_RE.sub(" ", without_punctuation).strip()


def similarity(left: str, right: str) -> float:
    normalized_left = normalize_text(left)
    normalized_right = normalize_text(right)
    if not normalized_left or not normalized_right:
        return 0
    if normalized_left == normalized_right:
        return 1
    return SequenceMatcher(None, normalized_left, normalized_right).ratio()


def truncate_sentence(text: str, limit: int = 140) -> str:
    compact = SPACE_RE.sub(" ", text).strip()
    if len(compact) <= limit:
        return compact
    return f"{compact[: limit - 3].rstrip()}..."
