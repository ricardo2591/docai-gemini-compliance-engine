"""OCR extraction cascade and fuzzy document matching utilities."""

from compliance_engine.ocr.cascade import (
    CompositeOcrCascade,
    GoogleCloudOcrBackend,
    LocalPdfTextBackend,
    OcrExtractionResult,
    decide_scope_from_pages,
)
from compliance_engine.ocr.fuzzy_match import (
    edit_distance,
    extract_candidate_worker_ids,
    id_in_text_fuzzy,
    map_lookalikes_to_digits,
    match_by_keywords,
    name_similarity,
    normalize_name,
)

__all__ = [
    "CompositeOcrCascade",
    "GoogleCloudOcrBackend",
    "LocalPdfTextBackend",
    "OcrExtractionResult",
    "decide_scope_from_pages",
    "edit_distance",
    "extract_candidate_worker_ids",
    "id_in_text_fuzzy",
    "map_lookalikes_to_digits",
    "match_by_keywords",
    "name_similarity",
    "normalize_name",
]
