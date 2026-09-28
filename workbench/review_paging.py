"""Lossless, finite pages for presenting a canonical review pack to a model.

This module deliberately does not call a model, worker, or store.  The input
pack is the ledger projection owned by the caller; returned pages are views
and must not be treated as a completed review.
"""
from copy import deepcopy
import hashlib
import json


PAGE_VERSION = "review-page-v1"


class ProjectionTooLarge(ValueError):
    """A single source record or page envelope cannot fit the requested bound."""


def _budget_exception_types():
    """Resolve repository typed budget errors without importing eagerly."""
    try:
        from . import review_context
        return tuple(cls for cls in (
            getattr(review_context, "InputBudgetExceeded", None),
            getattr(review_context, "InputBudgetError", None),
        ) if cls is not None)
    except ImportError:
        return ()


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value):
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _source_identity(observation, shared=None):
    """Return the source identity without normalizing or truncating it."""
    fields = observation.get("fields", {})
    keys = (
        "source_sha256", "source_location", "source_offset", "source_range_start",
        "image_file_byte_offset", "byte_offset", "byte_length",
        "partition_offset", "volume_id", "snapshot_id", "os_instance",
        "path", "inode", "line", "timestamp", "timestamp_ns", "event_time_ns",
        "nanosecond", "time_record", "time_basis", "time_kind", "time_type",
        "time_semantics", "kind_basis", "timezone_basis", "source_origin", "locator_basis",
    )
    shared = shared or {}
    def value(key):
        if key in fields:
            return fields[key]
        if key in observation:
            return observation[key]
        # review_context.share_metadata uses slash-delimited paths. Resolve
        # only for comparison; the page itself remains exactly as fit_fn left
        # it and can be expanded by the normal consumer.
        for path, default in shared.items():
            if path.split("/")[-1] == key:
                return default
        return None
    return (observation.get("id"), tuple((key, value(key)) for key in keys))


def _fit(page, maximum, fit_fn):
    candidate = deepcopy(page)
    def expanded(value):
        result = deepcopy(value)
        from .structured_context import expand as expand_structured
        from .review_context import expand_metadata
        expand_structured(result)
        expand_metadata(result)
        return result
    before_pack = expanded(candidate)
    before = [_source_identity(row, before_pack.get("shared_observation_metadata"))
              for row in before_pack.get("observations", [])]
    if fit_fn is not None:
        try:
            fit_fn(candidate, maximum)
        except Exception as exc:
            budget_types = _budget_exception_types()
            if not budget_types or not isinstance(exc, budget_types):
                raise
            return None
    after_pack = expanded(candidate)
    after = [_source_identity(row, after_pack.get("shared_observation_metadata"))
             for row in after_pack.get("observations", [])]
    if before != after:
        raise ProjectionTooLarge("fit_fn changed source identity or observation membership")
    if len(_json(candidate)) > maximum:
        return None
    return candidate


def _project(pack, observations, index, count, canonical_digest):
    rows = deepcopy(observations)
    ids = [row.get("id") for row in rows]
    id_set = set(ids)
    all_ids = [row.get("id") for row in pack.get("observations", [])]
    omitted_ids = [oid for oid in all_ids if oid not in id_set]

    dossiers = []
    for dossier in pack.get("required_dossiers", []):
        item = deepcopy(dossier)
        source_ids = list(dossier.get("observation_ids", []))
        item["observation_ids"] = [oid for oid in source_ids if oid in id_set]
        item["omitted_observation_count"] = sum(oid not in id_set for oid in source_ids)
        item["paginated_scope"] = True
        dossiers.append(item)

    checks = []
    omitted_check_count = 0
    check_owners = set()
    for check in pack.get("executed_checks", []):
        item = deepcopy(check)
        source_ids = list(check.get("observation_ids", []))
        present_ids = [oid for oid in source_ids if oid in id_set]
        # Empty checks still have logical outcomes. Present all once, not just
        # the first one and not once per source page.
        include = bool(present_ids) or (not source_ids and index == 0)
        if not include:
            omitted_check_count += 1
            continue
        for contract in check.get("contracts", []):
            if contract.get("dossier_id"):
                check_owners.add(contract["dossier_id"])
        item["observation_ids"] = present_ids
        item["omitted_observation_count"] = sum(oid not in id_set for oid in source_ids)
        item["paginated_scope"] = True
        item["assessment_complete"] = False
        checks.append(item)

    allowed = {}
    for dossier_id, source_ids in pack.get("allowed_observation_ids_by_dossier", {}).items():
        present = [oid for oid in source_ids if oid in id_set]
        if present or dossier_id in check_owners:
            allowed[dossier_id] = present

    candidates = []
    for candidate in pack.get("literal_fact_candidates", []):
        oid = candidate.get("observation_id", candidate.get("id")) if isinstance(candidate, dict) else None
        if oid in id_set:
            candidates.append(deepcopy(candidate))

    page = {
        "page_version": PAGE_VERSION,
        "canonical_input_sha256": canonical_digest,
        "page_index": index,
        "page_count": count,
        "observations": rows,
        "required_dossiers": dossiers,
        "executed_checks": checks,
        "executed_checks_total": len(pack.get("executed_checks", [])),
        "omitted_check_count": omitted_check_count,
        "check_ledger_manifest": {"canonical_input_sha256": canonical_digest,
                                   "retrieval": "canonical_pack"},
        "deferred_checks": deepcopy(pack.get("deferred_checks", [])),
        "previous_assessment": deepcopy(pack.get("previous_assessment")),
        # Preserve routing/contract semantics explicitly; do not make a page
        # look like a generic observation dump.
        "review_mode": pack.get("review_mode"),
        "final_pass": pack.get("final_pass", False),
        "target_os": pack.get("target_os"),
        "available_tools": deepcopy(pack.get("available_tools", [])),
        "citation_contract": pack.get("citation_contract"),
        "validation_feedback": deepcopy(pack.get("validation_feedback")),
        "selection_is_partial": pack.get("selection_is_partial"),
        "allowed_observation_ids": ids,
        "allowed_observation_ids_by_dossier": allowed,
        "shared_check_observation_ids": [oid for oid in pack.get("shared_check_observation_ids", []) if oid in id_set],
        "literal_fact_candidates": candidates,
        "omitted_observation_count": len(omitted_ids),
        "omitted_observation_manifest": {
            "canonical_input_sha256": canonical_digest,
            "retrieval": "canonical_pack",
            "scope": "Complete omitted IDs remain in the canonical ledger; this model view carries count only.",
        },
        "paginated_scope": True,
        "coverage_complete": not omitted_ids and not omitted_check_count and all(
            item["omitted_observation_count"] == 0 for item in dossiers
        ),
        "finalization_allowed": False,
        "finalization_scope": "A page is a source view only; no final review may be inferred from it.",
    }
    page["page_id"] = hashlib.sha256(
        (canonical_digest + ":" + str(index) + ":" + ",".join(str(oid) for oid in ids)).encode("utf-8")
    ).hexdigest()
    return page


def _refresh_finalization(page, canonical_count):
    """Recompute the safety gate after presentation-only fitting."""
    present = len(page.get("observations", []))
    complete_dossiers = all(item.get("omitted_observation_count", 0) == 0
                            for item in page.get("required_dossiers", []))
    page["omitted_observation_count"] = max(
        page.get("omitted_observation_count", 0), canonical_count - present
    )
    page["coverage_complete"] = (
        present == canonical_count and page["omitted_observation_count"] == 0 and complete_dossiers
        and page.get("omitted_check_count", 0) == 0
    )
    # Coverage completeness is not review completion. Final acceptance belongs
    # to the caller after all page-level assessments and ledger validation.
    page["finalization_allowed"] = False
    page["finalization_scope"] = (
        "A page is a source view only; coverage_complete does not authorize final review."
    )


def build_pages(pack, maximum=36000, fit_fn=None):
    """Build deterministic, bounded pages without changing ``pack``.

    ``fit_fn`` may perform the repository's presentation-only compaction on a
    trial page. It must preserve observation IDs and source identity. Pages are
    selected greedily with binary search over the canonical observation order.
    """
    if maximum <= 0:
        raise ValueError("maximum must be positive")
    canonical = deepcopy(pack)
    canonical_digest = _digest(canonical)
    observations = canonical.get("observations", [])
    if not observations:
        page = _project(canonical, [], 0, 1, canonical_digest)
        fitted = _fit(page, maximum, fit_fn)
        if fitted is None:
            raise ProjectionTooLarge("empty page envelope exceeds maximum")
        _refresh_finalization(fitted, 0)
        return [fitted]

    spans = []
    start = 0
    while start < len(observations):
        low, high = start + 1, len(observations)
        best = None
        while low <= high:
            end = (low + high) // 2
            # Use the largest plausible page-count width while searching so
            # final page numbering cannot make an otherwise fitting page grow
            # over the bound.
            trial = _project(canonical, observations[start:end], 0 if start == 0 else len(observations),
                             len(observations), canonical_digest)
            fitted = _fit(trial, maximum, fit_fn)
            if fitted is None:
                high = end - 1
            else:
                best = end
                low = end + 1
        if best is None:
            raise ProjectionTooLarge(
                f"single observation at index {start} exceeds maximum {maximum}"
            )
        spans.append((start, best))
        start = best

    pages = []
    count = len(spans)
    for index, (start, end) in enumerate(spans):
        page = _project(canonical, observations[start:end], index, count, canonical_digest)
        fitted = _fit(page, maximum, fit_fn)
        if fitted is None:
            raise ProjectionTooLarge(f"page {index} exceeds maximum {maximum}")
        _refresh_finalization(fitted, len(observations))
        pages.append(fitted)
    return pages
