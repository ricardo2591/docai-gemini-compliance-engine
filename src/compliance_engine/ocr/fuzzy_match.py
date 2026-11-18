"""OCR text normalization, lookalike digit mapping, and fuzzy ID/name matching."""

from __future__ import annotations

import re
import unicodedata

LOOKALIKE_DIGIT_MAP = str.maketrans(
    {
        "O": "0",
        "o": "0",
        "Q": "0",
        "I": "1",
        "l": "1",
        "L": "1",
        "|": "1",
        "S": "5",
        "s": "5",
        "B": "8",
        "Z": "2",
        "z": "2",
        "G": "6",
        "g": "6",
    }
)


def map_lookalikes_to_digits(text: str | None) -> str:
    """Replace common OCR glyph confusions (O->0, l->1, S->5, B->8, etc.) with digits."""
    if not text:
        return ""
    return text.translate(LOOKALIKE_DIGIT_MAP)


def normalize_name(text: str | None) -> list[str]:
    """Normalize personal/entity names into lowercase ASCII word tokens."""
    if not text:
        return []
    nfkd = unicodedata.normalize("NFKD", text)
    ascii_only = "".join(ch for ch in nfkd if not unicodedata.combining(ch)).lower()
    cleaned = re.sub(r"[\r\n]+", " ", ascii_only)
    cleaned = re.sub(r"[^a-z\s]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return [tok for tok in cleaned.split(" ") if tok]


def name_similarity(tokens_a: list[str], tokens_b: list[str]) -> float:
    """Compute Jaccard token overlap similarity in [0.0, 1.0]."""
    if not tokens_a or not tokens_b:
        return 0.0
    set_a, set_b = set(tokens_a), set(tokens_b)
    return len(set_a & set_b) / (len(set_a | set_b) or 1)


def edit_distance(a: str, b: str) -> int:
    """Compute Levenshtein edit distance between two strings."""
    la, lb = len(a), len(b)
    dp = list(range(lb + 1))
    for i in range(1, la + 1):
        prev = dp[0]
        dp[0] = i
        for j in range(1, lb + 1):
            temp = dp[j]
            cost = 0 if a[i - 1] == b[j - 1] else 1
            dp[j] = min(dp[j] + 1, dp[j - 1] + 1, prev + cost)
            prev = temp
    return dp[lb]


def id_in_text_fuzzy(worker_id: str | None, ocr_text: str | None) -> bool:
    """Detect whether a worker national ID appears in noisy OCR text."""
    if not worker_id or not ocr_text:
        return False

    target_digits = re.sub(r"[^0-9]", "", str(worker_id))
    if not target_digits:
        return False

    mapped = map_lookalikes_to_digits(ocr_text)
    digits_only = re.sub(r"[^0-9]", "", mapped)
    if target_digits in digits_only:
        return True

    tokens = re.findall(r"\d{7,11}", re.sub(r"[^0-9]", " ", mapped))
    for tok in tokens:
        if len(tok) == len(target_digits) and edit_distance(tok, target_digits) <= 1:
            return True
        if len(target_digits) >= 7 and len(tok) >= 7 and target_digits[-7:] == tok[-7:]:
            return True
    return False


def extract_candidate_worker_ids(text: str | None) -> list[str]:
    """Extract plausible 8-10 digit worker IDs using surrounding semantic context."""
    if not text:
        return []

    mapped = map_lookalikes_to_digits(text)
    lowered = text.lower()
    matches = re.finditer(r"(?<!\d)(\d{8,10})(?!\d)", mapped)

    id_marker_re = re.compile(r"\b(?:cc|c\.c\.?|c[eé]dula|worker\s*id|id)\b", re.IGNORECASE)
    exclude_ctx = (
        "nit",
        " n.i",
        " n i",
        "tax id",
        " dv",
        "planilla",
        "numero de planilla",
        "número de planilla",
    )

    seen: set[str] = set()
    candidates: list[str] = []
    for match in matches:
        cand = match.group(1)
        start, end = match.start(1), match.end(1)
        # Restrict context to the same delimited cell/segment when pipes or newlines exist
        left_raw = lowered[max(0, start - 35) : start]
        right_raw = lowered[end : min(len(lowered), end + 35)]
        local_left = re.split(r"[|\r\n]", left_raw)[-1]
        local_right = re.split(r"[|\r\n]", right_raw)[0]
        local_segment = f"{local_left}{cand}{local_right}"
        immediate_prefix = lowered[max(0, start - 8) : start]

        if not id_marker_re.search(local_segment):
            continue
        if "$" in local_segment or any(tok in local_segment for tok in exclude_ctx):
            continue
        if any(tok in immediate_prefix for tok in ("nit", "n.i", "n i", "tax")):
            continue
        if cand not in seen:
            seen.add(cand)
            candidates.append(cand)
    return candidates


def match_by_keywords(
    required_keywords: list[str],
    exclude_keywords: list[str],
    filename: str,
    text: str,
) -> bool:
    """Return True if document filename or text satisfies required/excluded terms."""
    fn_l = filename.lower()
    txt_l = text.lower()
    has_required = any(k.lower() in fn_l or k.lower() in txt_l for k in required_keywords)
    has_excluded = any(k.lower() in fn_l or k.lower() in txt_l for k in exclude_keywords)
    return has_required and not has_excluded
