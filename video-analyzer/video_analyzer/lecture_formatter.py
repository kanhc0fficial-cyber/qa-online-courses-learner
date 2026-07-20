"""Validated, cacheable orchestration for structured lecture formatting."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

from .lecture_document import (
    bind_group_frames,
    ensure_callout_kinds,
    ensure_document_text_retention,
    ensure_minimum_callouts,
    limit_callouts,
    limit_group_callouts,
    normalize_group_blocks,
    restore_missing_source_number_sentences,
    split_lecture_draft,
    validate_lecture_document,
    validate_lecture_group,
)


FORMAT_VERSION = "lecture-article-json-v3"
CALLOUT_KIND_CYCLE = ["definition", "derivation", "conclusion", "warning"]


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _source_hash(excerpt: str) -> str:
    return sha256(excerpt.encode("utf-8")).hexdigest()


def _load_cache(path: Path, excerpt: str) -> Dict[str, Any] | None:
    if not path.exists():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        return None
    if value.get("format_version") != FORMAT_VERSION:
        return None
    if value.get("source_sha256") != _source_hash(excerpt):
        return None
    group = value.get("group")
    return group if isinstance(group, dict) else None


def _write_cache(path: Path, excerpt: str, group: Dict[str, Any]) -> None:
    _write_json(path, {
        "format_version": FORMAT_VERSION,
        "source_sha256": _source_hash(excerpt),
        "group": group,
    })


def format_lecture_document(
    analyzer: Any,
    draft: str,
    expected_frames: Sequence[int],
    artifact_dir: Path,
    *,
    group_size: int = 1,
    supplemental_excerpts: Dict[int, str] | None = None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Format independent source units, repair locally, then validate globally."""
    title, groups = split_lecture_draft(draft, group_size=group_size)
    actual_frames = [frame for _, frames in groups for frame in frames]
    supplements = supplemental_excerpts or {}
    unexpected = [frame for frame in actual_frames if frame not in expected_frames]
    if unexpected:
        raise ValueError(f"Draft contains unexpected frames {unexpected}")
    group_by_frame = {}
    for excerpt, frames in groups:
        if len(frames) == 1 and frames[0] not in group_by_frame:
            group_by_frame[frames[0]] = (excerpt, frames)
    missing = [frame for frame in expected_frames if frame not in group_by_frame]
    unavailable = [frame for frame in missing if frame not in supplements]
    if unavailable:
        raise ValueError(f"Draft frames {unavailable} have no supplemental source excerpt")
    ordered_groups = []
    for frame in expected_frames:
        ordered_groups.append(
            (supplements[frame], [frame])
            if frame in supplements else group_by_frame[frame]
        )
    groups = ordered_groups
    validation_source = draft

    sections: List[Dict[str, Any]] = []
    group_validations: List[Dict[str, Any]] = []
    for index, (excerpt, frames) in enumerate(groups):
        cache_path = artifact_dir / f"article.frame_{frames[0]:03d}.cache.json"
        candidate_path = artifact_dir / f"article.frame_{frames[0]:03d}.candidate.json"
        repair_candidate_path = artifact_dir / f"article.frame_{frames[0]:03d}.repair.candidate.json"
        numeric_repair_candidate_path = (
            artifact_dir / f"article.frame_{frames[0]:03d}.numeric_repair.candidate.json"
        )
        group = _load_cache(cache_path, excerpt)
        if group is None:
            group = _load_cache(repair_candidate_path, excerpt)
        if group is None:
            group = _load_cache(candidate_path, excerpt)
        if group is None:
            group = analyzer.format_lecture_article_group(
                excerpt, frames,
                [CALLOUT_KIND_CYCLE[index % len(CALLOUT_KIND_CYCLE)]],
            )
            if isinstance(group, dict):
                _write_cache(candidate_path, excerpt, group)
        if isinstance(group, dict):
            group = normalize_group_blocks(group)
            group = bind_group_frames(group, frames)
            group = limit_group_callouts(group, maximum=1)
        is_supplement = frames[0] in supplements
        minimum_text_ratio = 0.17 if is_supplement else 0.55
        group_validation = validate_lecture_group(
            group, frames, excerpt,
            minimum_text_ratio=minimum_text_ratio,
            require_source_numbers=is_supplement,
        )
        if not group_validation["valid"] and isinstance(group, dict):
            repaired = analyzer.repair_lecture_article_group(
                excerpt, group, group_validation["errors"]
            )
            if isinstance(repaired, dict):
                _write_cache(repair_candidate_path, excerpt, repaired)
            if isinstance(repaired, dict):
                repaired = normalize_group_blocks(repaired)
                repaired = bind_group_frames(repaired, frames)
                repaired = limit_group_callouts(repaired, maximum=1)
            repaired_validation = validate_lecture_group(
                repaired, frames, excerpt,
                minimum_text_ratio=minimum_text_ratio,
                require_source_numbers=is_supplement,
            )
            if (
                isinstance(repaired, dict)
                and any(
                    error.startswith("group_missing_source_numbers_")
                    for error in repaired_validation["errors"]
                )
            ):
                repaired = restore_missing_source_number_sentences(
                    repaired, excerpt, repaired_validation["errors"]
                )
                _write_cache(numeric_repair_candidate_path, excerpt, repaired)
                repaired_validation = validate_lecture_group(
                    repaired, frames, excerpt,
                    minimum_text_ratio=minimum_text_ratio,
                    require_source_numbers=is_supplement,
                )
            if repaired_validation["valid"]:
                group, group_validation = repaired, repaired_validation
        group_validations.append(group_validation)
        if not group_validation["valid"]:
            _write_json(artifact_dir / f"article.group_{index + 1}.rejected.json", group or {})
            validation = {"valid": False, "group_validations": group_validations}
            _write_json(artifact_dir / "article.document.validation.json", validation)
            raise ValueError(
                f"Structured article group {index + 1} failed: "
                + ", ".join(group_validation["errors"])
            )
        _write_cache(cache_path, excerpt, group)
        sections.extend(group["sections"])

    document = {"title": title, "lead": [], "sections": sections}
    document = ensure_minimum_callouts(document, minimum=8)
    document = ensure_callout_kinds(document)
    document = limit_callouts(document, maximum=12)
    validation = validate_lecture_document(document, expected_frames, validation_source)
    if any(
        error.startswith("structured_text_too_short_")
        for error in validation["errors"]
    ):
        document = ensure_document_text_retention(
            document, validation_source, validation["errors"]
        )
        _write_json(
            artifact_dir / "article.document.retention_repair.candidate.json",
            document,
        )
        validation = validate_lecture_document(
            document, expected_frames, validation_source
        )
    validation["group_validations"] = group_validations
    _write_json(artifact_dir / "article.document.validation.json", validation)
    if not validation["valid"]:
        _write_json(artifact_dir / "article.document.rejected.json", document)
        raise ValueError(
            "Structured article validation failed: " + ", ".join(validation["errors"])
        )
    return document, validation
