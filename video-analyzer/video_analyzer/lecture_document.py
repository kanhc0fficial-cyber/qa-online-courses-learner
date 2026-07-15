"""Schema validation and generic Markdown rendering for lecture articles."""

from __future__ import annotations

from typing import Any, Dict, List, Sequence, Tuple
import re


CALLOUT_LABELS = {
    "definition": ("#2563EB", "核心定义"),
    "derivation": ("#D97706", "推导要点"),
    "conclusion": ("#059669", "得到结论"),
    "warning": ("#DC2626", "注意"),
}
ALLOWED_BLOCKS = {"paragraph", "subheading", "callout", "equation", "table", "frame"}
ORAL_FILLER_PATTERN = re.compile(
    r"那么|其实|我们来看(?:一下)?|大家(?:要|需要)|不难看出|啊"
)
GENERIC_HEADING_PATTERN = re.compile(
    r"^(?:(?:第[一二三四五六七八九十百0-9]+章|课程|本章|全文|章节|内容|要点|重点)\s*)?"
    r"(?:报告|摘要|总结|小结)(?:$|[:：])"
)
BLOCK_KEYS = {
    "paragraph": {"type", "text"},
    "subheading": {"type", "text"},
    "callout": {"type", "kind", "text"},
    "equation": {"type", "text"},
    "table": {"type", "headers", "rows"},
    "frame": {"type", "frame_number"},
}


def document_text(document: Dict[str, Any]) -> str:
    values: List[str] = [str(document.get("title", ""))]
    values.extend(str(item) for item in document.get("lead", []) if isinstance(item, str))
    for section in document.get("sections", []) if isinstance(document.get("sections"), list) else []:
        if not isinstance(section, dict):
            continue
        values.append(str(section.get("heading", "")))
        for block in section.get("blocks", []) if isinstance(section.get("blocks"), list) else []:
            if not isinstance(block, dict):
                continue
            values.append(str(block.get("text", "")))
            values.extend(str(value) for value in block.get("headers", []) if isinstance(value, str))
            for row in block.get("rows", []) if isinstance(block.get("rows"), list) else []:
                if isinstance(row, list):
                    values.extend(str(value) for value in row)
    return "\n".join(value for value in values if value)


def validate_lecture_document(
    document: Any,
    expected_frames: Sequence[int],
    original_text: str,
) -> Dict[str, Any]:
    errors: List[str] = []
    if not isinstance(document, dict):
        return {"valid": False, "errors": ["document_not_object"]}
    title = document.get("title")
    lead = document.get("lead")
    sections = document.get("sections")
    if not isinstance(title, str) or not title.strip():
        errors.append("missing_title")
    elif GENERIC_HEADING_PATTERN.search(title.strip()):
        errors.append("forbidden_generic_title")
    if not isinstance(lead, list) or not all(isinstance(item, str) for item in lead):
        errors.append("lead_must_be_string_array")
    if not isinstance(sections, list):
        return {"valid": False, "errors": errors + ["sections_must_be_array"]}
    if len(sections) < 7:
        errors.append(f"need_at_least_7_sections_got_{len(sections)}")

    frame_numbers: List[int] = []
    callout_kinds: List[str] = []
    subheading_count = 0
    paragraph_count = 0
    table_count = 0
    for section_index, section in enumerate(sections):
        if not isinstance(section, dict):
            errors.append(f"section_{section_index}_not_object")
            continue
        if not isinstance(section.get("heading"), str) or not section["heading"].strip():
            errors.append(f"section_{section_index}_missing_heading")
        elif GENERIC_HEADING_PATTERN.search(section["heading"].strip()):
            errors.append(f"section_{section_index}_forbidden_generic_heading")
        unknown_section_keys = set(section) - {"heading", "blocks"}
        if unknown_section_keys:
            errors.append(
                f"section_{section_index}_unknown_keys_" + "_".join(sorted(unknown_section_keys))
            )
        blocks = section.get("blocks")
        if not isinstance(blocks, list):
            errors.append(f"section_{section_index}_blocks_not_array")
            continue
        for block_index, block in enumerate(blocks):
            if not isinstance(block, dict):
                errors.append(f"section_{section_index}_block_{block_index}_not_object")
                continue
            block_type = block.get("type")
            if block_type not in ALLOWED_BLOCKS:
                errors.append(f"unknown_block_type_{block_type}")
                continue
            unknown_block_keys = set(block) - BLOCK_KEYS[block_type]
            if unknown_block_keys:
                errors.append(
                    f"section_{section_index}_block_{block_index}_unknown_keys_"
                    + "_".join(sorted(unknown_block_keys))
                )
            if block_type in {"paragraph", "subheading", "callout", "equation"}:
                if not isinstance(block.get("text"), str) or not block["text"].strip():
                    errors.append(f"empty_{block_type}_block")
            if (
                block_type == "subheading"
                and isinstance(block.get("text"), str)
                and GENERIC_HEADING_PATTERN.search(block["text"].strip())
            ):
                errors.append(
                    f"section_{section_index}_block_{block_index}_forbidden_generic_subheading"
                )
            if block_type == "paragraph":
                paragraph_count += 1
            elif block_type == "subheading":
                subheading_count += 1
            elif block_type == "callout":
                kind = block.get("kind")
                if kind not in CALLOUT_LABELS:
                    errors.append(f"invalid_callout_kind_{kind}")
                else:
                    callout_kinds.append(kind)
            elif block_type == "frame":
                number = block.get("frame_number")
                if not isinstance(number, int):
                    errors.append("frame_number_not_integer")
                else:
                    frame_numbers.append(number)
            elif block_type == "table":
                headers, rows = block.get("headers"), block.get("rows")
                if not isinstance(headers, list) or len(headers) < 2 or not all(isinstance(value, str) for value in headers):
                    errors.append("invalid_table_headers")
                elif not isinstance(rows, list) or not rows or not all(
                    isinstance(row, list) and len(row) == len(headers) for row in rows
                ):
                    errors.append("invalid_table_rows")
                else:
                    table_count += 1

    if subheading_count < 4:
        errors.append(f"need_at_least_4_subheadings_got_{subheading_count}")
    if not 8 <= len(callout_kinds) <= 12:
        errors.append(f"need_8_to_12_callouts_got_{len(callout_kinds)}")
    missing_kinds = sorted(set(CALLOUT_LABELS) - set(callout_kinds))
    if missing_kinds:
        errors.append("missing_callout_kinds_" + "_".join(missing_kinds))
    if frame_numbers != list(expected_frames):
        errors.append(f"frame_sequence_must_be_{list(expected_frames)}_got_{frame_numbers}")

    text = document_text(document)
    original_plain = re.sub(r"<!--.*?-->|[#>*`|\-]", "", original_text, flags=re.DOTALL)
    # Short lectures should be judged primarily by source retention. A fixed
    # 2800-character floor rejected an 88%-retained 2292-character source.
    minimum_length = max(1800, round(len(original_plain) * 0.88))
    if len(text) < minimum_length:
        errors.append(f"structured_text_too_short_{len(text)}_minimum_{minimum_length}")
    bold_pairs = text.count("**") // 2
    if bold_pairs < 15:
        errors.append(f"need_at_least_15_bold_items_got_{bold_pairs}")
    oral_fillers = len(ORAL_FILLER_PATTERN.findall(text))
    if oral_fillers > 4:
        errors.append(f"too_many_oral_fillers_{oral_fillers}_maximum_4")

    return {
        "valid": not errors,
        "errors": errors,
        "metrics": {
            "sections": len(sections),
            "subheadings": subheading_count,
            "paragraphs": paragraph_count,
            "callouts": len(callout_kinds),
            "tables": table_count,
            "frames": frame_numbers,
            "text_characters": len(text),
            "minimum_text_characters": minimum_length,
            "bold_pairs": bold_pairs,
            "oral_fillers": oral_fillers,
        },
    }


def split_lecture_draft(draft: str, group_size: int = 3) -> Tuple[str, List[Tuple[str, List[int]]]]:
    title_match = re.search(r"(?m)^#\s+(.+)$", draft)
    title = title_match.group(1).strip() if title_match else "课程记录"
    without_title = re.sub(r"(?m)^#\s+.+\r?\n?", "", draft, count=1).strip()
    marker = re.compile(r"<!--\s*FRAME\s*:\s*(\d+)\s*-->", re.IGNORECASE)
    matches = list(marker.finditer(without_title))
    if not matches:
        raise ValueError("Lecture draft contains no frame markers")
    prefix = without_title[:matches[0].start()].strip()
    units: List[Tuple[str, int]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(without_title)
        text = without_title[match.start():end].strip()
        if index == 0 and prefix:
            text = prefix + "\n\n" + text
        units.append((text, int(match.group(1))))
    groups = []
    for start in range(0, len(units), max(1, group_size)):
        chunk = units[start:start + max(1, group_size)]
        groups.append(("\n\n".join(item[0] for item in chunk), [item[1] for item in chunk]))
    return title, groups


def lecture_frame_markers(draft: str) -> List[int]:
    return [
        int(value)
        for value in re.findall(r"<!--\s*FRAME\s*:\s*(\d+)\s*-->", draft, re.IGNORECASE)
    ]


def validate_lecture_draft_frames(draft: str, expected_frames: Sequence[int]) -> Dict[str, Any]:
    actual = lecture_frame_markers(draft)
    expected = list(expected_frames)
    missing = [number for number in expected if number not in actual]
    unexpected = [number for number in actual if number not in expected]
    duplicates = sorted({number for number in actual if actual.count(number) > 1})
    return {
        "valid": actual == expected,
        "expected": expected,
        "actual": actual,
        "missing": missing,
        "unexpected": unexpected,
        "duplicates": duplicates,
    }


def can_recover_lecture_draft_frames(validation: Dict[str, Any]) -> bool:
    """Allow code-owned ordering and source-backed gap repair for known frames only."""
    return not validation.get("unexpected") and not validation.get("duplicates")


def thin_lecture_draft_frames(draft: str, minimum_characters: int = 20) -> List[int]:
    _, groups = split_lecture_draft(draft, group_size=1)
    thin = []
    for excerpt, frames in groups:
        plain = re.sub(
            r"<!--.*?-->|[#>*`|\-]|\s+", "", excerpt, flags=re.DOTALL
        )
        if len(frames) == 1 and len(plain) < minimum_characters:
            thin.append(frames[0])
    return thin


def validate_lecture_group(
    group: Any,
    expected_frames: Sequence[int],
    original_excerpt: str,
    minimum_text_ratio: float = 0.55,
    require_source_numbers: bool = False,
) -> Dict[str, Any]:
    if not isinstance(group, dict) or not isinstance(group.get("sections"), list):
        return {"valid": False, "errors": ["group_sections_missing"]}
    temporary = {"title": "group", "lead": [], "sections": group["sections"]}
    base = validate_lecture_document(temporary, expected_frames, original_excerpt)
    ignored_prefixes = (
        "need_at_least_7_sections_", "need_at_least_4_subheadings_",
        "need_8_to_12_callouts_", "missing_callout_kinds_",
        "structured_text_too_short_", "need_at_least_15_bold_items_",
        "too_many_oral_fillers_",
    )
    errors = [error for error in base.get("errors", []) if not error.startswith(ignored_prefixes)]
    metrics = base.get("metrics", {})
    minimum_sections = max(1, len(expected_frames))
    if metrics.get("sections", 0) < minimum_sections:
        errors.append(f"group_needs_{minimum_sections}_sections")
    # Subheadings are a document-level requirement. Requiring one on every
    # single-slide unit produces mechanical hierarchy on thin transition pages.
    if metrics.get("callouts", 0) > max(2, len(expected_frames)):
        errors.append("group_has_too_many_callouts")
    if metrics.get("oral_fillers", 0) > 2:
        errors.append(
            f"group_has_too_many_oral_fillers_{metrics.get('oral_fillers')}_maximum_2"
        )
    plain_excerpt = re.sub(r"<!--.*?-->|[#>*`|\-]", "", original_excerpt, flags=re.DOTALL)
    # Local excerpts may contain dense oral filler. A 55% floor permits genuine
    # written-language cleanup; the 88% whole-document floor still prevents
    # cumulative summarization across the lecture.
    minimum_length = max(35, round(len(plain_excerpt) * minimum_text_ratio))
    if metrics.get("text_characters", 0) < minimum_length:
        errors.append(
            f"group_text_too_short_{metrics.get('text_characters', 0)}_minimum_{minimum_length}"
        )
    if require_source_numbers:
        without_timestamps = re.sub(r"\[\d+(?:\.\d+)?-\d+(?:\.\d+)?\]", "", original_excerpt)
        without_timestamps = re.sub(r"<!--.*?-->", "", without_timestamps, flags=re.DOTALL)
        # Ignore digits that begin inside identifiers/ASR tokens (a21, I2, L1),
        # while retaining values followed by unit symbols such as 100V.
        number_pattern = r"(?<![A-Za-z0-9_])\d+(?:\.\d+)?"
        source_numbers = {
            value for value in re.findall(number_pattern, without_timestamps)
            if float(value) >= 10 or "." in value
        }
        output_numbers = set(re.findall(number_pattern, document_text(temporary)))
        missing_numbers = sorted(source_numbers - output_numbers, key=lambda value: float(value))
        if missing_numbers:
            errors.append("group_missing_source_numbers_" + "_".join(missing_numbers))
    return {"valid": not errors, "errors": errors, "metrics": metrics}


def normalize_group_blocks(group: Dict[str, Any]) -> Dict[str, Any]:
    """Flatten model-created nested blocks into the renderer's flat block schema."""
    for section in group.get("sections", []):
        if not isinstance(section, dict) or not isinstance(section.get("blocks"), list):
            continue
        flattened: List[Any] = []

        def append_block(block: Any) -> None:
            if not isinstance(block, dict):
                flattened.append(block)
                return
            clean = dict(block)
            nested = clean.pop("blocks", None)
            flattened.append(clean)
            if isinstance(nested, list):
                for child in nested:
                    append_block(child)

        for block in section["blocks"]:
            append_block(block)
        section["blocks"] = flattened
    return group


def bind_group_frames(group: Dict[str, Any], frame_numbers: Sequence[int]) -> Dict[str, Any]:
    """Bind source-owned frame ids to model-created sections without semantic guessing."""
    sections = group.get("sections", []) if isinstance(group, dict) else []
    for section in sections:
        if isinstance(section, dict) and isinstance(section.get("blocks"), list):
            section["blocks"] = [
                block for block in section["blocks"]
                if not (isinstance(block, dict) and block.get("type") == "frame")
            ]
    if len(sections) < len(frame_numbers):
        return group
    for index, frame_number in enumerate(frame_numbers):
        blocks = sections[index].setdefault("blocks", [])
        insertion = 1 if blocks and blocks[0].get("type") == "subheading" else 0
        blocks.insert(insertion, {"type": "frame", "frame_number": int(frame_number)})
    return group


def limit_callouts(document: Dict[str, Any], maximum: int = 12) -> Dict[str, Any]:
    """Keep callout text while enforcing a stable, document-wide color budget."""
    references = []
    for section in document.get("sections", []):
        for block in section.get("blocks", []):
            if isinstance(block, dict) and block.get("type") == "callout":
                references.append(block)
    if len(references) <= maximum:
        return document

    keep = set()
    for kind in CALLOUT_LABELS:
        for index, block in enumerate(references):
            if block.get("kind") == kind:
                keep.add(index)
                break
    remaining = [index for index in range(len(references)) if index not in keep]
    slots = max(0, maximum - len(keep))
    if slots and remaining:
        if slots >= len(remaining):
            keep.update(remaining)
        elif slots == 1:
            keep.add(remaining[len(remaining) // 2])
        else:
            for position in range(slots):
                offset = round(position * (len(remaining) - 1) / (slots - 1))
                keep.add(remaining[offset])
    for index, block in enumerate(references):
        if index not in keep:
            block["type"] = "paragraph"
            block.pop("kind", None)
    return document


def ensure_minimum_callouts(document: Dict[str, Any], minimum: int = 8) -> Dict[str, Any]:
    """Promote existing paragraphs to callouts without changing their text."""
    callouts = [
        block
        for section in document.get("sections", [])
        for block in section.get("blocks", [])
        if isinstance(block, dict) and block.get("type") == "callout"
    ]
    needed = max(0, minimum - len(callouts))
    if not needed:
        return document
    existing_kinds = {block.get("kind") for block in callouts}
    kind_order = [kind for kind in CALLOUT_LABELS if kind not in existing_kinds]
    kind_order.extend(kind for kind in CALLOUT_LABELS if kind in existing_kinds)
    candidates = []
    sections = document.get("sections", [])
    # Prefer chapters that currently have no callout, then fill from the rest.
    for prefer_empty in (True, False):
        for section in sections:
            blocks = section.get("blocks", []) if isinstance(section, dict) else []
            has_callout = any(
                isinstance(block, dict) and block.get("type") == "callout"
                for block in blocks
            )
            if has_callout == prefer_empty:
                continue
            paragraph = next((
                block for block in blocks
                if isinstance(block, dict) and block.get("type") == "paragraph"
                and block not in candidates
            ), None)
            if paragraph is not None:
                candidates.append(paragraph)
    for offset, block in enumerate(candidates[:needed]):
        block["type"] = "callout"
        block["kind"] = kind_order[offset % len(kind_order)]
    return document


def limit_group_callouts(group: Dict[str, Any], maximum: int = 1) -> Dict[str, Any]:
    references = []
    for section in group.get("sections", []):
        for block in section.get("blocks", []):
            if isinstance(block, dict) and block.get("type") == "callout":
                references.append(block)
    for block in references[maximum:]:
        block["type"] = "paragraph"
        block.pop("kind", None)
    return group


def _escape_table_cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", "<br>")


def render_lecture_document(document: Dict[str, Any]) -> str:
    lines: List[str] = [f"# {document['title'].strip()}", ""]
    for paragraph in document.get("lead", []):
        lines.extend([str(paragraph).strip(), ""])
    for section in document.get("sections", []):
        lines.extend([f"## {str(section['heading']).strip()}", ""])
        for block in section.get("blocks", []):
            block_type = block.get("type")
            if block_type == "paragraph":
                lines.extend([str(block["text"]).strip(), ""])
            elif block_type == "subheading":
                lines.extend([f"### {str(block['text']).strip()}", ""])
            elif block_type == "callout":
                color, label = CALLOUT_LABELS[block["kind"]]
                lines.extend([
                    f'> <span style="color:{color}"><strong>{label}</strong></span> {str(block["text"]).strip()}',
                    "",
                ])
            elif block_type == "equation":
                lines.extend([f"**{str(block['text']).strip()}**", ""])
            elif block_type == "frame":
                lines.extend([f"<!-- FRAME: {int(block['frame_number'])} -->", ""])
            elif block_type == "table":
                headers = [_escape_table_cell(value) for value in block["headers"]]
                lines.extend([
                    "| " + " | ".join(headers) + " |",
                    "| " + " | ".join("---" for _ in headers) + " |",
                ])
                for row in block["rows"]:
                    lines.append("| " + " | ".join(_escape_table_cell(value) for value in row) + " |")
                lines.append("")
    return "\n".join(lines).strip() + "\n"
