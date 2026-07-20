"""Build a timestamped interactive lesson from an audited video record."""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ANALYZER_ROOT = PROJECT_ROOT / "video-analyzer"
if str(ANALYZER_ROOT) not in sys.path:
    sys.path.insert(0, str(ANALYZER_ROOT))

from video_analyzer.clients.generic_openai_api import GenericOpenAIAPIClient


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _extract_json(text: str) -> dict[str, Any]:
    value = text.strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:json)?\s*", "", value, flags=re.I)
        value = re.sub(r"\s*```$", "", value)
    def repair_invalid_escapes(candidate: str) -> str:
        # Models occasionally emit a stray line-continuation slash before
        # spaces, or LaTeX-like \Delta inside JSON strings. Remove the former
        # and JSON-escape the latter while leaving valid escapes untouched.
        candidate = re.sub(r"\\(?=\s)", "", candidate)
        return re.sub(r'\\(?!["\\/bfnrtu])', r"\\\\", candidate)

    match = re.search(r"\{.*\}", value, flags=re.S)
    candidates = [value, repair_invalid_escapes(value)]
    if match and match.group(0) != value:
        candidates.extend([match.group(0), repair_invalid_escapes(match.group(0))])
    parsed = None
    last_error = None
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
            break
        except json.JSONDecodeError as exc:
            last_error = exc
    if parsed is None:
        raise ValueError(f"模型没有返回可解析的 JSON：{last_error}")
    if not isinstance(parsed, dict):
        raise ValueError("课程结果必须是 JSON 对象")
    return parsed


def _validate_lesson(lesson: dict[str, Any], duration: float, *, strict: bool = True) -> None:
    required = ("title", "overview", "teaching_plan", "checkpoints")
    missing = [key for key in required if not lesson.get(key)]
    if missing:
        raise ValueError(f"课程结果缺少字段：{', '.join(missing)}")
    checkpoints = lesson["checkpoints"]
    if not isinstance(checkpoints, list) or not checkpoints:
        raise ValueError("checkpoints 必须至少包含一个检查点")
    if strict and not 3 <= len(checkpoints) <= 12:
        raise ValueError("严格课程校验要求 checkpoints 包含 3 到 12 个检查点")
    previous = -1.0
    total_questions = 0
    for index, checkpoint in enumerate(checkpoints, 1):
        if not isinstance(checkpoint, dict):
            raise ValueError(f"检查点 {index} 不是对象")
        timestamp = float(checkpoint.get("time", -1))
        if timestamp <= previous or timestamp > duration + 2:
            raise ValueError(f"检查点 {index} 时间戳无效：{timestamp}")
        note_frames = checkpoint.get("note_frame_numbers")
        if note_frames is not None and (
            not isinstance(note_frames, list)
            or any(not isinstance(frame, int) for frame in note_frames)
            or len(note_frames) != len(set(note_frames))
        ):
            raise ValueError(f"检查点 {index} 的笔记帧映射无效")
        note_range = checkpoint.get("note_time_range")
        if note_range is not None and (
            not isinstance(note_range, dict)
            or float(note_range.get("end_inclusive", -1)) != timestamp
        ):
            raise ValueError(f"检查点 {index} 的笔记时间范围无效")
        previous = timestamp
        checkpoint.setdefault("id", f"checkpoint-{index:02d}")
        checkpoint.setdefault("summary", "")
        questions = checkpoint.get("questions")
        if not isinstance(questions, list) or not 1 <= len(questions) <= 5:
            raise ValueError(f"检查点 {index} 的题目数量无效")
        for q_index, question in enumerate(questions, 1):
            if not isinstance(question, dict) or not question.get("prompt"):
                raise ValueError(f"检查点 {index} 第 {q_index} 题无题干")
            q_type = question.get("type", "choice")
            if q_type not in {"choice", "text"}:
                raise ValueError(f"不支持的题型：{q_type}")
            question["type"] = q_type
            answers = question.get("answers")
            if not isinstance(answers, list) or not answers:
                raise ValueError(f"检查点 {index} 第 {q_index} 题无答案")
            if q_type == "choice":
                options = question.get("options")
                if not isinstance(options, list) or len(options) < 2:
                    raise ValueError(f"检查点 {index} 第 {q_index} 题选项不足")
                if answers[0] not in options:
                    raise ValueError(f"检查点 {index} 第 {q_index} 题答案不在选项中")
            question.setdefault("explanation", str(answers[0]))
            question.setdefault("id", f"q-{index:02d}-{q_index:02d}")
            total_questions += 1
    if strict and total_questions < 6:
        raise ValueError("严格课程校验要求整节课程至少需要 6 道题")


def _order_checkpoints(lesson: dict[str, Any]) -> None:
    """Own chronological ordering in code while leaving checkpoint content intact."""
    checkpoints = lesson.get("checkpoints")
    if not isinstance(checkpoints, list) or not all(isinstance(item, dict) for item in checkpoints):
        return
    try:
        checkpoints.sort(key=lambda item: float(item.get("time", -1)))
    except (TypeError, ValueError):
        # Let the regular validator produce the precise schema error.
        return


def _article_frame_timeline(article: str, scenes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build the canonical note timeline from frames that actually occur in article.md."""
    timestamps = {
        int(scene["frame_number"]): float(scene.get("timestamp", 0))
        for scene in scenes
        if isinstance(scene, dict) and scene.get("frame_number") is not None
    }
    frame_numbers: list[int] = []
    pattern = re.compile(
        r"<!--\s*FRAME\s*:\s*(\d+)\s*-->|slide_(\d+)\.[A-Za-z0-9]+",
        re.I,
    )
    for match in pattern.finditer(article):
        frame_number = int(match.group(1) or match.group(2))
        if frame_number in timestamps and frame_number not in frame_numbers:
            frame_numbers.append(frame_number)
    return [
        {"frame_number": frame_number, "time": timestamps[frame_number]}
        for frame_number in frame_numbers
    ]


def _align_checkpoint_notes(
    lesson: dict[str, Any], article: str, scenes: list[dict[str, Any]]
) -> None:
    """Persist an explicit checkpoint-to-note mapping during lesson generation."""
    timeline = _article_frame_timeline(article, scenes)
    lesson["note_alignment"] = {
        "version": "article-frame-interval-v1",
        "article": "article.md",
        "fallback_required": False,
    }
    previous_time = -1.0
    for checkpoint in lesson.get("checkpoints", []):
        checkpoint_time = float(checkpoint["time"])
        checkpoint["note_time_range"] = {
            "start_exclusive": max(0.0, previous_time),
            "end_inclusive": checkpoint_time,
        }
        checkpoint["note_frame_numbers"] = [
            item["frame_number"]
            for item in timeline
            if previous_time < item["time"] <= checkpoint_time + 0.5
        ]
        previous_time = checkpoint_time


def _next_available(path: Path) -> Path:
    if not path.exists():
        return path
    for index in range(2, 1000):
        candidate = path.with_name(f"{path.stem}.{index}{path.suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"无法为失败产物分配文件名：{path}")


def build_lesson(record_dir: Path, lesson_id: str, part: int, source_url: str,
                 *, source_id: str, strict_validation: bool | None = None,
                 validation_profile: str | None = None) -> Path:
    record_dir = record_dir.resolve()
    record = _read_json(record_dir / "record.json")
    transcript = _read_json(record_dir / "transcript.json")
    ocr_timeline = (
        _read_json(record_dir / "ocr_timeline.json")
        if (record_dir / "ocr_timeline.json").is_file()
        else []
    )
    article = (record_dir / "article.md").read_text(encoding="utf-8")
    metadata = record.get("metadata", {})
    profile = validation_profile or str(
        metadata.get("validation_profile")
        or ("general_video" if strict_validation is False else "strict_course")
    )
    if profile not in {"strict_course", "general_video", "phonetics_course"}:
        raise ValueError(f"不支持的课程校验包：{profile}")
    ocr_primary = profile == "phonetics_course"
    strict = profile in {"strict_course", "phonetics_course"}
    duration = float(metadata.get("duration", 0))
    video_path = Path(str(metadata.get("video", ""))).resolve()
    if not video_path.is_file() or duration <= 0:
        raise ValueError("record.json 中的视频路径或时长无效")

    scene_material = []
    for scene in record.get("scenes", []):
        scene_material.append({
            "timestamp": scene.get("timestamp"),
            "title": scene.get("slide_title"),
            "facts": scene.get("visible_facts", []),
            "formulas": scene.get("formulas", []),
            "speech": scene.get("speech", ""),
            "screen_text": scene.get("screen_text", []),
            "ipa_symbols": scene.get("ipa_symbols", []),
            "examples": scene.get("examples", []),
            "articulation_cues": scene.get("articulation_cues", []),
        })
    transcript_material = [
        {
            "start": round(float(segment.get("start", 0)), 2),
            "end": round(float(segment.get("end", segment.get("start", 0))), 2),
            "text": str(segment.get("text", "")).strip(),
        }
        for segment in transcript.get("segments", [])
        if str(segment.get("text", "")).strip()
    ]
    title = metadata.get("metadata", {}).get("title") or f"第 {part} 集"
    materials = {
        "episode_title": title,
        "duration_seconds": duration,
        "evidence_mode": "ocr_primary" if ocr_primary else "audio_visual",
        "audited_article": article,
        "ocr_scenes" if ocr_primary else "ppt_scenes": scene_material,
        "dense_ocr_timeline": ocr_timeline if ocr_primary else [],
        "timestamped_transcript": [] if ocr_primary else transcript_material,
    }
    timing_rule = (
        "time 必须取自对应知识点最后一个 OCR 场景的 timestamp；不得使用字幕或语音推断时间。"
        if ocr_primary
        else "time 必须是老师已经讲完该知识点后的秒数，而不是刚开始讲的秒数；只能依据字幕时间。"
    )
    evidence_rule = (
        "OCR 场景和审计文章是唯一事实源。保持 IPA、英文例词和中文提示原样；"
        "不得依据字幕、语音、常识纠正或补充画面内容。"
        if ocr_primary
        else "只使用材料中明确讲过的内容，不补充常识性知识，不猜测。"
    )
    prompt = f"""
你是一名视频课程教研员。请根据下方已经审计的课程材料，生成一份可直接驱动互动视频播放器的课程 JSON。

硬性要求：
1. {evidence_rule}
2. teaching_plan 用 Markdown，包含学习目标、知识结构、重点、易错点和课堂流程。
3. 生成 {"5 到 10 个" if strict else "适量的"} checkpoints，按教学顺序排列。{timing_rule}
4. 每个检查点 1 到 4 道简单题，默认全部使用 choice。题目只能考该时间点之前已经讲完的内容。
5. 公式、符号、数值、单位、术语定义或精确关系式绝对不要设计成 text 填空题；必须改写成选择题，例如问“以下哪个关系式正确”。不要让学生依赖空格、括号、乘号写法、上下标或 LaTeX 格式才能答对。
6. choice 题格式：type="choice"、options 至少两个、answers 的第一项必须与正确选项完全相同；错误选项应是材料范围内容易混淆但格式完整的表达，不使用残缺字符串。
7. text 仅可用于答案唯一、且唯一可接受表达方式也明确的极少数事实题；只要存在同义表达、不同但等价的表述、公式、数值、单位、符号或任何可能的多种写法，就必须使用 choice。text 答案不得包含公式、精确数值或符号表达式。每题提供 explanation。
8. 不输出代码围栏，不输出 JSON 之外的文字。

JSON 结构：
{{
  "title": "课程标题",
  "overview": "两三句话的课程概览",
  "teaching_plan": "Markdown 教案",
  "checkpoints": [
    {{
      "id": "稳定英文或拼音标识",
      "time": 123.4,
      "title": "知识点标题",
      "summary": "刚讲完的内容",
      "questions": [
        {{
          "id": "q01",
          "type": "choice",
          "prompt": "题干",
          "options": ["选项A", "选项B"],
          "answers": ["选项A"],
          "explanation": "依据课程材料的简短解释"
        }}
      ]
    }}
  ]
}}

课程材料：
{json.dumps(materials, ensure_ascii=False, separators=(',', ':'))}
""".strip()

    api_key = os.environ.get("XIAOMI_MIMO_API_KEY_TEM1")
    base_url = os.environ.get("XIAOMI_MIMO_BASE_URL")
    if not api_key or not base_url:
        raise RuntimeError("缺少 XIAOMI_MIMO_API_KEY_TEM1 或 XIAOMI_MIMO_BASE_URL")
    client = GenericOpenAIAPIClient(
        api_key,
        base_url,
        max_retries=1,
        api_key_header="api-key",
    )
    response = client.generate(
        prompt,
        response_format={"type": "json_object"},
        model="mimo-v2.5",
        temperature=0.0,
        num_predict=8000,
    )
    return finalize_lesson_response(
        response, record_dir, lesson_id, part, source_url, source_id, strict,
        record, article, duration, video_path, validation_profile=profile,
    )


def finalize_lesson_response(
    response: dict[str, Any],
    record_dir: Path,
    lesson_id: str,
    part: int,
    source_url: str,
    source_id: str,
    strict_validation: bool,
    record: dict[str, Any],
    article: str,
    duration: float,
    video_path: Path,
    validation_profile: str | None = None,
) -> Path:
    """Validate and persist a saved provider response without another API call."""
    record_dir = record_dir.resolve()
    metadata = record.get("metadata", {})
    (record_dir / "lesson_builder.api.json").write_text(
        json.dumps(response, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if response.get("finish_reason") == "length":
        raise RuntimeError("课程题目生成因长度限制被截断")
    lesson = _extract_json(str(response.get("response", "")))
    _order_checkpoints(lesson)
    _align_checkpoint_notes(lesson, article, record.get("scenes", []))
    try:
        _validate_lesson(lesson, duration, strict=strict_validation)
    except Exception:
        _next_available(record_dir / "lesson_builder.rejected.json").write_text(
            json.dumps(lesson, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        raise
    lesson.update({
        "id": lesson_id,
        "series_id": source_id,
        "part": part,
        "source_url": source_url,
        "duration": duration,
        "video_path": str(video_path),
        "subtitle_path": (
            None
            if validation_profile == "phonetics_course"
            else (str(metadata.get("subtitle")) if metadata.get("subtitle") else None)
        ),
        "record_dir": str(record_dir),
        "article_path": str(record_dir / "article.md"),
        "generation": {
            "model": response.get("model", "mimo-v2.5"),
            "usage": response.get("usage"),
            "finish_reason": response.get("finish_reason"),
            "automatic_retry": False,
            "validation_profile": validation_profile or (
                "strict_course" if strict_validation else "general_video"
            ),
            "evidence_mode": (
                "ocr_primary"
                if validation_profile == "phonetics_course"
                else "audio_visual"
            ),
        },
    })
    output_dir = Path(__file__).resolve().parent / "data" / "lessons"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{lesson_id}.json"
    output_path.write_text(json.dumps(lesson, ensure_ascii=False, indent=2), encoding="utf-8")
    return output_path
