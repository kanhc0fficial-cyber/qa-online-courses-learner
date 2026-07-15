"""Local web workflow for Bilibili videos, with optional course strictness."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlparse

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, Response
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
ANALYZER_ROOT = PROJECT_ROOT / "video-analyzer"
if str(ANALYZER_ROOT) not in sys.path:
    sys.path.insert(0, str(ANALYZER_ROOT))

from lesson_builder import build_lesson
from video_analyzer.bilibili import download_with_yutto

DATA_DIR = ROOT / "data"
LESSONS_DIR = DATA_DIR / "lessons"
JOBS_DIR = DATA_DIR / "jobs"
STATIC_DIR = ROOT / "static"
for directory in (LESSONS_DIR, JOBS_DIR):
    directory.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="电力电子互动课程工坊")
job_lock = threading.Lock()


class JobRequest(BaseModel):
    source: str
    part: int | None = None
    reuse_download: bool = True
    force_rebuild: bool = False
    ppt_complete: bool = True
    strict_validation: bool = True


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def parse_source(source: str, explicit_part: int | None = None) -> tuple[str, str, int]:
    text = source.strip()
    if "://" in text:
        parsed = urlparse(text)
        host = (parsed.hostname or "").lower()
        if host not in {"bilibili.com", "www.bilibili.com"}:
            raise ValueError("只接受 bilibili.com 的分集链接")
        if not re.fullmatch(r"/video/BV[0-9A-Za-z]+/?", parsed.path):
            raise ValueError("请输入有效的 B 站视频链接")
    else:
        parsed = None
    match = re.search(r"BV[0-9A-Za-z]+", text)
    if not match:
        raise ValueError("请输入 BV 号或 bilibili.com 视频链接")
    source_id = match.group(0)
    part = explicit_part
    if part is None and parsed is not None:
        values = parse_qs(parsed.query).get("p", [])
        if values and values[0].isdigit():
            part = int(values[0])
    if part is None:
        part_match = re.search(r"(?:^|[-_])p(\d+)(?:$|[-_])", text, re.I)
        if part_match:
            part = int(part_match.group(1))
    part = part or 1
    if not 1 <= part <= 999:
        raise ValueError("分集必须在 1 到 999 之间")
    return f"https://www.bilibili.com/video/{source_id}?p={part}", source_id, part


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
        f"/subtitles/{lesson['id']}.vtt" if subtitle_path_for(lesson) else None
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


def rewrite_article_images(markdown: str, lesson_id: str, record_dir: Path) -> str:
    image_pattern = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)(?:\s+['\"][^)]*['\"])?\)")
    def replace(match: re.Match[str]) -> str:
        alt, target = match.group(1), match.group(2)
        if target.startswith(("http://", "https://", "data:")):
            return match.group(0)
        candidate = (record_dir / target.replace("/", os.sep)).resolve()
        if record_dir not in candidate.parents or not candidate.is_file():
            return f"![{alt}](missing-image)"
        relative = candidate.relative_to(record_dir).as_posix()
        return f"![{alt}](/api/lessons/{quote(lesson_id, safe='')}/assets/{quote(relative, safe='/')})"
    return image_pattern.sub(replace, markdown)


def article_blocks(lesson: dict[str, Any], filename: str) -> dict[str, Any]:
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
        markdown = rewrite_article_images("\n".join(lines), lesson["id"], record_dir)
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
        markdown = rewrite_article_images("\n".join(chunk_lines).strip(), lesson["id"], record_dir)
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


def find_download(source_id: str, part: int) -> Path | None:
    pattern = re.compile(rf"{re.escape(source_id)}-p0*{part}(?:\D|$)", re.I)
    candidates = []
    for directory in (PROJECT_ROOT / "downloads").glob("*"):
        if directory.is_dir() and pattern.search(directory.name):
            if list(directory.rglob("*.mp4")) and list(directory.rglob("*.srt")):
                candidates.append(directory)
    return max(candidates, key=lambda path: path.stat().st_mtime) if candidates else None


def update_job(job_id: str, **changes: Any) -> dict[str, Any]:
    path = JOBS_DIR / f"{job_id}.json"
    with job_lock:
        job = read_json(path)
        job.update(changes)
        job["updated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
        write_json_atomic(path, job)
    return job


def run_job(job_id: str, source_url: str, source_id: str, part: int, reuse_download: bool,
            ppt_complete: bool, strict_validation: bool) -> None:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    try:
        if not os.environ.get("XIAOMI_MIMO_API_KEY_TEM1") or not os.environ.get("XIAOMI_MIMO_BASE_URL"):
            raise RuntimeError("缺少 MiMo 环境变量，任务未开始下载")
        source_dir = find_download(source_id, part) if reuse_download else None
        if source_dir:
            update_job(job_id, stage="download", stage_label="复用已下载视频与字幕", progress=18,
                       download_dir=str(source_dir))
        else:
            source_dir = PROJECT_ROOT / "downloads" / f"{source_id}-p{part:02d}_{timestamp}"
            update_job(job_id, stage="download", stage_label="下载视频、字幕和元数据", progress=8,
                       download_dir=str(source_dir))
            download_with_yutto(source_url, source_dir, part)

        mode = "ppt_complete" if ppt_complete else "general"
        record_dir = PROJECT_ROOT / "records" / f"{source_id}-p{part:02d}_{mode}_mimo-v2.5_tem1_{timestamp}"
        log_path = JOBS_DIR / f"{job_id}.workflow.log"
        update_job(job_id, stage="analysis", stage_label="识别 PPT、理解字幕并生成教案", progress=28,
                   record_dir=str(record_dir), log_path=str(log_path))
        command = [
            sys.executable,
            str(ANALYZER_ROOT / "scripts" / "bilibili_workflow.py"),
            str(source_dir),
            "--part", str(part),
            "--output", str(record_dir),
            "--model", "mimo-v2.5",
            "--api-key-env", "XIAOMI_MIMO_API_KEY_TEM1",
            "--validation-profile", "strict_course" if strict_validation else "general_video",
        ]
        if ppt_complete:
            command += ["--ppt-complete", "--ppt-ignore-head", "18", "--ppt-ignore-tail", "10"]
        environment = dict(os.environ)
        environment["PYTHONUTF8"] = "1"
        environment["PYTHONIOENCODING"] = "utf-8"
        with log_path.open("w", encoding="utf-8") as log:
            subprocess.run(
                command,
                cwd=ANALYZER_ROOT,
                env=environment,
                stdout=log,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                check=True,
            )

        update_job(job_id, stage="quiz", stage_label="生成题目并定位老师讲完的时间", progress=82)
        lesson_path = build_lesson(record_dir, job_id, part, source_url,
                                   source_id=source_id, strict_validation=strict_validation)
        lesson = read_json(lesson_path)
        update_job(job_id, status="complete", stage="complete", stage_label="课程网址已就绪",
                   progress=100, lesson_id=lesson["id"], lesson_url=f"/lessons/{lesson['id']}")
    except Exception as exc:
        error_path = JOBS_DIR / f"{job_id}.error.log"
        error_path.write_text(traceback.format_exc(), encoding="utf-8")
        update_job(job_id, status="failed", stage="failed", stage_label="任务已停止",
                   error=str(exc), error_log=str(error_path))


@app.get("/api/series")
def get_series() -> dict[str, Any]:
    lessons = [public_lesson(read_json(path)) for path in lesson_files()]
    jobs = [read_json(path) for path in sorted(JOBS_DIR.glob("*.json"), reverse=True)[:20]]
    return {
        "title": "互动课程工坊",
        "lessons": lessons,
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
    if active_jobs:
        active = max(active_jobs, key=lambda job: str(job.get("updated_at", "")))
        raise HTTPException(409, f"当前正在制作 P{active.get('part')}，完成后才能提交下一集")
    job_id = f"p{part:02d}-{datetime.now():%Y%m%d-%H%M%S}"
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    job = {
        "id": job_id,
        "status": "running",
        "stage": "queued",
        "stage_label": "等待开始",
        "progress": 2,
        "part": part,
        "source_id": source_id,
        "ppt_complete": request.ppt_complete,
        "validation_profile": "strict_course" if request.strict_validation else "general_video",
        "source_url": source_url,
        "created_at": now,
        "updated_at": now,
    }
    write_json_atomic(JOBS_DIR / f"{job_id}.json", job)
    thread = threading.Thread(
        target=run_job,
        args=(job_id, source_url, source_id, part, request.reuse_download,
              request.ppt_complete, request.strict_validation),
        daemon=True,
        name=f"lesson-{job_id}",
    )
    thread.start()
    return job


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
    if not path:
        raise HTTPException(404, "中文字幕不存在")
    text = path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() == ".srt":
        text = srt_to_vtt(text)
    elif not text.lstrip().startswith("WEBVTT"):
        text = "WEBVTT\n\n" + text
    return Response(content=text, media_type="text/vtt; charset=utf-8", headers={"Cache-Control": "no-cache"})


@app.get("/static/{asset_path:path}")
def static_asset(asset_path: str) -> FileResponse:
    path = (STATIC_DIR / asset_path).resolve()
    if STATIC_DIR.resolve() not in path.parents or not path.is_file():
        raise HTTPException(404)
    return FileResponse(path)


@app.get("/{path:path}", response_class=HTMLResponse)
def frontend(path: str = "") -> str:
    return (STATIC_DIR / "index.html").read_text(encoding="utf-8")
