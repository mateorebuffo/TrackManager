"""
Low-level text utilities shared across services.
"""
import re
import unicodedata


# Patterns that should be stripped from raw titles
_STRIP_PATTERNS: list[re.Pattern] = [
    # Leading promo prefixes
    re.compile(r"^gift\s+track\s*[|﹨\\]?\s*", re.IGNORECASE),
    # Download / promo tags
    re.compile(r"\[free\s*download\]", re.IGNORECASE),
    re.compile(r"\(free\s*download\)", re.IGNORECASE),
    re.compile(r"\bfree\s*download\b", re.IGNORECASE),
    re.compile(r"\[out\s*now\]", re.IGNORECASE),
    re.compile(r"\(out\s*now\)", re.IGNORECASE),
    re.compile(r"\bout\s*now\b", re.IGNORECASE),
    # Video / official tags
    re.compile(r"\bofficial\s*(music\s*)?video\b", re.IGNORECASE),
    re.compile(r"\bofficial\s*audio\b", re.IGNORECASE),
    re.compile(r"\blyrics?\s*video\b", re.IGNORECASE),
    re.compile(r"\blyrics?\b", re.IGNORECASE),
    # Premiere markers — handles all forms:
    #   "Premiere:", "• PREMIERE •", "PREMIERE •", etc.
    #   The \s*:?\s* after premiere also eats the colon in "Premiere: 5 - "
    re.compile(r"[•·]*\s*premiere\s*[•·]*\s*:?\s*", re.IGNORECASE),
    re.compile(r"\bexclusive\b", re.IGNORECASE),
    # Remaining bullet / interpunct / asterisk decorators
    re.compile(r"[•·*]+"),
    # Leftover leading colon (e.g. edge case ": 5 - ") after premiere removal
    re.compile(r"^:\s*"),
    # Label / copyright markers in brackets (including empty [])
    re.compile(r"\[[^\]]{0,40}\]"),   # generic [label] tag ≤40 chars (0 allows [])
    # Year patterns at end  e.g. (2023)
    re.compile(r"\(\d{4}\)\s*$"),
    # Original Mix — adds no useful info, not a remix or edit
    re.compile(r"[\(\[]\s*original\s*mix\s*[\)\]]", re.IGNORECASE),
    re.compile(r"\boriginal\s*mix\b", re.IGNORECASE),  # without brackets too
    # Preview / snippet markers
    re.compile(r"[\(\[]\s*preview\s*[\)\]]", re.IGNORECASE),
    re.compile(r"\bsnippets?\b", re.IGNORECASE),
    # Clip / visualizer
    re.compile(r"\b(clip|visualizer|teaser)\b", re.IGNORECASE),
    # Hashtags (including the # symbol itself)
    re.compile(r"#\w*"),
    # Vinyl side / track labels at start: A1, B2, C3, etc.
    re.compile(r"^[A-Da-d][0-9]\s*[-–]?\s*"),
    # Vinyl side / track labels at end: (B1), [A2], " B1", " A2"
    re.compile(r"\s+[A-Da-d][0-9]$"),
    re.compile(r"[\(\[][A-Da-d][0-9][\)\]]"),
    # Leading standalone track number after premiere removal: "5 - ", "4 - "
    re.compile(r"^\d+\s*[-–]\s*"),
    # Leading catalog/label codes: "CB033 - ", "SR015 - "
    re.compile(r"^[A-Z]{2,}\d+\w*\s*-\s*"),
    # Trailing catalog/label codes: " - TALMAN16", " - SR015", " - LCS024"
    # Strips the code and everything after (e.g. " - TALMAN16 - Samples")
    re.compile(r"\s*-\s*[A-Z]{2,}\d+\w*(?:\s*-\s*.+)?$"),
]

# Only strip these generic version tags — they add no useful info.
# Everything else (Remix, Extended Mix, VIP, Club Mix, etc.) stays in the title
# because it identifies who remixed it or what version it is.
_VERSION_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\bradio\s*edit\b", re.IGNORECASE), "Radio Edit"),
]

# Emoji range
_EMOJI_RE = re.compile(
    "["
    "\U0001F600-\U0001F64F"
    "\U0001F300-\U0001F5FF"
    "\U0001F680-\U0001F6FF"
    "\U0001F1E0-\U0001F1FF"
    "\U00002702-\U000027B0"
    "\U000024C2-\U0001F251"
    "]+",
    flags=re.UNICODE,
)


def remove_emojis(text: str) -> str:
    return _EMOJI_RE.sub("", text)


def normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def clean_edges(text: str) -> str:
    """Strip leading/trailing punctuation and whitespace (dots, dashes, pipes, etc.)."""
    return text.strip(" \t\n\r.-–—|/\\:,")


def strip_noise(text: str) -> str:
    """Remove promotional/meta noise from a raw title string."""
    # Normalise exotic dash/separator characters to plain ' - ' before any other processing
    text = re.sub(r"\s*[\u2500\u2013\u2014\u2015\u2212]\s*", " - ", text)
    # Normalise "Artist -Title" (space + dash + no space) → "Artist - Title"
    text = re.sub(r" -([^\s\-])", r" - \1", text)
    # Truncate everything after ' EP' / ' EP3' / '- EP' when followed by more content
    # e.g. "KOLTER - That was fresh ep - uts17" → "KOLTER - That was fresh ep"
    # e.g. "Split EP3 - hpf027" → "Split EP3"
    text = re.sub(r"(\bep\d*)\s*-\s*.+", r"\1", text, flags=re.IGNORECASE)
    for pattern in _STRIP_PATTERNS:
        text = pattern.sub("", text)
    text = remove_emojis(text)
    return normalize_whitespace(text)


_VERSION_KEYWORDS_RE = re.compile(
    r"\b(remix|edit|mix|vip|version|dub|instrumental|bootleg|rework|flip|"
    r"feat\.?|ft\.?|live|acoustic|club|extended|radio|remaster)\b",
    re.IGNORECASE,
)


def strip_label_parens(text: str) -> str:
    """Remove parenthesised content that is NOT a version/remix/edit label.

    Keeps: (Youandewan Remix), (Ksea Edit), (Extended Mix)
    Strips: (LCS024), (Shift Records), (Bandcamp), (SR015)
    """
    def _keep(inner: str) -> bool:
        return bool(_VERSION_KEYWORDS_RE.search(inner))

    result = re.sub(r"\(([^)]*)\)", lambda m: m.group(0) if _keep(m.group(1)) else "", text)
    return normalize_whitespace(result)


def extract_version(text: str) -> tuple[str, str | None]:
    """
    Detect a version label inside parentheses or brackets (e.g. '(Extended Mix)').
    Returns (text_without_version_paren, version_label | None).
    """
    # Look for (something) or [something] that contains a version keyword
    paren_re = re.compile(r"[\(\[](.*?)[\)\]]")
    for match in paren_re.finditer(text):
        inner = match.group(1)
        for version_pat, label in _VERSION_PATTERNS:
            if version_pat.search(inner):
                cleaned = text[: match.start()] + text[match.end() :]
                return normalize_whitespace(cleaned), label
    return text, None


# Suffixes that identify a channel as a label/promoter rather than an artist.
# Checked case-insensitively as whole words anywhere in the name.
_LABEL_KEYWORDS = re.compile(
    r"\b(records?|recordings?|music|audio|label|tracks?|management|"
    r"agency|presents?|official|club|bar|radio|podcast|collective|"
    r"entertainment|productions?|studios?|sounds?)\b",
    re.IGNORECASE,
)


def is_label_channel(name: str) -> bool:
    """Return True if the channel name looks like a label or promoter, not an artist."""
    return bool(_LABEL_KEYWORDS.search(name))


def split_artist_title(raw: str, raw_artist: str | None) -> tuple[str, str]:
    """
    Split 'Artist - Title' into (artist, title).

    Priority:
    1. If the raw title contains a recognised separator (' - ', ' – ', ' — ', ' • '),
       split on it — this is the most reliable signal regardless of raw_artist.
    2. If no separator is found AND raw_artist is set AND it doesn't look like a
       label/promoter channel, use raw_artist as artist and full raw as title.
    3. Otherwise return ("", raw) — no artist detected.
    """
    # Normalise bullet to dash before splitting
    normalized = raw.replace(" • ", " - ").replace("• ", " - ").replace(" •", " - ")

    for sep in (" - ", " – ", " — ", " \u2500 ", " \u2013 ", " \u2014 "):
        if sep in normalized:
            parts = normalized.split(sep, 1)
            return parts[0].strip(), parts[1].strip()

    # No separator — try raw_artist as fallback if it looks like a real artist
    if raw_artist and raw_artist.strip() and not is_label_channel(raw_artist):
        return raw_artist.strip(), raw.strip()

    return "", raw.strip()


def build_fingerprint(artist: str, title: str, version: str | None) -> str:
    """Lowercase canonical fingerprint for deduplication."""
    parts = [_normalize_for_fingerprint(artist), _normalize_for_fingerprint(title)]
    if version:
        parts.append(_normalize_for_fingerprint(version))
    return "|".join(parts)


def _normalize_for_fingerprint(text: str) -> str:
    """Lowercase, remove accents, collapse whitespace, remove punctuation."""
    text = unicodedata.normalize("NFD", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    text = text.lower()
    text = re.sub(r"[^\w\s]", "", text)
    return normalize_whitespace(text)
