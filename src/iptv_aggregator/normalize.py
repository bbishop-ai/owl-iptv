from __future__ import annotations

import re
import unicodedata

from .models import Channel

QUALITY_WORDS = re.compile(r"\b(?:uhd|fhd|hd|sd|4k|8k|1080p?|720p?|backup|mirror)\b", re.IGNORECASE)
PUNCT = re.compile(r"[^a-z0-9]+")
ENGLISH_COUNTRIES = {"au", "ca", "gb", "ie", "nz", "us"}
COUNTRY_LANGUAGES = {
    "cn": "zh", "hk": "zh", "tw": "zh", "jp": "ja", "kr": "ko",
    "ru": "ru", "ua": "uk", "fr": "fr", "de": "de", "es": "es",
    "it": "it", "pt": "pt", "br": "pt", "tr": "tr", "in": "hi",
}

# Sports-first brand/token matcher with word boundaries.
# \bespn has no trailing boundary to match ESPNews/ESPNU/ESPN2.
# Bare fishing/hunting omitted; covered by brand patterns (outdoor\s*channel etc.).
_SPORTS = re.compile(
    r"\bsports?\b"
    r"|\bespn"
    r"|\bnfl\b|\bnba\b|\bmlb\b|\bnhl\b"
    r"|\bgolf\b|\btennis\b|\bsoccer\b|\bfootball\b"
    r"|\bbasketball\b|\bbaseball\b|\bhockey\b"
    r"|\bracing\b|\bnascar\b"
    r"|\bformula\s*1\b|\bf1\b"
    r"|\bmotogp\b"
    r"|\bwrestling\b|\bboxing\b|\bufc\b|\bmma\b"
    r"|\bbellator\b|\bpfl\b"
    r"|\bmonster\s*jam\b|\bworld\s*poker\s*tour\b|\bpoker\b"
    r"|\bbig\s*ten\b|\bbtn\b"
    r"|\bsec\s*network\b|\bacc\s*network\b"
    r"|\boutdoor\s*channel\b|\boutdoor\s*america\b"
    r"|\bduck\s*hunting\b|\bsportsman\b",
    re.IGNORECASE,
)


def normalized_name(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().lower()
    value = QUALITY_WORDS.sub(" ", value)
    return PUNCT.sub(" ", value).strip()


def classify_language(channel: Channel) -> str:
    explicit = channel.language.lower().split(";")[0].split(",")[0].strip()
    aliases = {"eng": "en", "english": "en", "en-us": "en", "en-gb": "en"}
    if explicit in aliases:
        return aliases[explicit]
    if explicit and explicit != "unknown":
        return explicit[:2]
    combined = " ".join((channel.name, channel.group, channel.attrs.get("metadata-name", ""), channel.attrs.get("metadata-alt-names", ""))).lower()
    if re.search(r"(?:en\s+espa[nñ]ol|las\s+tortugas|espa[nñ]ol|castellano)", combined):
        return "es"
    if re.search(r"(?:em\s+portugu[eê]s|portugu[eê]s)", combined):
        return "pt"
    if re.search(r"(?:en\s+fran[cç]ais|fran[cç]ais)", combined):
        return "fr"
    # A regional label is not evidence of English, even when a FAST channel has a US catalog ID.
    if re.search(r"(?:latin america|latinoam[eé]rica)", combined):
        return "unknown"
    if channel.country.lower() in ENGLISH_COUNTRIES:
        return "en"
    if channel.country.lower() in COUNTRY_LANGUAGES:
        return COUNTRY_LANGUAGES[channel.country.lower()]
    group = channel.group.lower()
    if any(word in group for word in ("english", "united states", "united kingdom", "canada", "australia")):
        return "en"
    if re.search(r"[\u4e00-\u9fff]", combined):
        return "zh"
    if re.search(r"[\u3040-\u30ff]", combined):
        return "ja"
    if re.search(r"[\uac00-\ud7af]", combined):
        return "ko"
    if re.search(r"[\u0400-\u04ff]", combined):
        return "ru"
    if re.search(r"[\u0600-\u06ff]", combined):
        return "ar"
    # ASCII alone is weak evidence; keep it unknown so the configurable caution policy decides.
    return "unknown"


def simple_category(channel: Channel) -> str:
    """Collapse provider labels into one stable, player-friendly category.

    Sports is deliberately evaluated first: a sports-first service can be tagged
    by an upstream as News, Outdoor, Auto, or General, but it belongs in the
    single sports browse bucket. General networks that merely air occasional
    games are not listed in the matcher and retain their ordinary category.
    """
    text = " ".join((channel.group, channel.name, channel.tvg_id, channel.attrs.get("metadata-name", ""))).casefold()
    groups = {part.strip().casefold() for part in re.split(r"[;,|]", channel.group) if part.strip()}
    if "sports" in groups or _SPORTS.search(text):
        return "Sports"
    if groups & {"news", "weather", "legislative", "current affairs"} or re.search(r"\bnews\b|weather|c-span|cspan|congress|senate|house of representatives", text):
        return "News"
    if "movies" in groups or re.search(r"movies?|films?|cinema", text):
        return "Movies"
    if "kids" in groups or "family" in groups or re.search(r"\bkids?\b|children|cartoon|nickelodeon|disney junior", text):
        return "Kids"
    if "music" in groups or re.search(r"\bmusic\b|concert|radio", text):
        return "Music"
    if "religious" in groups or re.search(r"religious|religion|church|gospel|bible|christian|islamic|quran", text):
        return "Religion"
    if "education" in groups or re.search(r"education|educational|science|history|learning|university", text):
        return "Education"
    if "local" in groups or re.search(r"\blocal\b|community", text):
        return "Local"
    if groups & {"lifestyle", "cooking", "outdoor", "travel", "auto", "business", "shop"} or re.search(r"lifestyle|cooking|food|travel|outdoor|fishing|auto|cars?|business|finance|shopping", text):
        return "Lifestyle"
    if groups & {"series", "entertainment", "comedy", "animation", "classic", "documentary", "culture", "general", "undefined"} or re.search(r"series|entertainment|comedy|animation|classic|documentary|culture", text):
        return "Entertainment"
    return "Other"


_JMP2_PLUTO = re.compile(r"^https?://jmp2\.uk/plu-([0-9a-f]{24})(?:\.m3u8)?(?:[?#].*)?$")


def rewrite_pluto_url(url: str, relay_base: str | None) -> str:
    """jmp2.uk Pluto short link -> owl Pluto relay (Pluto v1 shutdown, Oct 2026).

    jmp2.uk now redirects to a v2 stitcher URL carrying a JWT Pluto rejects (403);
    the relay mints a fresh Pluto guest session per play. Non-Pluto URLs, and every
    URL when no relay is configured, pass through unchanged.
    """
    if not relay_base:
        return url
    m = _JMP2_PLUTO.match(url or "")
    if not m:
        return url
    return f"{relay_base.rstrip('/')}/plu/{m.group(1)}.m3u8"


def normalize_channel(channel: Channel) -> Channel:
    channel.name = re.sub(r"\s+", " ", channel.name).strip()
    channel.language = classify_language(channel)
    key = normalized_name(channel.name)
    base_id = channel.tvg_id.split("@", 1)[0]
    channel.identity = f"id:{base_id.lower()}" if base_id else f"name:{key}"
    return channel


def language_allowed(channel: Channel, languages: list[str], allow_unknown: bool) -> bool:
    return channel.language in languages or (allow_unknown and channel.language == "unknown")
