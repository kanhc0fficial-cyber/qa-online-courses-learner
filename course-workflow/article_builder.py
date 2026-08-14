"""Build strict illustrated Markdown articles without creating playable lessons."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
ANALYZER_ROOT = PROJECT_ROOT / "video-analyzer"
from analysis_runner import parse_bilibili_source, run_bilibili_analysis

ProgressCallback = Callable[[str, str, int, dict[str, Any]], None]


def validate_illustrated_article(record_dir: Path) -> dict[str, Any]:
    """Require a completed strict record and at least one valid local image."""
    record_dir = record_dir.resolve()
    article_path = record_dir / "article.md"
    record_path = record_dir / "record.json"
    state_path = record_dir / "run_state.json"
    for path in (article_path, record_path, state_path):
        if not path.is_file():
            raise RuntimeError(f"严格文章产物缺失：{path.name}")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    if state.get("status") != "complete":
        raise RuntimeError("严格文章分析尚未完成")
    record = json.loads(record_path.read_text(encoding="utf-8"))
    metadata = record.get("metadata", {})
    if metadata.get("validation_profile") != "strict_course":
        raise RuntimeError("文章不是由 strict_course 校验包生成")
    markdown = article_path.read_text(encoding="utf-8")
    image_targets = re.findall(r"!\[[^\]]*\]\(([^)\s]+)", markdown)
    local_images = []
    for target in image_targets:
        if target.startswith(("http://", "https://", "data:")):
            continue
        candidate = (record_dir / target.replace("/", os.sep)).resolve()
        if record_dir in candidate.parents and candidate.is_file():
            local_images.append(candidate)
    if not local_images:
        raise RuntimeError("article.md 没有可读取的本地图片，不能发布")
    return {
        "article_path": article_path,
        "record": record,
        "image_count": len(local_images),
    }


def build_article_entry(
    record_dir: Path,
    article_id: str,
    source_url: str,
    source_id: str,
    part: int,
) -> dict[str, Any]:
    """Create frontend metadata while deliberately omitting all playback fields."""
    validated = validate_illustrated_article(record_dir)
    metadata = validated["record"].get("metadata", {})
    source_metadata = metadata.get("metadata", {})
    scenes = validated["record"].get("scenes", [])
    title = str(source_metadata.get("title") or f"{source_id} P{part} 严格图文记录")
    return {
        "id": article_id,
        "kind": "strict_illustrated_article",
        "title": title,
        "series_id": source_id,
        "part": part,
        "source_url": source_url,
        "record_dir": str(record_dir.resolve()),
        "article_path": str(validated["article_path"]),
        "image_count": validated["image_count"],
        "section_count": len(scenes),
        "duration": float(metadata.get("duration", 0.0)),
        "validation_profile": "strict_course",
        "playable": False,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }


def generate_strict_article(
    source: str,
    *,
    part: int | None = None,
    output: Path | None = None,
    reuse_download: bool = True,
    resume: bool = False,
    article_id: str | None = None,
    publish_dir: Path | None = None,
    model: str = "mimo-v2.5",
    api_key_env: str = "XIAOMI_MIMO_API_KEY_TEM1",
    base_url_env: str = "XIAOMI_MIMO_BASE_URL",
    yutto_proxy: str = "no",
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    """Run the canonical strict article pipeline for UI and headless callers."""
    source_url, source_id, resolved_part = parse_bilibili_source(source, part)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    analysis = run_bilibili_analysis(
        source_url,
        source_id,
        resolved_part,
        project_root=PROJECT_ROOT,
        analyzer_root=ANALYZER_ROOT,
        reuse_download=reuse_download,
        ppt_complete=True,
        validation_profile="strict_course",
        output=output,
        resume=resume,
        model=model,
        api_key_env=api_key_env,
        base_url_env=base_url_env,
        yutto_proxy=yutto_proxy,
        progress=progress,
    )
    record_dir = analysis["record_dir"]
    entry = build_article_entry(
        record_dir,
        article_id or f"{source_id}-p{resolved_part:02d}-{timestamp}",
        source_url,
        source_id,
        resolved_part,
    )
    if publish_dir is not None:
        publish_dir.mkdir(parents=True, exist_ok=True)
        path = publish_dir / f"{entry['id']}.json"
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(entry, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.replace(path)
        entry["index_path"] = str(path)
    if progress:
        progress("complete", "严格图文文章已就绪", 100, {"article_id": entry["id"]})
    return entry
