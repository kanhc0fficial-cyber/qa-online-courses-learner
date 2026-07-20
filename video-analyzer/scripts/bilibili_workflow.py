"""Bilibili-to-article workflow with minimal and complete-PPT modes."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from datetime import datetime
from pathlib import Path

import cv2

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from video_analyzer.analyzer import VideoAnalyzer
from video_analyzer.artifacts import crop_article_frames, render_article, render_record
from video_analyzer.audio_processor import AudioProcessor
from video_analyzer.bilibili import (
    discover_bilibili_assets, download_with_yutto, parse_nfo, parse_srt,
    recommended_bilibili_frame_count, subtitle_quality,
)
from video_analyzer.clients.generic_openai_api import GenericOpenAIAPIClient
from video_analyzer.frame import Frame, VideoProcessor
from video_analyzer.lecture_document import (
    can_recover_lecture_draft_frames,
    render_lecture_document, thin_lecture_draft_frames,
    validate_lecture_draft_frames,
)
from video_analyzer.lecture_formatter import format_lecture_document
from video_analyzer.prompt import PromptLoader

PROMPTS = [
    {"name": "Frame Record", "path": "frame_analysis/frame_analysis.txt"},
    {"name": "Chronological Record", "path": "frame_analysis/record.txt"},
]


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def duration_of(path: Path) -> float:
    cap = cv2.VideoCapture(str(path))
    fps, count = cap.get(cv2.CAP_PROP_FPS), cap.get(cv2.CAP_PROP_FRAME_COUNT)
    cap.release()
    if fps <= 0 or count <= 0:
        raise ValueError(f"无法读取视频时长：{path}")
    return count / fps


def selected_markers(article: str) -> set[int]:
    return {int(value) for value in re.findall(r"<!--\s*FRAME\s*:\s*(\d+)\s*-->", article, re.I)}


def missing_frame_materials(scenes, transcript, missing_frames):
    ordered = sorted(scenes, key=lambda scene: float(scene.get("timestamp", 0.0)))
    materials = []
    for index, scene in enumerate(ordered):
        frame_number = int(scene.get("frame_number", 0))
        if frame_number not in missing_frames:
            continue
        timestamp = float(scene.get("timestamp", 0.0))
        title = str(scene.get("slide_title", "")).strip()
        visible_facts = scene.get("visible_facts", [])
        formulas = scene.get("formulas", [])
        diagrams = scene.get("diagrams", [])
        transition_page = (
            not formulas
            and any(token in title.lower() for token in ("contents", "part", "目录"))
        )
        next_timestamp = (
            float(ordered[index + 1].get("timestamp", timestamp + 90.0))
            if index + 1 < len(ordered) else timestamp + 90.0
        )
        window_start = max(0.0, timestamp - 10.0)
        window_end = timestamp + 5.0 if transition_page else next_timestamp + 5.0
        subtitle = []
        for segment in transcript.segments or []:
            start = float(segment.get("start", 0.0))
            end = float(segment.get("end", start))
            if end >= window_start and start <= window_end:
                text = str(segment.get("text", "")).strip()
                if text:
                    subtitle.append(f"[{start:.1f}-{end:.1f}] {text}")
        materials.append({
            "frame_number": frame_number,
            "timestamp": timestamp,
            "slide_title": title,
            "page_role": "transition" if transition_page else "teaching_content",
            "visible_facts": visible_facts,
            "screen_text": scene.get("screen_text", []),
            "formulas": scene.get("formulas", []),
            "diagrams": scene.get("diagrams", []),
            "subtitle_window": subtitle,
        })
    return materials


def supplemental_excerpt(material):
    number = int(material["frame_number"])
    if material.get("page_role") == "transition":
        visible_text = "；".join(
            str(value) for value in material.get("screen_text", [])[:3]
        )
        return (
            f"<!-- FRAME: {number} -->\n\n"
            "课程过渡页（只记录可见栏目与下一主题，不解释校徽、页脚或机构关系）："
            f"{visible_text or material['slide_title']}"
        )
    lines = [f"<!-- FRAME: {number} -->", "", f"PPT标题：{material['slide_title']}"]
    for label, key in (
        ("画面要点", "visible_facts"),
        ("PPT原文", "screen_text"),
        ("公式", "formulas"),
        ("图示", "diagrams"),
        ("对应字幕", "subtitle_window"),
    ):
        values = material.get(key, [])
        if values:
            lines.append(f"{label}：")
            lines.extend(f"- {value}" for value in values)
    return "\n".join(lines)


def next_available(path: Path) -> Path:
    if not path.exists():
        return path
    index = 1
    while path.with_name(f"{path.stem}.v{index}{path.suffix}").exists():
        index += 1
    return path.with_name(f"{path.stem}.v{index}{path.suffix}")


def main() -> None:
    parser = argparse.ArgumentParser(description="最省 token 的 B 站视频图文流程")
    parser.add_argument("source", help="已有下载目录、BV号或B站链接")
    parser.add_argument("--part", type=int)
    parser.add_argument("--download-root", type=Path, default=ROOT.parent / "downloads")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--max-frames", type=int)
    parser.add_argument("--model", default="mimo-v2.5")
    parser.add_argument(
        "--whisper-model",
        default=os.environ.get("WHISPER_MODEL", "medium"),
    )
    parser.add_argument("--api-key-env", default="XIAOMI_MIMO_API_KEY")
    parser.add_argument("--base-url-env", default="XIAOMI_MIMO_BASE_URL")
    parser.add_argument("--ppt-complete", action="store_true",
                        help="Detect every stable change inside the fixed PPT screen region")
    parser.add_argument("--ppt-ignore-head", type=float, default=0.0)
    parser.add_argument("--ppt-ignore-tail", type=float, default=0.0)
    parser.add_argument("--resume", action="store_true",
                        help="Reuse validated frames, scenes, and draft artifacts from --output")
    parser.add_argument(
        "--validation-profile",
        choices=("strict_course", "general_video", "phonetics_course"),
        default="strict_course",
        help="规则包；phonetics_course 强制以画面 OCR 为唯一事实源",
    )
    args = parser.parse_args()

    source_path = Path(args.source).expanduser()
    if source_path.is_dir():
        source_dir = source_path.resolve()
    else:
        source_dir = (args.download_root / datetime.now().strftime("bilibili_%Y%m%d-%H%M%S")).resolve()
        download_with_yutto(args.source, source_dir, args.part)

    assets = discover_bilibili_assets(source_dir)
    mode_name = (
        "phonetics_ocr"
        if args.validation_profile == "phonetics_course"
        else ("ppt_complete" if args.ppt_complete else "minimal")
    )
    output = (args.output or (ROOT.parent / "records" / f"{assets.video.stem}_bilibili_{mode_name}_{datetime.now():%Y%m%dT%H%M%S}")).resolve()
    output.mkdir(parents=True, exist_ok=True)
    duration = duration_of(assets.video)

    transcript = None
    subtitle_info = {"usable": False}
    transcript_source = "none"
    ocr_primary = args.validation_profile == "phonetics_course"
    if ocr_primary:
        subtitle_info = {
            "usable": False,
            "ignored": True,
            "reason": "phonetics_course_ocr_primary",
        }
        transcript_source = "disabled_ocr_primary"
    elif assets.subtitle and assets.subtitle.suffix.lower() == ".srt":
        candidate = parse_srt(assets.subtitle)
        subtitle_info = subtitle_quality(candidate, duration)
        if subtitle_info["usable"]:
            transcript, transcript_source = candidate, "downloaded_srt"
    if transcript is None and not ocr_primary:
        processor = AudioProcessor(language=None, model_size_or_path=args.whisper_model, device="cpu")
        audio = processor.extract_audio(assets.video, output)
        if not audio or not (transcript := processor.transcribe(audio)):
            raise RuntimeError("字幕不可用，且本地 Whisper 转写失败")
        transcript_source = f"local_whisper_{args.whisper_model}"

    frame_budget = args.max_frames or (
        min(60, max(24, math.ceil(duration / 60.0 * 8)))
        if ocr_primary
        else recommended_bilibili_frame_count(duration)
    )
    frame_processor = VideoProcessor(assets.video, output / "assets" / "frames", args.model)
    frames_path = output / "frames.json"
    if args.resume and frames_path.exists():
        frame_rows = json.loads(frames_path.read_text(encoding="utf-8"))
        frames = [Frame(
            number=int(row["number"]),
            path=output / row["path"],
            timestamp=float(row["timestamp"]),
            score=float(row["score"]),
            source=str(row.get("source", "resumed")),
        ) for row in frame_rows]
        if not frames or not all(frame.path.exists() for frame in frames):
            raise RuntimeError("--resume frames.json references missing frame files")
        frame_budget = None if args.ppt_complete and not ocr_primary else len(frames)
    elif args.ppt_complete and not ocr_primary:
        frames = frame_processor.extract_ppt_slides(
            ignore_head_seconds=args.ppt_ignore_head,
            ignore_tail_seconds=args.ppt_ignore_tail,
        )
        frame_budget = None
    elif ocr_primary:
        frames = frame_processor.extract_keyframes(
            frames_per_minute=8,
            max_frames=frame_budget,
            scene_threshold=0.08,
            hash_distance=2,
            detect_scenes=True,
        )
    else:
        frames = frame_processor.extract_keyframes(
            frames_per_minute=1, max_frames=frame_budget, scene_threshold=0.16,
            detect_scenes=False,
        )
    ocr_frames = []
    raw_ocr_timeline = []
    if ocr_primary:
        raw_timeline_path = output / "ocr_timeline.raw.json"
        if args.resume and raw_timeline_path.is_file():
            raw_ocr_timeline = json.loads(
                raw_timeline_path.read_text(encoding="utf-8")
            )
            ocr_frames = [
                Frame(
                    number=int(item["segment"]),
                    path=Path(str(item["frame_path"])),
                    timestamp=float(item["sample_timestamp"]),
                    score=float(item.get("change_ratio", 0.0)),
                    source="resumed_dense_caption_ocr",
                )
                for item in raw_ocr_timeline
            ]
            if not ocr_frames or not all(frame.path.is_file() for frame in ocr_frames):
                raise RuntimeError(
                    "--resume ocr_timeline.raw.json references missing OCR frames"
                )
        else:
            ocr_frames, raw_ocr_timeline = frame_processor.extract_dense_ocr_segments(
                output / "assets" / "ocr_captions",
                sample_interval=0.25,
            )
    metadata = parse_nfo(assets.metadata)
    manifest = {
        "workflow": (
            "bilibili_phonetics_ocr_v1"
            if ocr_primary
            else ("bilibili_ppt_complete_v1" if args.ppt_complete else "bilibili_token_minimal_v1")
        ),
        "validation_profile": args.validation_profile,
        "evidence_mode": "ocr_primary" if ocr_primary else "audio_visual",
        "video": str(assets.video), "duration": duration,
        "subtitle": None if ocr_primary else (str(assets.subtitle) if assets.subtitle else None),
        "ignored_subtitle": str(assets.subtitle) if ocr_primary and assets.subtitle else None,
        "subtitle_quality": subtitle_info, "transcript_source": transcript_source,
        "metadata": metadata,
        "danmaku": {"path": str(assets.danmaku) if assets.danmaku else None,
                    "role": "audience_commentary", "used_for_facts": False},
        "cover": str(assets.cover) if assets.cover else None,
        "frame_budget": frame_budget, "extracted_frames": len(frames),
        "ocr_sampling": ({
            "sample_interval_seconds": 0.25,
            "frames_per_minute": 8,
            "scene_threshold": 0.08,
            "hash_distance": 2,
            "max_frames": frame_budget,
            "fact_source": "frame_ocr_only",
            "dense_caption_segments": len(ocr_frames),
            "coverage_policy": "every_detected_caption_state_must_have_an_ocr_result",
        } if ocr_primary else None),
        "ppt_detection": ({
            "detector_bounds": [0.035, 0.075, 0.60, 0.78],
            "content_bounds": [0.02, 0.055, 0.73, 0.79],
            "ignore_head_seconds": args.ppt_ignore_head,
            "ignore_tail_seconds": args.ppt_ignore_tail,
            "scene_scope": "fixed_ppt_region_only",
        } if args.ppt_complete and not ocr_primary else None),
        "token_policy": {"visual_calls": "ceil(frames/4)" if args.ppt_complete or ocr_primary else "ceil(frames/6)", "article_calls": 1,
                         "fact_check_call": False, "automatic_retry": False},
    }
    write_json(output / "source_manifest.json", manifest)
    write_json(output / "transcript.json", {
        "text": transcript.text if transcript else "",
        "segments": transcript.segments if transcript else [],
        "language": transcript.language if transcript else None,
        "source": transcript_source,
        "used_for_facts": not ocr_primary,
    })
    write_json(output / "frames.json", [{
        "number": f.number, "path": f.path.relative_to(output).as_posix(),
        "timestamp": f.timestamp, "score": f.score, "source": f.source,
    } for f in frames])
    if ocr_primary:
        write_json(output / "ocr_timeline.raw.json", raw_ocr_timeline)
    if args.prepare_only:
        write_json(output / "run_state.json", {
            "status": "prepared",
            "next": "MiMo visual grounding",
            "dense_ocr_segments": len(ocr_frames),
        })
        print(output)
        return

    client = GenericOpenAIAPIClient(
        os.environ[args.api_key_env], os.environ[args.base_url_env],
        max_retries=1, api_key_header="api-key",
    )
    analyzer = VideoAnalyzer(client, args.model, PromptLoader("", PROMPTS), 0.0)
    record_path = output / "record.json"
    api_log_path = output / "api_calls.json"
    prior_call_log = []
    api_history_complete = True
    if args.resume and api_log_path.exists():
        prior_value = json.loads(api_log_path.read_text(encoding="utf-8"))
        if isinstance(prior_value, list):
            prior_call_log = prior_value
    if args.resume and not prior_call_log and record_path.exists():
        api_history_complete = False
    cached_article_units = len(list(output.glob("article.frame_*.cache.json")))
    if args.resume and len(prior_call_log) < cached_article_units:
        api_history_complete = False

    def combined_call_log():
        return prior_call_log + analyzer.call_log

    ocr_timeline = []
    if ocr_primary:
        completed_timeline_path = output / "ocr_timeline.json"
        if args.resume and completed_timeline_path.is_file():
            saved_timeline = json.loads(
                completed_timeline_path.read_text(encoding="utf-8")
            )
            if isinstance(saved_timeline, list):
                ocr_timeline = saved_timeline
        if len(ocr_timeline) > len(ocr_frames):
            raise RuntimeError("--resume OCR timeline has more results than raw segments")
        for start in range(len(ocr_timeline), len(ocr_frames), 4):
            frame_group = ocr_frames[start:start + 4]
            try:
                results = analyzer.analyze_phonetics_caption_group(frame_group)
            except Exception as exc:
                write_json(output / "ocr_timeline.json", ocr_timeline)
                write_json(output / "api_calls.json", combined_call_log())
                write_json(output / "run_state.json", {
                    "status": "failed",
                    "stage": "dense_caption_ocr",
                    "error": str(exc),
                    "completed_segments": len(ocr_timeline),
                    "expected_segments": len(ocr_frames),
                    "policy": "stopped_after_first_model_error",
                })
                raise
            for raw, result in zip(raw_ocr_timeline[start:start + 4], results):
                item = {**raw, **result}
                ocr_timeline.append(item)
            write_json(output / "ocr_timeline.json", ocr_timeline)
            write_json(output / "api_calls.json", combined_call_log())

        unresolved = [
            item for item in ocr_timeline
            if item.get("has_caption")
            and (
                not str(item.get("caption_text", "")).strip()
                or item.get("uncertainties")
            )
        ]
        if len(ocr_timeline) != len(raw_ocr_timeline) or unresolved:
            write_json(output / "run_state.json", {
                "status": "failed",
                "stage": "dense_caption_ocr_validation",
                "expected_segments": len(raw_ocr_timeline),
                "actual_segments": len(ocr_timeline),
                "unresolved_segments": [
                    int(item.get("segment", -1)) for item in unresolved
                ],
                "policy": "no_silent_caption_omissions",
            })
            raise RuntimeError(
                "音标课程字幕 OCR 覆盖不完整；未确认时间段已保留在 ocr_timeline.json"
            )

    scenes = []
    cached_scenes = {}
    if args.resume and record_path.exists():
        cached_record = json.loads(record_path.read_text(encoding="utf-8"))
        cached_scenes = {
            int(scene["frame_number"]): scene
            for scene in cached_record.get("scenes", [])
            if isinstance(scene, dict) and "frame_number" in scene
        }
    group_size = 4 if args.ppt_complete or ocr_primary else 6
    for start in range(0, len(frames), group_size):
        frame_group = frames[start:start + group_size]
        if all(frame.number in cached_scenes for frame in frame_group):
            batch = [cached_scenes[frame.number] for frame in frame_group]
        else:
            if ocr_primary:
                batch = analyzer.analyze_phonetics_frame_group(frame_group)
            elif args.ppt_complete:
                batch = analyzer.analyze_lecture_slide_group(frame_group, transcript)
            else:
                batch = analyzer.analyze_bilibili_frame_group(frame_group, transcript)
        failed = next((str(value) for scene in batch for value in scene.get("uncertainties", [])
                       if str(value).startswith((
                           "Bilibili frame group failed:",
                           "Lecture slide group failed:",
                           "Phonetics OCR frame group failed:",
                       ))), None)
        if failed:
            write_json(output / "run_state.json", {
                "status": "failed", "stage": "mimo_visual_grounding",
                "error": failed, "policy": "stopped_after_first_model_error",
            })
            write_json(output / "record.json", {"metadata": manifest, "scenes": scenes + batch})
            write_json(output / "api_calls.json", combined_call_log())
            raise RuntimeError(failed)
        scenes.extend(batch)
        write_json(record_path, {"metadata": manifest, "scenes": scenes})
        write_json(output / "api_calls.json", combined_call_log())
    # One editorial call only. The raw scenes/transcript remain available for audit.
    draft_path = output / "article.content_draft.md"
    saved_pre_coverage_drafts = sorted(
        output.glob("article.content_draft.pre_coverage_repair*.md"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if args.resume and (draft_path.exists() or saved_pre_coverage_drafts):
        reusable_draft = (
            draft_path if draft_path.exists() else saved_pre_coverage_drafts[0]
        )
        content_draft = reusable_draft.read_text(encoding="utf-8").strip()
    else:
        if ocr_primary:
            content_draft = analyzer.compose_phonetics_article(
                scenes,
                ocr_timeline,
                metadata,
            )
        elif args.ppt_complete:
            content_draft = analyzer.compose_lecture_article(scenes, transcript, metadata)
        else:
            content_draft = analyzer.compose_article(scenes, transcript, metadata)
    if content_draft.startswith(("# 文章生成失败", "# 课程记录生成失败")):
        write_json(output / "run_state.json", {
            "status": "failed", "stage": "mimo_article", "error": content_draft,
        })
        raise RuntimeError(content_draft)
    article = content_draft
    if args.ppt_complete and args.validation_profile == "strict_course":
        expected_frames = [
            int(scene["frame_number"]) for scene in scenes
            if scene.get("relevance", "content") == "content"
        ]
        coverage = validate_lecture_draft_frames(content_draft, expected_frames)
        thin_frames = thin_lecture_draft_frames(content_draft)
        coverage["thin_frames"] = thin_frames
        supplements = {}
        if not coverage["valid"] or thin_frames:
            next_available(output / "article.content_draft.pre_coverage_repair.md").write_text(
                content_draft + "\n", encoding="utf-8"
            )
            expected_existing_order = [
                frame for frame in expected_frames if frame in coverage["actual"]
            ]
            coverage["requires_reorder"] = coverage["actual"] != expected_existing_order
            can_supplement = can_recover_lecture_draft_frames(coverage)
            coverage["recoverable_by_code"] = can_supplement
            if can_supplement:
                frames_needing_source = set(coverage["missing"]) | set(thin_frames)
                materials = missing_frame_materials(
                    scenes, transcript, frames_needing_source
                )
                supplements = {
                    int(material["frame_number"]): supplemental_excerpt(material)
                    for material in materials
                }
                coverage["supplemented_frames"] = sorted(supplements)
                coverage["valid_with_supplements"] = (
                    sorted(supplements) == sorted(frames_needing_source)
                )
                coverage["normalization"] = {
                    "order_owned_by": "record.json expected_frames",
                    "model_order_preserved": False,
                    "missing_frames_filled_from_audited_sources": sorted(supplements),
                }
                write_json(output / "article.content_supplements.json", materials)
        write_json(output / "article.content_draft.validation.json", coverage)
        write_json(output / "api_calls.json", combined_call_log())
        if not coverage["valid"] and not coverage.get("valid_with_supplements", False):
            next_available(output / "article.content_draft.rejected.md").write_text(
                content_draft + "\n", encoding="utf-8"
            )
            raise RuntimeError(
                f"Lecture draft frame coverage failed: expected {coverage['expected']}, "
                f"got {coverage['actual']}"
            )
        draft_path.write_text(content_draft + "\n", encoding="utf-8")
        try:
            document, validation = format_lecture_document(
                analyzer, content_draft, expected_frames, output, group_size=1,
                supplemental_excerpts=supplements,
            )
        finally:
            # Formatting is cacheable and may fail after many successful calls.
            # Persist its call history before propagating the validation error.
            write_json(output / "api_calls.json", combined_call_log())
        write_json(output / "article.document.json", document)
        article = render_lecture_document(document)
    chosen = selected_markers(article)
    if not args.ppt_complete or ocr_primary:
        crop_article_frames(scenes, frames, output, selected_frame_numbers=chosen)
    render_record(scenes, output)
    (output / "article.source.md").write_text(article + "\n", encoding="utf-8")
    render_article(article, scenes, output, max_images=None if args.ppt_complete else 8)
    write_json(output / "record.json", {"metadata": manifest, "scenes": scenes})
    calls = combined_call_log()
    write_json(output / "api_calls.json", calls)
    write_json(output / "run_state.json", {
        "status": "complete",
        "api_call_count": len(calls),
        "api_history_complete": api_history_complete,
    })
    print(output)


if __name__ == "__main__":
    main()
