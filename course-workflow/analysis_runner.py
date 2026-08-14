"""Shared orchestration for the canonical Bilibili analysis pipeline."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Literal
from urllib.parse import parse_qs, urlparse

ValidationProfile = Literal["strict_course", "general_video", "phonetics_course"]
ProgressCallback = Callable[[str, str, int, dict[str, Any]], None]


def parse_bilibili_source(
    source: str, explicit_part: int | None = None
) -> tuple[str, str, int]:
    text = source.strip()
    parsed = urlparse(text) if "://" in text else None
    if parsed is not None:
        host = (parsed.hostname or "").lower()
        if host not in {"bilibili.com", "www.bilibili.com"}:
            raise ValueError("只接受 bilibili.com 的分集链接")
        if not re.fullmatch(r"/video/BV[0-9A-Za-z]+/?", parsed.path):
            raise ValueError("请输入有效的 B 站视频链接")
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


def find_reusable_download(
    downloads_root: Path,
    source_id: str,
    part: int,
    *,
    require_subtitle: bool = False,
) -> Path | None:
    pattern = re.compile(rf"{re.escape(source_id)}-p0*{part}(?:\D|$)", re.I)
    candidates = []
    for directory in downloads_root.glob("*"):
        if not directory.is_dir() or not pattern.search(directory.name):
            continue
        has_video = any(directory.rglob("*.mp4"))
        has_subtitle = any(directory.rglob("*.srt"))
        if has_video and (has_subtitle or not require_subtitle):
            candidates.append(directory)
    return max(candidates, key=lambda path: path.stat().st_mtime) if candidates else None


def run_bilibili_analysis(
    source_url: str,
    source_id: str,
    part: int,
    *,
    project_root: Path,
    analyzer_root: Path,
    reuse_download: bool,
    ppt_complete: bool,
    validation_profile: ValidationProfile,
    output: Path | None = None,
    resume: bool = False,
    log_path: Path | None = None,
    model: str = "mimo-v2.5",
    api_key_env: str = "XIAOMI_MIMO_API_KEY_TEM1",
    base_url_env: str = "XIAOMI_MIMO_BASE_URL",
    yutto_proxy: str = "no",
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    """Download/reuse assets and run the existing audited analyzer unchanged."""
    if not os.environ.get(api_key_env) or not os.environ.get(base_url_env):
        raise RuntimeError("缺少 MiMo 环境变量，任务未开始下载")
    if str(analyzer_root) not in sys.path:
        sys.path.insert(0, str(analyzer_root))
    from video_analyzer.bilibili import download_with_yutto

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    downloads_root = project_root / "downloads"
    source_dir = (
        find_reusable_download(downloads_root, source_id, part)
        if reuse_download
        else None
    )
    if source_dir is None:
        source_dir = downloads_root / f"{source_id}-p{part:02d}_{timestamp}"
        if progress:
            progress(
                "download",
                "下载视频、字幕和元数据（最高 720p）",
                8,
                {"download_dir": str(source_dir)},
            )
        download_with_yutto(source_url, source_dir, part, proxy=yutto_proxy)
    elif progress:
        progress(
            "download",
            "复用已下载视频与字幕",
            18,
            {"download_dir": str(source_dir)},
        )

    ocr_primary = validation_profile == "phonetics_course"
    mode = (
        "phonetics_ocr"
        if ocr_primary
        else ("ppt_complete" if ppt_complete else "general")
    )
    record_dir = (
        output.resolve()
        if output
        else project_root
        / "records"
        / f"{source_id}-p{part:02d}_{mode}_{model}_tem1_{timestamp}"
    )
    record_dir.mkdir(parents=True, exist_ok=True)
    workflow_log = log_path or record_dir / "workflow.log"
    analysis_label = (
        "密集抽帧并以 OCR 识别音标、中英文字与例词"
        if ocr_primary
        else "识别 PPT、理解字幕并生成严格图文记录"
    )
    if progress:
        progress(
            "analysis",
            analysis_label,
            28,
            {"record_dir": str(record_dir), "log_path": str(workflow_log)},
        )

    command = [
        sys.executable,
        str(analyzer_root / "scripts" / "bilibili_workflow.py"),
        str(source_dir),
        "--part",
        str(part),
        "--output",
        str(record_dir),
        "--model",
        model,
        "--api-key-env",
        api_key_env,
        "--base-url-env",
        base_url_env,
        "--validation-profile",
        validation_profile,
    ]
    if ppt_complete and not ocr_primary:
        command += [
            "--ppt-complete",
            "--ppt-ignore-head",
            "18",
            "--ppt-ignore-tail",
            "10",
        ]
    if resume:
        command.append("--resume")

    environment = dict(os.environ)
    environment["PYTHONUTF8"] = "1"
    environment["PYTHONIOENCODING"] = "utf-8"
    environment.setdefault("WHISPER_MODEL", "medium")
    environment.setdefault("WHISPER_BACKEND", "openai")
    environment.setdefault("HF_HUB_DISABLE_XET", "1")
    environment.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

    state_path = record_dir / "run_state.json"
    completed_analysis = False
    if resume and state_path.is_file():
        state = json.loads(state_path.read_text(encoding="utf-8"))
        completed_analysis = state.get("status") == "complete"
    if completed_analysis:
        workflow_log.write_text(
            "Reused completed analyzer artifacts; continuing to publication.\n",
            encoding="utf-8",
        )
        if progress:
            progress(
                "analysis",
                "复用已完成的记录与文章",
                78,
                {"record_dir": str(record_dir), "log_path": str(workflow_log)},
            )
    else:
        with workflow_log.open("a" if resume else "w", encoding="utf-8") as log:
            subprocess.run(
                command,
                cwd=analyzer_root,
                env=environment,
                stdout=log,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                check=True,
            )
    return {
        "source_dir": source_dir,
        "record_dir": record_dir,
        "log_path": workflow_log,
        "completed_analysis_reused": completed_analysis,
        "command": command,
    }
