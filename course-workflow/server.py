"""Local web workflow for Bilibili videos, with optional course strictness."""

from __future__ import annotations

import json
import os
import re
import sys
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, Response
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
ANALYZER_ROOT = PROJECT_ROOT / "video-analyzer"
if str(ANALYZER_ROOT) not in sys.path:
    sys.path.insert(0, str(ANALYZER_ROOT))

from lesson_builder import build_lesson
from article_builder import generate_strict_article
from analysis_runner import (
    find_reusable_download,
    parse_bilibili_source,
    run_bilibili_analysis,
)
from video_analyzer.bilibili import download_with_yutto

DATA_DIR = ROOT / "data"
LESSONS_DIR = DATA_DIR / "lessons"
ARTICLES_DIR = DATA_DIR / "articles"
JOBS_DIR = DATA_DIR / "jobs"
HOME_LAYOUT_PATH = DATA_DIR / "home_layout.json"
STATIC_DIR = ROOT / "static"
for directory in (LESSONS_DIR, ARTICLES_DIR, JOBS_DIR):
    directory.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="电力电子互动课程工坊")
job_lock = threading.Lock()
MAX_ACTIVE_JOBS = max(1, int(os.environ.get("COURSE_MAX_ACTIVE_JOBS", "15")))
ValidationProfile = Literal["strict_course", "general_video", "phonetics_course"]


class JobRequest(BaseModel):
    source: str
    part: int | None = None
    reuse_download: bool = True
    force_rebuild: bool = False
    ppt_complete: bool = True
    strict_validation: bool | None = None
    validation_profile: ValidationProfile | None = None


class ArticleJobRequest(BaseModel):
    source: str
    part: int | None = None
    reuse_download: bool = True


class DownloadBatchRequest(BaseModel):
    source: str
    start_part: int
    end_part: int
    execution_mode: Literal["parallel", "serial"] = "serial"
    reuse_download: bool = True


class CourseBatchRequest(BaseModel):
    source: str
    start_part: int
    end_part: int
    execution_mode: Literal["parallel", "serial"] = "serial"
    reuse_download: bool = True
    ppt_complete: bool = True
    strict_validation: bool | None = None
    validation_profile: ValidationProfile | None = None


class HomeLayoutRequest(BaseModel):
    source_order: list[str]
    lesson_order: dict[str, list[str]]
    titles: dict[str, str]


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def normalized_home_layout(value: dict[str, Any]) -> dict[str, Any]:
    lessons = [read_json(path) for path in lesson_files()]
    lesson_sources = {
        str(lesson.get("id", "")): str(lesson.get("series_id", ""))
        for lesson in lessons
        if lesson.get("id") and lesson.get("series_id")
    }
    valid_sources = set(lesson_sources.values())

    source_order = []
    for source_id in value.get("source_order", []):
        source_id = str(source_id)
        if source_id in valid_sources and source_id not in source_order:
            source_order.append(source_id)

    lesson_order = {}
    raw_lesson_order = value.get("lesson_order", {})
    if isinstance(raw_lesson_order, dict):
        for source_id, lesson_ids in raw_lesson_order.items():
            source_id = str(source_id)
            if source_id not in valid_sources or not isinstance(lesson_ids, list):
                continue
            ordered_ids = []
            for lesson_id in lesson_ids:
                lesson_id = str(lesson_id)
                if (
                    lesson_sources.get(lesson_id) == source_id
                    and lesson_id not in ordered_ids
                ):
                    ordered_ids.append(lesson_id)
            if ordered_ids:
                lesson_order[source_id] = ordered_ids

    titles = {}
    raw_titles = value.get("titles", {})
    if isinstance(raw_titles, dict):
        for source_id, title in raw_titles.items():
            source_id = str(source_id)
            title = str(title).strip()
            if source_id in valid_sources and title:
                titles[source_id] = title[:80]
    return {
        "source_order": source_order,
        "lesson_order": lesson_order,
        "titles": titles,
    }


def parse_source(source: str, explicit_part: int | None = None) -> tuple[str, str, int]:
    return parse_bilibili_source(source, explicit_part)


def lesson_files() -> list[Path]:
    return sorted(LESSONS_DIR.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)


def public_lesson(lesson: dict[str, Any]) -> dict[str, Any]:
    value = dict(lesson)
    value.pop("video_path", None)
    value.pop("record_dir", None)
    value.pop("article_path", None)
    value["url"] = f"/lessons/{lesson['id']}"
    value["video_url"] = f"/media/{lesson['id']}"
    value["subtitle_url"] = (
        f"/subtitles/{lesson['id']}.vtt"
        if subtitle_path_for(lesson) or transcript_path_for(lesson)
        else None
    )
    value["article_count"] = len(article_files_for(lesson))
    return value


def load_lesson(lesson_id: str) -> dict[str, Any]:
    path = LESSONS_DIR / f"{lesson_id}.json"
    if not path.is_file():
        raise HTTPException(404, "课程不存在")
    return read_json(path)


def record_dir_for(lesson: dict[str, Any]) -> Path:
    path = Path(str(lesson.get("record_dir", ""))).resolve()
    if not path.is_dir():
        raise HTTPException(404, "课程记录目录不存在")
    return path


def record_for(lesson: dict[str, Any]) -> dict[str, Any]:
    path = record_dir_for(lesson) / "record.json"
    if not path.is_file():
        raise HTTPException(404, "record.json 不存在")
    return read_json(path)


def subtitle_path_for(lesson: dict[str, Any]) -> Path | None:
    configured = lesson.get("subtitle_path")
    if configured:
        path = Path(str(configured)).resolve()
        if path.is_file() and path.suffix.lower() in {".srt", ".vtt"}:
            return path
    try:
        record = record_for(lesson)
    except HTTPException:
        return None
    configured = record.get("metadata", {}).get("subtitle")
    if configured:
        path = Path(str(configured)).resolve()
        if path.is_file() and path.suffix.lower() in {".srt", ".vtt"}:
            return path
    return None


def transcript_path_for(lesson: dict[str, Any]) -> Path | None:
    try:
        record_dir = record_dir_for(lesson)
    except HTTPException:
        return None
    path = record_dir / "transcript.json"
    if not path.is_file():
        return None
    try:
        value = read_json(path)
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    segments = value.get("segments")
    if not isinstance(segments, list):
        return None
    usable = any(
        isinstance(segment, dict)
        and str(segment.get("text", "")).strip()
        and segment.get("start") is not None
        for segment in segments
    )
    return path if usable else None


def srt_to_vtt(source: str) -> str:
    normalized = source.replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff")
    cues = []
    for block in re.split(r"\n{2,}", normalized.strip()):
        lines = [line.rstrip() for line in block.split("\n")]
        timestamp_index = next((index for index, line in enumerate(lines) if "-->" in line), None)
        if timestamp_index is None:
            continue
        timestamp = re.sub(
            r"(\d{2}:\d{2}:\d{2}),(\d{3})",
            r"\1.\2",
            lines[timestamp_index],
        )
        text_lines = lines[timestamp_index + 1:]
        if text_lines:
            cues.append(timestamp + "\n" + "\n".join(text_lines))
    if not cues:
        raise ValueError("字幕文件中没有有效时间轴")
    return "WEBVTT\n\n" + "\n\n".join(cues) + "\n"


def vtt_timestamp(seconds: float) -> str:
    milliseconds = max(0, round(float(seconds) * 1000))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    whole_seconds, milliseconds = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{whole_seconds:02d}.{milliseconds:03d}"


def transcript_to_vtt(transcript: dict[str, Any]) -> str:
    cues = []
    for segment in transcript.get("segments", []):
        if not isinstance(segment, dict):
            continue
        text = str(segment.get("text", "")).strip()
        if not text or segment.get("start") is None:
            continue
        start = max(0.0, float(segment["start"]))
        end = float(segment.get("end", start + 2.0))
        if end <= start:
            end = start + 0.5
        cues.append(
            f"{vtt_timestamp(start)} --> {vtt_timestamp(end)}\n{text}"
        )
    if not cues:
        raise ValueError("transcript.json 中没有可用的时间轴片段")
    return "WEBVTT\n\n" + "\n\n".join(cues) + "\n"


def article_files_for(lesson: dict[str, Any]) -> list[Path]:
    try:
        record_dir = record_dir_for(lesson)
    except HTTPException:
        return []
    files = [
        path for path in record_dir.iterdir()
        if path.is_file() and path.suffix.lower() == ".md" and "article" in path.name.lower()
    ]
    def priority(path: Path) -> tuple[int, str]:
        name = path.name.lower()
        if name == "article.md":
            return (0, name)
        if name == "article.source.md":
            return (1, name)
        if "rejected" in name:
            return (5, name)
        if "draft" in name:
            return (4, name)
        return (2, name)
    return sorted(files, key=priority)


def article_kind(name: str) -> str:
    lowered = name.lower()
    if lowered == "article.md":
        return "final"
    if lowered == "article.source.md":
        return "source"
    if "rejected" in lowered:
        return "rejected"
    if "draft" in lowered:
        return "draft"
    return "version"


def safe_article_file(lesson: dict[str, Any], filename: str) -> Path:
    if Path(filename).name != filename or "article" not in filename.lower() or not filename.lower().endswith(".md"):
        raise HTTPException(400, "无效的 article 文件名")
    record_dir = record_dir_for(lesson)
    path = (record_dir / filename).resolve()
    if path.parent != record_dir or not path.is_file():
        raise HTTPException(404, "article 文件不存在")
    return path


def rewrite_article_images(
    markdown: str,
    lesson_id: str,
    record_dir: Path,
    collection: str = "lessons",
    asset_route: str = "assets",
) -> str:
    image_pattern = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)(?:\s+['\"][^)]*['\"])?\)")
    def replace(match: re.Match[str]) -> str:
        alt, target = match.group(1), match.group(2)
        if target.startswith(("http://", "https://", "data:")):
            return match.group(0)
        candidate = (record_dir / target.replace("/", os.sep)).resolve()
        if record_dir not in candidate.parents or not candidate.is_file():
            return f"![{alt}](missing-image)"
        relative = candidate.relative_to(record_dir).as_posix()
        return f"![{alt}](/api/{collection}/{quote(lesson_id, safe='')}/{asset_route}/{quote(relative, safe='/')})"
    return image_pattern.sub(replace, markdown)


def article_blocks(
    lesson: dict[str, Any],
    filename: str,
    collection: str = "lessons",
    asset_route: str = "assets",
) -> dict[str, Any]:
    path = safe_article_file(lesson, filename)
    record_dir = record_dir_for(lesson)
    record = record_for(lesson)
    scenes = {
        int(scene["frame_number"]): scene
        for scene in record.get("scenes", [])
        if isinstance(scene, dict) and scene.get("frame_number") is not None
    }
    lines = path.read_text(encoding="utf-8").splitlines()
    anchors: list[tuple[int, int]] = []
    for index, line in enumerate(lines):
        marker = re.search(r"<!--\s*FRAME\s*:\s*(\d+)\s*-->", line, re.I)
        image = re.search(r"!\[[^\]]*\]\([^)]*slide_(\d+)\.[^)]+\)", line, re.I)
        match = marker or image
        if match:
            frame_number = int(match.group(1))
            if not anchors or anchors[-1][1] != frame_number:
                anchors.append((index, frame_number))
    if not anchors:
        markdown = rewrite_article_images(
            "\n".join(lines), lesson["id"], record_dir, collection, asset_route
        )
        return {"filename": filename, "kind": article_kind(filename), "blocks": [{
            "id": "block-001", "frame_number": None, "time": 0.0,
            "title": filename, "markdown": markdown,
        }]}
    boundaries = []
    previous_anchor = -1
    for position, _ in anchors:
        if not boundaries:
            boundaries.append(0)
        else:
            headings = [
                index for index in range(previous_anchor + 1, position + 1)
                if re.match(r"^#{1,2}\s+", lines[index])
            ]
            boundaries.append(headings[-1] if headings else previous_anchor + 1)
        previous_anchor = position
    blocks = []
    for block_index, ((_, frame_number), start) in enumerate(zip(anchors, boundaries), 1):
        end = boundaries[block_index] if block_index < len(boundaries) else len(lines)
        chunk_lines = lines[start:end]
        has_image = any(re.search(r"!\[[^\]]*\]\([^)]+\)", line) for line in chunk_lines)
        slide_path = record_dir / "assets" / "frames" / f"slide_{frame_number:03d}.jpg"
        if not has_image and slide_path.is_file():
            insert_at = 1 if chunk_lines and chunk_lines[0].startswith("#") else 0
            chunk_lines[insert_at:insert_at] = ["", f"![PPT 第 {frame_number} 页](assets/frames/slide_{frame_number:03d}.jpg)", ""]
        markdown = rewrite_article_images(
            "\n".join(chunk_lines).strip(),
            lesson["id"],
            record_dir,
            collection,
            asset_route,
        )
        scene = scenes.get(frame_number, {})
        heading = next((re.sub(r"^#+\s+", "", line).strip() for line in chunk_lines if re.match(r"^#+\s+", line)), "")
        blocks.append({
            "id": f"block-{block_index:03d}",
            "frame_number": frame_number,
            "time": float(scene.get("timestamp", 0.0)),
            "title": heading or scene.get("slide_title") or f"PPT 第 {frame_number} 页",
            "slide_title": scene.get("slide_title", ""),
            "markdown": markdown,
        })
    return {"filename": filename, "kind": article_kind(filename), "blocks": blocks}


def find_existing_lesson(source_id: str, part: int) -> dict[str, Any] | None:
    for path in lesson_files():
        lesson = read_json(path)
        if lesson.get("series_id") == source_id and int(lesson.get("part", -1)) == part:
            return lesson
    return None


def article_files() -> list[Path]:
    return sorted(
        ARTICLES_DIR.glob("*.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )


def load_article_entry(article_id: str) -> dict[str, Any]:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", article_id):
        raise HTTPException(404, "文章不存在")
    path = ARTICLES_DIR / f"{article_id}.json"
    if not path.is_file():
        raise HTTPException(404, "文章不存在")
    return read_json(path)


def public_article_entry(article: dict[str, Any]) -> dict[str, Any]:
    value = dict(article)
    value.pop("record_dir", None)
    value.pop("article_path", None)
    value["url"] = f"/articles/{article['id']}"
    value["playable"] = False
    return value


def find_existing_article(source_id: str, part: int) -> dict[str, Any] | None:
    for path in article_files():
        article = read_json(path)
        if (
            article.get("series_id") == source_id
            and int(article.get("part", -1)) == part
        ):
            return article
    return None


def resolve_validation_profile(
    validation_profile: ValidationProfile | None,
    strict_validation: bool | None,
) -> ValidationProfile:
    """Keep old API clients working while making new workflows explicit."""
    if validation_profile is not None:
        return validation_profile
    return "general_video" if strict_validation is False else "strict_course"


def visible_jobs(
    jobs: list[dict[str, Any]],
    lessons: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Hide historical failures once the same series part has a completed lesson."""
    completed_parts = {
        (str(lesson.get("series_id", "")), int(lesson.get("part", -1)))
        for lesson in lessons
        if lesson.get("series_id") and lesson.get("part") is not None
    }
    return [
        job for job in jobs
        if not (
            job.get("status") == "failed"
            and (str(job.get("source_id", "")), int(job.get("part", -1)))
            in completed_parts
        )
    ]


def find_download(
    source_id: str,
    part: int,
    *,
    require_subtitle: bool = False,
) -> Path | None:
    return find_reusable_download(
        PROJECT_ROOT / "downloads",
        source_id,
        part,
        require_subtitle=require_subtitle,
    )


def update_job(job_id: str, **changes: Any) -> dict[str, Any]:
    path = JOBS_DIR / f"{job_id}.json"
    with job_lock:
        job = read_json(path)
        job.update(changes)
        job["updated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
        write_json_atomic(path, job)
    return job


def next_job_artifact(job_id: str, suffix: str) -> Path:
    path = JOBS_DIR / f"{job_id}.{suffix}"
    if not path.exists():
        return path
    version = 2
    while (candidate := JOBS_DIR / f"{job_id}.v{version}.{suffix}").exists():
        version += 1
    return candidate


def resumable_record_dir(job: dict[str, Any]) -> Path | None:
    if job.get("status") != "failed" or not job.get("record_dir"):
        return None
    record_dir = Path(str(job["record_dir"])).resolve()
    records_root = (PROJECT_ROOT / "records").resolve()
    if records_root not in record_dir.parents or not record_dir.is_dir():
        return None
    # frames.json is written before model work and is the minimum safe resume
    # checkpoint. Audio-only failures (for example a native Whisper crash) must
    # restart instead of presenting a misleading resume action.
    if not (record_dir / "frames.json").is_file():
        return None
    return record_dir


def run_job(job_id: str, source_url: str, source_id: str, part: int, reuse_download: bool,
            ppt_complete: bool, validation_profile: ValidationProfile,
            resume_record_dir: str | None = None) -> None:
    try:
        ocr_primary = validation_profile == "phonetics_course"
        log_path = next_job_artifact(job_id, "workflow.log")

        def report(
            stage: str,
            stage_label: str,
            progress: int,
            details: dict[str, Any],
        ) -> None:
            update_job(
                job_id,
                stage=stage,
                stage_label=stage_label,
                progress=progress,
                **details,
            )

        analysis = run_bilibili_analysis(
            source_url,
            source_id,
            part,
            project_root=PROJECT_ROOT,
            analyzer_root=ANALYZER_ROOT,
            reuse_download=reuse_download,
            ppt_complete=ppt_complete,
            validation_profile=validation_profile,
            output=Path(resume_record_dir) if resume_record_dir else None,
            resume=bool(resume_record_dir),
            log_path=log_path,
            progress=report,
        )
        record_dir = analysis["record_dir"]

        quiz_label = (
            "依据 OCR 场景时间线生成音标练习"
            if ocr_primary
            else "生成题目并定位老师讲完的时间"
        )
        update_job(job_id, stage="quiz", stage_label=quiz_label, progress=82)
        lesson_path = build_lesson(record_dir, job_id, part, source_url,
                                   source_id=source_id,
                                   validation_profile=validation_profile)
        lesson = read_json(lesson_path)
        update_job(job_id, status="complete", stage="complete", stage_label="课程网址已就绪",
                   progress=100, lesson_id=lesson["id"], lesson_url=f"/lessons/{lesson['id']}")
    except Exception as exc:
        error_path = next_job_artifact(job_id, "error.log")
        error_path.write_text(traceback.format_exc(), encoding="utf-8")
        update_job(job_id, status="failed", stage="failed", stage_label="任务已停止",
                   error=str(exc), error_log=str(error_path))


def run_article_job(
    job_id: str,
    source_url: str,
    source_id: str,
    part: int,
    reuse_download: bool,
    resume_record_dir: str | None = None,
) -> None:
    """Generate and publish an article without invoking the lesson builder."""
    try:
        if not os.environ.get("XIAOMI_MIMO_API_KEY_TEM1") or not os.environ.get(
            "XIAOMI_MIMO_BASE_URL"
        ):
            raise RuntimeError("缺少 MiMo 环境变量，任务未开始下载")

        def report(
            stage: str,
            stage_label: str,
            progress: int,
            details: dict[str, Any],
        ) -> None:
            update_job(
                job_id,
                stage=stage,
                stage_label=stage_label,
                progress=progress,
                **details,
            )

        entry = generate_strict_article(
            source_url,
            part=part,
            output=Path(resume_record_dir) if resume_record_dir else None,
            reuse_download=reuse_download,
            resume=bool(resume_record_dir),
            article_id=job_id,
            publish_dir=ARTICLES_DIR,
            progress=report,
        )
        update_job(
            job_id,
            status="complete",
            stage="complete",
            stage_label="严格图文文章已就绪",
            progress=100,
            article_id=entry["id"],
            article_url=f"/articles/{entry['id']}",
            record_dir=entry["record_dir"],
        )
    except Exception as exc:
        error_path = next_job_artifact(job_id, "error.log")
        error_path.write_text(traceback.format_exc(), encoding="utf-8")
        update_job(
            job_id,
            status="failed",
            stage="failed",
            stage_label="文章任务已停止",
            error=str(exc),
            error_log=str(error_path),
        )


def run_download_job(job_id: str, source_url: str, source_id: str, part: int,
                     reuse_download: bool) -> None:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    try:
        source_dir = find_download(source_id, part) if reuse_download else None
        if source_dir:
            update_job(
                job_id,
                status="complete",
                stage="complete",
                stage_label="已复用现有 720p 视频与字幕",
                progress=100,
                download_dir=str(source_dir),
            )
            return
        source_dir = PROJECT_ROOT / "downloads" / f"{source_id}-p{part:02d}_{timestamp}"
        update_job(
            job_id,
            status="running",
            stage="download",
            stage_label="正在下载 720p 视频、字幕和元数据",
            progress=12,
            download_dir=str(source_dir),
        )
        download_with_yutto(source_url, source_dir, part)
        update_job(
            job_id,
            status="complete",
            stage="complete",
            stage_label="720p 下载完成",
            progress=100,
        )
    except Exception as exc:
        error_path = JOBS_DIR / f"{job_id}.error.log"
        error_path.write_text(traceback.format_exc(), encoding="utf-8")
        update_job(
            job_id,
            status="failed",
            stage="failed",
            stage_label="下载已停止",
            error=str(exc),
            error_log=str(error_path),
        )


def run_download_batch(items: list[tuple[str, str, str, int, bool]],
                       execution_mode: str) -> None:
    if execution_mode == "parallel":
        with ThreadPoolExecutor(max_workers=min(3, len(items)), thread_name_prefix="download") as pool:
            list(pool.map(lambda item: run_download_job(*item), items))
        return
    for item in items:
        run_download_job(*item)


def run_course_batch(items: list[tuple[str, str, str, int, bool, bool, ValidationProfile]],
                     execution_mode: str) -> None:
    if execution_mode == "parallel":
        with ThreadPoolExecutor(max_workers=min(3, len(items)), thread_name_prefix="course") as pool:
            list(pool.map(lambda item: run_job(*item), items))
        return
    for item in items:
        run_job(*item)


def validate_part_range(start_part: int, end_part: int) -> list[int]:
    if not 1 <= start_part <= 999 or not 1 <= end_part <= 999:
        raise HTTPException(400, "分集必须在 1 到 999 之间")
    if start_part > end_part:
        raise HTTPException(400, "起始 P 不能大于结束 P")
    parts = list(range(start_part, end_part + 1))
    if len(parts) > 30:
        raise HTTPException(400, "一次最多处理 30 个分集")
    return parts


@app.get("/api/series")
def get_series() -> dict[str, Any]:
    lesson_values = [read_json(path) for path in lesson_files()]
    lessons = [public_lesson(lesson) for lesson in lesson_values]
    articles = [public_article_entry(read_json(path)) for path in article_files()]
    job_values = [
        read_json(path)
        for path in JOBS_DIR.glob("*.json")
    ]
    jobs = sorted(
        visible_jobs(job_values, lesson_values),
        key=lambda job: str(job.get("updated_at", job.get("created_at", ""))),
        reverse=True,
    )[:20]
    for job in jobs:
        job["resume_supported"] = resumable_record_dir(job) is not None
    return {
        "title": "互动课程工坊",
        "canonical": True,
        "canonical_root": str(ROOT.resolve()),
        "data_dir": str(DATA_DIR.resolve()),
        "lessons": lessons,
        "articles": articles,
        "jobs": jobs,
    }


@app.post("/api/jobs")
def create_job(request: JobRequest) -> dict[str, Any]:
    try:
        source_url, source_id, part = parse_source(request.source, request.part)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    existing = find_existing_lesson(source_id, part)
    if existing and not request.force_rebuild:
        return {
            "status": "confirmation_required",
            "confirmation_required": True,
            "part": part,
            "existing_lesson_id": existing["id"],
            "existing_lesson_title": existing.get("title") or f"P{part} 已完成课程",
            "existing_lesson_url": f"/lessons/{existing['id']}",
            "message": f"{source_id} P{part} 已有完成课程，确认后才会重新制作。",
        }
    active_jobs = []
    for path in JOBS_DIR.glob("*.json"):
        job = read_json(path)
        if job.get("status") in {"queued", "running"}:
            active_jobs.append(job)
    duplicate = next(
        (
            job for job in active_jobs
            if job.get("source_id") == source_id and int(job.get("part", -1)) == part
        ),
        None,
    )
    if duplicate:
        raise HTTPException(409, f"P{part} 已在制造中，不能重复提交")
    if len(active_jobs) >= MAX_ACTIVE_JOBS:
        active = max(active_jobs, key=lambda job: str(job.get("updated_at", "")))
        raise HTTPException(
            409,
            f"并发制造已达到上限 {MAX_ACTIVE_JOBS}；"
            f"P{active.get('part')} 等任务完成后才能继续提交",
        )
    validation_profile = resolve_validation_profile(
        request.validation_profile,
        request.strict_validation,
    )
    job_id = f"p{part:02d}-{datetime.now():%Y%m%d-%H%M%S}"
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    job = {
        "id": job_id,
        "kind": "lesson",
        "status": "running",
        "stage": "queued",
        "stage_label": "等待开始",
        "progress": 2,
        "part": part,
        "source_id": source_id,
        "ppt_complete": request.ppt_complete and validation_profile != "phonetics_course",
        "validation_profile": validation_profile,
        "evidence_mode": "ocr_primary" if validation_profile == "phonetics_course" else "audio_visual",
        "source_url": source_url,
        "created_at": now,
        "updated_at": now,
    }
    write_json_atomic(JOBS_DIR / f"{job_id}.json", job)
    thread = threading.Thread(
        target=run_job,
        args=(job_id, source_url, source_id, part, request.reuse_download,
              request.ppt_complete, validation_profile),
        daemon=True,
        name=f"lesson-{job_id}",
    )
    thread.start()
    return job


@app.post("/api/article-jobs")
def create_article_job(request: ArticleJobRequest) -> dict[str, Any]:
    try:
        source_url, source_id, part = parse_source(request.source, request.part)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    existing = find_existing_article(source_id, part)
    if existing:
        return {
            "status": "exists",
            "part": part,
            "article_id": existing["id"],
            "article_url": f"/articles/{existing['id']}",
            "message": f"{source_id} P{part} 已有严格图文文章。",
        }
    active_jobs = []
    for path in JOBS_DIR.glob("*.json"):
        job = read_json(path)
        if job.get("status") in {"queued", "running"}:
            active_jobs.append(job)
    duplicate = next(
        (
            job
            for job in active_jobs
            if job.get("kind") == "article"
            and job.get("source_id") == source_id
            and int(job.get("part", -1)) == part
        ),
        None,
    )
    if duplicate:
        raise HTTPException(409, f"P{part} 的文章正在生成，不能重复提交")
    if len(active_jobs) >= MAX_ACTIVE_JOBS:
        raise HTTPException(409, f"并发制造已达到上限 {MAX_ACTIVE_JOBS}")
    job_id = f"article-p{part:02d}-{datetime.now():%Y%m%d-%H%M%S-%f}"
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    job = {
        "id": job_id,
        "kind": "article",
        "status": "running",
        "stage": "queued",
        "stage_label": "等待生成严格图文文章",
        "progress": 2,
        "part": part,
        "source_id": source_id,
        "source_url": source_url,
        "reuse_download": request.reuse_download,
        "ppt_complete": True,
        "validation_profile": "strict_course",
        "playable": False,
        "created_at": now,
        "updated_at": now,
    }
    write_json_atomic(JOBS_DIR / f"{job_id}.json", job)
    threading.Thread(
        target=run_article_job,
        args=(job_id, source_url, source_id, part, request.reuse_download),
        daemon=True,
        name=job_id,
    ).start()
    return job


@app.post("/api/jobs/{job_id}/resume")
def resume_job(job_id: str) -> dict[str, Any]:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", job_id):
        raise HTTPException(404, "任务不存在")
    path = JOBS_DIR / f"{job_id}.json"
    if not path.is_file():
        raise HTTPException(404, "任务不存在")
    job = read_json(path)
    record_dir = resumable_record_dir(job)
    if record_dir is None:
        raise HTTPException(409, "该失败任务没有可恢复的帧与场景，请重新制作")
    other_jobs = [
        read_json(candidate)
        for candidate in JOBS_DIR.glob("*.json")
        if candidate != path
    ]
    duplicate = next((
        value for value in other_jobs
        if value.get("status") in {"queued", "running"}
        and value.get("source_id") == job.get("source_id")
        and int(value.get("part", -1)) == int(job.get("part", -1))
    ), None)
    if duplicate:
        raise HTTPException(409, f"P{job['part']} 已在制造中，不能重复继续")
    active_count = sum(
        value.get("status") in {"queued", "running"}
        for value in other_jobs
    )
    if active_count >= MAX_ACTIVE_JOBS:
        raise HTTPException(409, f"并发制造已达到上限 {MAX_ACTIVE_JOBS}")

    resumed = update_job(
        job_id,
        status="running",
        stage="queued",
        stage_label="从保留现场继续",
        progress=max(2, min(int(job.get("progress", 2)), 82)),
        error=None,
        previous_error=job.get("error"),
        previous_error_log=job.get("error_log"),
    )
    if job.get("kind") == "article":
        target = run_article_job
        args = (
            job_id,
            str(job["source_url"]),
            str(job["source_id"]),
            int(job["part"]),
            True,
            str(record_dir),
        )
    else:
        validation_profile = resolve_validation_profile(
            job.get("validation_profile"),
            None,
        )
        target = run_job
        args = (
            job_id,
            str(job["source_url"]),
            str(job["source_id"]),
            int(job["part"]),
            True,
            bool(job.get("ppt_complete", True)),
            validation_profile,
            str(record_dir),
        )
    thread = threading.Thread(
        target=target,
        args=args,
        daemon=True,
        name=f"resume-{job_id}",
    )
    thread.start()
    return resumed


@app.post("/api/download-batches")
def create_download_batch(request: DownloadBatchRequest) -> dict[str, Any]:
    parts = validate_part_range(request.start_part, request.end_part)
    try:
        _, source_id, _ = parse_source(request.source, request.start_part)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    active_parts = {
        int(job["part"])
        for path in JOBS_DIR.glob("*.json")
        if (job := read_json(path)).get("kind") == "download"
        and job.get("source_id") == source_id
        and job.get("status") in {"queued", "running"}
    }
    duplicates = sorted(active_parts.intersection(parts))
    if duplicates:
        raise HTTPException(
            409,
            f"{'、'.join(f'P{part}' for part in duplicates)} 已有下载任务正在运行",
        )

    batch_id = f"download-{datetime.now():%Y%m%d-%H%M%S-%f}"
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    jobs = []
    items = []
    for part in parts:
        source_url, _, _ = parse_source(request.source, part)
        job_id = f"{batch_id}-p{part:03d}"
        job = {
            "id": job_id,
            "batch_id": batch_id,
            "kind": "download",
            "status": "queued",
            "stage": "queued",
            "stage_label": "等待下载（固定 720p）",
            "progress": 2,
            "part": part,
            "source_id": source_id,
            "source_url": source_url,
            "execution_mode": request.execution_mode,
            "quality": "720p",
            "created_at": now,
            "updated_at": now,
        }
        write_json_atomic(JOBS_DIR / f"{job_id}.json", job)
        jobs.append(job)
        items.append((job_id, source_url, source_id, part, request.reuse_download))
    threading.Thread(
        target=run_download_batch,
        args=(items, request.execution_mode),
        daemon=True,
        name=batch_id,
    ).start()
    return {
        "batch_id": batch_id,
        "status": "running",
        "execution_mode": request.execution_mode,
        "quality": "720p",
        "parts": parts,
        "jobs": jobs,
    }


@app.post("/api/course-batches")
def create_course_batch(request: CourseBatchRequest) -> dict[str, Any]:
    parts = validate_part_range(request.start_part, request.end_part)
    try:
        _, source_id, _ = parse_source(request.source, request.start_part)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    active_parts = {
        int(job["part"])
        for path in JOBS_DIR.glob("*.json")
        if (job := read_json(path)).get("source_id") == source_id
        and job.get("status") in {"queued", "running"}
    }
    duplicates = sorted(active_parts.intersection(parts))
    if duplicates:
        raise HTTPException(
            409,
            f"{'、'.join(f'P{part}' for part in duplicates)} 已有任务正在运行",
        )

    validation_profile = resolve_validation_profile(
        request.validation_profile,
        request.strict_validation,
    )
    skipped_parts = [
        part for part in parts
        if find_existing_lesson(source_id, part) is not None
    ]
    pending_parts = [part for part in parts if part not in skipped_parts]
    batch_id = f"course-{datetime.now():%Y%m%d-%H%M%S-%f}"
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    jobs = []
    items = []
    for part in pending_parts:
        source_url, _, _ = parse_source(request.source, part)
        job_id = f"{batch_id}-p{part:03d}"
        job = {
            "id": job_id,
            "batch_id": batch_id,
            "kind": "lesson",
            "status": "queued",
            "stage": "queued",
            "stage_label": "等待批量课程制作",
            "progress": 2,
            "part": part,
            "source_id": source_id,
            "source_url": source_url,
            "execution_mode": request.execution_mode,
            "ppt_complete": request.ppt_complete and validation_profile != "phonetics_course",
            "validation_profile": validation_profile,
            "evidence_mode": "ocr_primary" if validation_profile == "phonetics_course" else "audio_visual",
            "created_at": now,
            "updated_at": now,
        }
        write_json_atomic(JOBS_DIR / f"{job_id}.json", job)
        jobs.append(job)
        items.append((
            job_id,
            source_url,
            source_id,
            part,
            request.reuse_download,
            request.ppt_complete,
            validation_profile,
        ))
    if items:
        threading.Thread(
            target=run_course_batch,
            args=(items, request.execution_mode),
            daemon=True,
            name=batch_id,
        ).start()
    return {
        "batch_id": batch_id,
        "status": "running" if items else "complete",
        "execution_mode": request.execution_mode,
        "parts": pending_parts,
        "skipped_parts": skipped_parts,
        "jobs": jobs,
    }


@app.get("/api/home-layout")
def get_home_layout() -> dict[str, Any]:
    if not HOME_LAYOUT_PATH.is_file():
        return normalized_home_layout({})
    return normalized_home_layout(read_json(HOME_LAYOUT_PATH))


@app.put("/api/home-layout")
def update_home_layout(request: HomeLayoutRequest) -> dict[str, Any]:
    layout = normalized_home_layout(request.model_dump())
    write_json_atomic(HOME_LAYOUT_PATH, layout)
    return layout


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict[str, Any]:
    path = JOBS_DIR / f"{job_id}.json"
    if not path.is_file():
        raise HTTPException(404, "任务不存在")
    return read_json(path)


@app.get("/api/lessons/{lesson_id}")
def get_lesson(lesson_id: str) -> dict[str, Any]:
    return public_lesson(load_lesson(lesson_id))


@app.get("/api/lessons/{lesson_id}/article", response_class=PlainTextResponse)
def get_article(lesson_id: str) -> str:
    lesson = load_lesson(lesson_id)
    path = Path(lesson["article_path"])
    if not path.is_file():
        raise HTTPException(404, "教案文件不存在")
    return path.read_text(encoding="utf-8")


@app.get("/api/lessons/{lesson_id}/articles")
def get_articles(lesson_id: str) -> dict[str, Any]:
    lesson = load_lesson(lesson_id)
    files = article_files_for(lesson)
    return {
        "default": "article.md" if any(path.name == "article.md" for path in files) else (files[0].name if files else None),
        "files": [{
            "name": path.name,
            "kind": article_kind(path.name),
            "size": path.stat().st_size,
            "modified_at": datetime.fromtimestamp(path.stat().st_mtime).astimezone().isoformat(timespec="seconds"),
        } for path in files],
    }


@app.get("/api/lessons/{lesson_id}/articles/{filename}")
def get_article_blocks(lesson_id: str, filename: str) -> dict[str, Any]:
    lesson = load_lesson(lesson_id)
    return article_blocks(lesson, filename)


@app.get("/api/lessons/{lesson_id}/assets/{asset_path:path}")
def get_lesson_asset(lesson_id: str, asset_path: str) -> FileResponse:
    lesson = load_lesson(lesson_id)
    record_dir = record_dir_for(lesson)
    path = (record_dir / asset_path.replace("/", os.sep)).resolve()
    if record_dir not in path.parents or not path.is_file():
        raise HTTPException(404, "课程图片不存在")
    return FileResponse(path)


@app.get("/api/articles/{article_id}")
def get_standalone_article(article_id: str) -> dict[str, Any]:
    article = load_article_entry(article_id)
    value = public_article_entry(article)
    value["content_url"] = f"/api/articles/{article_id}/content"
    return value


@app.get("/api/articles/{article_id}/content")
def get_standalone_article_content(article_id: str) -> dict[str, Any]:
    article = load_article_entry(article_id)
    return article_blocks(
        article, "article.md", collection="articles", asset_route="files"
    )


@app.get("/api/articles/{article_id}/files/{asset_path:path}")
def get_standalone_article_asset(
    article_id: str, asset_path: str
) -> FileResponse:
    article = load_article_entry(article_id)
    record_dir = record_dir_for(article)
    path = (record_dir / asset_path.replace("/", os.sep)).resolve()
    if record_dir not in path.parents or not path.is_file():
        raise HTTPException(404, "文章图片不存在")
    return FileResponse(path)


@app.get("/media/{lesson_id}")
def get_media(lesson_id: str) -> FileResponse:
    lesson = load_lesson(lesson_id)
    path = Path(lesson["video_path"])
    if not path.is_file():
        raise HTTPException(404, "视频文件不存在")
    return FileResponse(path, media_type="video/mp4")


@app.get("/subtitles/{lesson_id}.vtt")
def get_subtitles(lesson_id: str) -> Response:
    lesson = load_lesson(lesson_id)
    path = subtitle_path_for(lesson)
    source = "downloaded_subtitle"
    if path:
        text = path.read_text(encoding="utf-8-sig")
        if path.suffix.lower() == ".srt":
            text = srt_to_vtt(text)
        elif not text.lstrip().startswith("WEBVTT"):
            text = "WEBVTT\n\n" + text
    else:
        transcript_path = transcript_path_for(lesson)
        if not transcript_path:
            raise HTTPException(404, "字幕和本地转写均不存在")
        text = transcript_to_vtt(read_json(transcript_path))
        source = "generated_from_transcript"
    return Response(
        content=text,
        media_type="text/vtt; charset=utf-8",
        headers={
            "Cache-Control": "no-cache",
            "X-Subtitle-Source": source,
        },
    )


@app.get("/static/{asset_path:path}")
def static_asset(asset_path: str) -> FileResponse:
    path = (STATIC_DIR / asset_path).resolve()
    if STATIC_DIR.resolve() not in path.parents or not path.is_file():
        raise HTTPException(404)
    return FileResponse(path)


@app.get("/{path:path}", response_class=HTMLResponse)
def frontend(path: str = "") -> str:
    return (STATIC_DIR / "index.html").read_text(encoding="utf-8")
