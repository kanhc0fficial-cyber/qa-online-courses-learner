import re

import pytest

from video_analyzer.lecture_document import (
    can_recover_lecture_draft_frames,
    document_text,
    ensure_callout_kinds, ensure_document_text_retention, ensure_minimum_callouts,
    restore_missing_source_number_sentences,
    lecture_frame_markers, limit_callouts, limit_group_callouts,
    normalize_group_blocks,
    render_lecture_document, split_lecture_draft,
    thin_lecture_draft_frames,
    validate_lecture_document, validate_lecture_group,
    validate_lecture_draft_frames,
)
from video_analyzer.lecture_formatter import format_lecture_document


def _valid_document():
    sections = []
    frames = iter(range(1, 10))
    kinds = ["definition", "derivation", "conclusion", "warning"] * 2
    for index in range(7):
        blocks = [
            {"type": "subheading", "text": f"步骤{index + 1}"},
            {"type": "paragraph", "text": ("**关键术语**与完整讲解。" * 35)},
            {"type": "callout", "kind": kinds[index], "text": "重要内容"},
        ]
        if index < 9:
            try:
                blocks.append({"type": "frame", "frame_number": next(frames)})
            except StopIteration:
                pass
        sections.append({"heading": f"主题{index + 1}", "blocks": blocks})
    sections[-1]["blocks"].extend([
        {"type": "callout", "kind": kinds[-1], "text": "重要内容"},
        {"type": "frame", "frame_number": 8},
        {"type": "frame", "frame_number": 9},
    ])
    return {"title": "课程", "lead": ["导语"], "sections": sections}


def test_structured_document_validates_and_renders():
    document = _valid_document()
    result = validate_lecture_document(document, list(range(1, 10)), "原稿" * 1000)
    assert result["valid"], result["errors"]
    markdown = render_lecture_document(document)
    assert len(re.findall(r"(?m)^## ", markdown)) == 7
    assert markdown.count('<span style="color:') == 8
    assert markdown.count("<!-- FRAME:") == 9


def test_structured_document_rejects_repeated_frame():
    document = _valid_document()
    document["sections"][-1]["blocks"][-1]["frame_number"] = 8
    result = validate_lecture_document(document, list(range(1, 10)), "原稿" * 1000)
    assert not result["valid"]
    assert any("frame_sequence" in error for error in result["errors"])


def test_short_lecture_uses_retention_ratio_instead_of_long_course_floor():
    document = _valid_document()
    short_source = "原稿" * 1146  # 2292 source characters, matching the P12 scale.
    result = validate_lecture_document(document, list(range(1, 10)), short_source)
    assert result["metrics"]["minimum_text_characters"] == 2017

    long_source = "原稿" * 2000
    result = validate_lecture_document(document, list(range(1, 10)), long_source)
    assert result["metrics"]["minimum_text_characters"] == 3520


def test_split_lecture_draft_groups_frames_without_reordering():
    draft = "# 标题\n\n开场\n\n<!-- FRAME: 1 -->\n甲\n<!-- FRAME: 2 -->\n乙\n<!-- FRAME: 3 -->\n丙\n<!-- FRAME: 4 -->\n丁"
    title, groups = split_lecture_draft(draft, group_size=3)
    assert title == "标题"
    assert [frames for _, frames in groups] == [[1, 2, 3], [4]]
    assert groups[0][0].startswith("开场")


def test_draft_frame_validation_reports_missing_duplicate_and_order():
    draft = "<!-- FRAME: 2 --><!-- FRAME: 2 --><!-- FRAME: 1 -->"
    assert lecture_frame_markers(draft) == [2, 2, 1]
    result = validate_lecture_draft_frames(draft, [1, 2, 3])
    assert not result["valid"]
    assert result["missing"] == [3]
    assert result["duplicates"] == [2]


def test_draft_frame_recovery_allows_reordering_and_missing_known_frames_only():
    reordered = validate_lecture_draft_frames(
        "<!-- FRAME: 3 --><!-- FRAME: 1 --><!-- FRAME: 4 -->",
        [1, 2, 3, 4],
    )
    assert reordered["missing"] == [2]
    assert can_recover_lecture_draft_frames(reordered)

    duplicate = validate_lecture_draft_frames(
        "<!-- FRAME: 1 --><!-- FRAME: 1 -->", [1, 2]
    )
    assert can_recover_lecture_draft_frames(duplicate)

    unexpected = validate_lecture_draft_frames(
        "<!-- FRAME: 1 --><!-- FRAME: 99 -->", [1, 2]
    )
    assert not can_recover_lecture_draft_frames(unexpected)


def test_thin_draft_frame_detection_catches_marker_without_content():
    draft = (
        "# 课程\n<!-- FRAME: 1 -->\n完整的教学内容超过二十个字符，包含公式条件和解释。"
        "\n<!-- FRAME: 2 -->\n\n<!-- FRAME: 3 -->\n短句"
    )
    assert thin_lecture_draft_frames(draft) == [2, 3]


def test_formatter_interleaves_hashed_supplement_for_missing_frame(tmp_path):
    class FakeAnalyzer:
        def __init__(self):
            self.frames = []

        def format_lecture_article_group(self, excerpt, frames, callout_kinds):
            self.frames.extend(frames)
            return {"sections": [{
                "heading": f"第{frames[0]}页",
                "blocks": [
                    {"type": "subheading", "text": f"知识点{frames[0]}"},
                    {"type": "paragraph", "text": "**关键内容**" * 60},
                    {"type": "callout", "kind": callout_kinds[0], "text": "课程重点"},
                ],
            }]}

        def repair_lecture_article_group(self, excerpt, group, errors):
            raise AssertionError(errors)

    draft = "# 课程\n\n" + "\n".join(
        f"<!-- FRAME: {number} -->\n原稿内容"
        for number in [4, 2, 9, 3, 8, 6, 5, 7]
    )
    analyzer = FakeAnalyzer()
    document, validation = format_lecture_document(
        analyzer,
        draft,
        list(range(1, 10)),
        tmp_path,
        supplemental_excerpts={1: "<!-- FRAME: 1 -->\n补充原稿内容"},
    )
    assert validation["valid"], validation["errors"]
    assert analyzer.frames == list(range(1, 10))
    assert document["sections"][0]["heading"] == "第1页"


def test_group_validator_uses_local_thresholds():
    document = _valid_document()
    group = {"sections": document["sections"][:3]}
    # Keep only the three frame blocks belonging to this local group.
    result = validate_lecture_group(group, [1, 2, 3], "原稿" * 500)
    assert result["valid"], result["errors"]


def test_group_validator_allows_oral_cleanup_under_global_coverage_guard():
    group = {"sections": [{
        "heading": "书面化",
        "blocks": [{"type": "paragraph", "text": "知识内容" * 28}],
    }]}
    result = validate_lecture_group(group, [], "原始口语" * 50)
    assert result["valid"], result["errors"]


def test_supplement_group_can_use_transcript_specific_coverage_ratio():
    group = {"sections": [{
        "heading": "暂态过程",
        "blocks": [{"type": "paragraph", "text": "**阶段变化**" * 35}],
    }]}
    result = validate_lecture_group(
        group, [], "带时间的口语字幕" * 45, minimum_text_ratio=0.35
    )
    assert result["valid"], result["errors"]


def test_supplement_group_requires_significant_source_numbers():
    group = {"sections": [{
        "heading": "数值例子",
        "blocks": [{"type": "paragraph", "text": "目标为**50V**，占空比为**0.5**。"}],
    }]}
    source = "<!-- FRAME: 14 -->[920.5-921.9] 输入100V，目标输出50V，占空比0.5。"
    result = validate_lecture_group(
        group, [], source, minimum_text_ratio=0.17, require_source_numbers=True
    )
    assert not result["valid"]
    assert "group_missing_source_numbers_100" in result["errors"]


def test_significant_number_guard_ignores_digits_inside_asr_identifiers():
    group = {"sections": [{
        "heading": "电感电流",
        "blocks": [{"type": "paragraph", "text": "I2 一定是负值，结合 L1 判断电流方向与工作状态。"}],
    }]}
    source = "[953.2-955.6] a21 定是一个负的值，结合 L1 与 I2 判断。"
    result = validate_lecture_group(
        group, [], source, minimum_text_ratio=0.17, require_source_numbers=True
    )
    assert result["valid"], result["errors"]


def test_significant_number_guard_ignores_cross_frame_provenance():
    group = {"sections": [{
        "heading": "工作象限",
        "blocks": [{
            "type": "paragraph",
            "text": "本页继续说明参考方向如何决定器件的工作象限，并强调应先统一电压和电流的参考方向。",
        }],
    }]}
    source = (
        "与 frame_number=11 内容相同，继续说明参考方向如何决定器件的工作象限，"
        "并强调应先统一电压和电流的参考方向。"
    )
    result = validate_lecture_group(
        group, [], source, minimum_text_ratio=0.17, require_source_numbers=True
    )
    assert result["valid"], result["errors"]


def test_significant_number_guard_ignores_cross_frame_provenance_lists():
    group = {"sections": [{
        "heading": "工作象限",
        "blocks": [{
            "type": "paragraph",
            "text": "内容与前面的画面完全相同；这些标记只描述采集来源。",
        }],
    }]}
    source = (
        "内容与 frame_number=10和11 完全相同，也对应第10帧和11帧，"
        "以及 image 10, 11；这些数字只描述采集来源。"
    )
    result = validate_lecture_group(
        group, [], source, minimum_text_ratio=0.17, require_source_numbers=True
    )
    assert result["valid"], result["errors"]


def test_supplement_group_allows_narrow_text_retention_boundary_tolerance():
    source = "甲" * 1829
    group = {"sections": [{
        "heading": "补充画面",
        "blocks": [{"type": "paragraph", "text": "乙" * 296}],
    }]}

    result = validate_lecture_group(
        group, [], source, minimum_text_ratio=0.17, require_source_numbers=True
    )

    assert result["valid"], result["errors"]


def test_regular_group_allows_narrow_text_retention_boundary_tolerance():
    source = "甲" * 565
    group = {"sections": [{
        "heading": "驱动电路",
        "blocks": [{"type": "paragraph", "text": "乙" * 296}],
    }]}

    result = validate_lecture_group(
        group, [], source, minimum_text_ratio=0.55, require_source_numbers=False
    )

    assert result["valid"], result["errors"]


def test_numeric_repair_restores_the_source_sentence_without_inference():
    group = {"sections": [{
        "heading": "器件选型",
        "blocks": [{"type": "paragraph", "text": "MOSFET常用于较低电压场合。"}],
    }]}
    source = (
        "<!-- FRAME: 22 -->\n"
        "这个器件一般用于500V甚至1500V以内，当然现在1kV的器件也有。"
    )

    restore_missing_source_number_sentences(
        group, source, ["group_missing_source_numbers_1500"]
    )

    text = group["sections"][0]["blocks"][-1]["text"]
    assert "原稿数值补充：" in text
    assert "500V甚至1500V以内" in text
    assert "<!-- FRAME" not in text


def test_document_retention_repair_adds_clean_high_novelty_source_text():
    document = _valid_document()
    original = (
        "<!-- FRAME: 1 -->\n\n"
        + "那么这是需要完整保留的器件工作机理与实验条件。" * 100
    )
    before = len(document_text(document))

    ensure_document_text_retention(
        document,
        original,
        [f"structured_text_too_short_{before}_minimum_{before + 500}"],
    )

    assert document["sections"][-1]["heading"] == "原稿细节保留"
    added = document["sections"][-1]["blocks"][0]["text"]
    assert "器件工作机理与实验条件" in added
    assert "那么" not in added
    assert len(document_text(document)) >= before + 500


def test_callout_kind_repair_relabels_only_redundant_callouts():
    document = _valid_document()
    for section in document["sections"]:
        for block in section["blocks"]:
            if block.get("type") == "callout":
                block["kind"] = "definition"
    original_text = [
        block["text"]
        for section in document["sections"]
        for block in section["blocks"]
        if block.get("type") == "callout"
    ]

    ensure_callout_kinds(document)

    callouts = [
        block
        for section in document["sections"]
        for block in section["blocks"]
        if block.get("type") == "callout"
    ]
    assert {block["kind"] for block in callouts} == {
        "definition", "derivation", "conclusion", "warning"
    }
    assert [block["text"] for block in callouts] == original_text


def test_short_lecture_scales_structure_thresholds_to_source_size():
    document = _valid_document()
    document["sections"] = document["sections"][:5]
    frame = 1
    for section in document["sections"]:
        section["blocks"] = [
            block for block in section["blocks"] if block.get("type") != "frame"
        ]
        section["blocks"].append({"type": "frame", "frame_number": frame})
        frame += 1
    text_blocks = [
        block
        for section in document["sections"]
        for block in section["blocks"]
        if block.get("type") in {"paragraph", "callout", "subheading"}
    ]
    for block in text_blocks:
        block["text"] += " **补充重点**"

    result = validate_lecture_document(document, [1, 2, 3, 4, 5], "原稿" * 1000)

    assert not any("need_at_least_7_sections" in error for error in result["errors"])
    assert result["metrics"]["minimum_bold_pairs"] == 10


def test_normalize_group_blocks_flattens_nested_content_without_losing_it():
    group = {"sections": [{
        "heading": "目标",
        "blocks": [{
            "type": "subheading",
            "text": "方法",
            "blocks": [{"type": "paragraph", "text": "保留这段内容"}],
        }],
    }]}
    normalize_group_blocks(group)
    assert group["sections"][0]["blocks"] == [
        {"type": "subheading", "text": "方法"},
        {"type": "paragraph", "text": "保留这段内容"},
    ]
    group["sections"][0]["blocks"][1]["text"] *= 5
    result = validate_lecture_group(group, [], "方法：保留这段内容")
    assert result["valid"], result["errors"]


def test_group_validator_rejects_nested_blocks_that_renderer_would_drop():
    group = {"sections": [{
        "heading": "目标",
        "blocks": [{
            "type": "subheading",
            "text": "方法",
            "blocks": [{"type": "paragraph", "text": "不能藏在这里"}],
        }],
    }]}
    result = validate_lecture_group(group, [], "目标与方法")
    assert not result["valid"]
    assert any("unknown_keys_blocks" in error for error in result["errors"])


def test_group_validator_rejects_unedited_oral_transcript_residue():
    group = {"sections": [{
        "heading": "频域说明",
        "blocks": [{
            "type": "paragraph",
            "text": "那么其实我们来看一下，不难看出啊，知识点仍被逐字口语包围。",
        }],
    }]}
    result = validate_lecture_group(group, [], "频域说明原稿")
    assert not result["valid"]
    assert any("oral_fillers" in error for error in result["errors"])


def test_callout_limits_preserve_text_as_plain_paragraphs():
    group = {"sections": [{"heading": "页", "blocks": [
        {"type": "callout", "kind": "definition", "text": "定义"},
        {"type": "callout", "kind": "warning", "text": "条件"},
    ]}]}
    limit_group_callouts(group, maximum=1)
    assert group["sections"][0]["blocks"][1] == {"type": "paragraph", "text": "条件"}

    document = {"sections": [{"heading": "长文", "blocks": [
        {"type": "callout", "kind": kind, "text": f"重点{index}"}
        for index, kind in enumerate(
            ["definition", "derivation", "conclusion", "warning"] * 4
        )
    ]}]}
    limit_callouts(document, maximum=12)
    blocks = document["sections"][0]["blocks"]
    assert sum(block["type"] == "callout" for block in blocks) == 12
    assert {block.get("kind") for block in blocks if block["type"] == "callout"} == {
        "definition", "derivation", "conclusion", "warning"
    }


def test_minimum_callout_budget_promotes_text_without_rewriting_it():
    document = {"sections": [
        {"heading": "无重点框", "blocks": [
            {"type": "paragraph", "text": "必须原样保留的正文"},
        ]},
        {"heading": "已有重点框", "blocks": [
            {"type": "callout", "kind": "definition", "text": f"重点{index}"}
            for index in range(7)
        ]},
    ]}
    ensure_minimum_callouts(document, minimum=8)
    promoted = document["sections"][0]["blocks"][0]
    assert promoted["text"] == "必须原样保留的正文"
    assert promoted["type"] == "callout"
    assert promoted["kind"] == "derivation"


def test_validator_rejects_generic_summary_headings_but_allows_specific_topics():
    document = _valid_document()
    document["sections"][0]["heading"] = "总结"
    result = validate_lecture_document(document, list(range(1, 10)), "原稿" * 1000)
    assert any("forbidden_generic_heading" in error for error in result["errors"])

    document["sections"][0]["heading"] = "稳态分析基本方法"
    result = validate_lecture_document(document, list(range(1, 10)), "原稿" * 1000)
    assert result["valid"], result["errors"]

    document["sections"][0]["heading"] = "稳态分析方法总结"
    result = validate_lecture_document(document, list(range(1, 10)), "原稿" * 1000)
    assert not any("forbidden_generic_heading" in error for error in result["errors"])

    document["sections"][0]["heading"] = "基本变换器拓扑总结"
    result = validate_lecture_document(document, list(range(1, 10)), "原稿" * 1000)
    assert not any("forbidden_generic_heading" in error for error in result["errors"])

    document["sections"][0]["heading"] = "第二章总结：核心方法"
    result = validate_lecture_document(document, list(range(1, 10)), "原稿" * 1000)
    assert any("forbidden_generic_heading" in error for error in result["errors"])


def test_formatter_reuses_rejected_candidates_before_calling_format_again(tmp_path):
    class FakeAnalyzer:
        def __init__(self, forbid_format=False):
            self.format_calls = 0
            self.repair_calls = 0
            self.forbid_format = forbid_format

        def format_lecture_article_group(self, excerpt, frames, callout_kinds):
            if self.forbid_format:
                raise AssertionError("format call should have been recovered from candidate")
            self.format_calls += 1
            return {"sections": [{
                "heading": "具体主题",
                "blocks": [
                    {"type": "subheading", "text": "总结"},
                    {"type": "paragraph", "text": "**完整内容**" * 60},
                ],
            }]}

        def repair_lecture_article_group(self, excerpt, group, errors):
            self.repair_calls += 1
            return group

    draft = "# 课程\n<!-- FRAME: 1 -->\n原始课程内容"
    first = FakeAnalyzer()
    with pytest.raises(ValueError, match="forbidden_generic_subheading"):
        format_lecture_document(first, draft, [1], tmp_path)
    assert first.format_calls == 1
    assert (tmp_path / "article.frame_001.candidate.json").is_file()

    resumed = FakeAnalyzer(forbid_format=True)
    with pytest.raises(ValueError, match="forbidden_generic_subheading"):
        format_lecture_document(resumed, draft, [1], tmp_path)
    assert resumed.format_calls == 0
