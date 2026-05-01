import re
from difflib import SequenceMatcher

URL_RE = re.compile(r"(https?://\S+|www\.\S+)", re.IGNORECASE)
SPACE_RE = re.compile(r"\s+")
NON_WORD_RE = re.compile(r"[^\w\s#@]", re.UNICODE)


def contains_url(text: str) -> bool:
    return bool(URL_RE.search(text))


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
