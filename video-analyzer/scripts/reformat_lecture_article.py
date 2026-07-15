"""Generate a validated structured lecture document and render it to Markdown."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from video_analyzer.analyzer import VideoAnalyzer
from video_analyzer.artifacts import render_article
from video_analyzer.clients.generic_openai_api import GenericOpenAIAPIClient
from video_analyzer.lecture_document import (
    render_lecture_document,
)
from video_analyzer.lecture_formatter import format_lecture_document
from video_analyzer.prompt import PromptLoader

PROMPTS = [
    {"name": "Frame Record", "path": "frame_analysis/frame_analysis.txt"},
    {"name": "Chronological Record", "path": "frame_analysis/record.txt"},
]


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def next_version(path: Path) -> Path:
    version = 1
    while path.with_name(f"{path.stem}.v{version}{path.suffix}").exists():
        version += 1
    return path.with_name(f"{path.stem}.v{version}{path.suffix}")


def main() -> None:
    parser = argparse.ArgumentParser(description="结构化重排课程文章并验证后渲染Markdown")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--draft", type=Path)
    parser.add_argument("--model", default="mimo-v2.5")
    parser.add_argument("--api-key-env", default="XIAOMI_MIMO_API_KEY_TEM1")
    parser.add_argument("--base-url-env", default="XIAOMI_MIMO_BASE_URL")
    args = parser.parse_args()

    output = args.output.resolve()
    draft_path = args.draft or (output / "article.source.v1.md")
    draft = draft_path.read_text(encoding="utf-8")
    record = read_json(output / "record.json")
    expected_frames = [
        int(scene["frame_number"]) for scene in record.get("scenes", [])
        if scene.get("relevance", "content") == "content"
    ]

    client = GenericOpenAIAPIClient(
        os.environ[args.api_key_env], os.environ[args.base_url_env],
        max_retries=1, api_key_header="api-key",
    )
    analyzer = VideoAnalyzer(client, args.model, PromptLoader("", PROMPTS), 0.0)
    try:
        document, validation = format_lecture_document(
            analyzer, draft, expected_frames, output, group_size=1
        )
    finally:
        write_json(output / "api_calls.article_formatting.json", analyzer.call_log)

    markdown = render_lecture_document(document)
    article_path = output / "article.md"
    source_path = output / "article.source.md"
    if article_path.exists():
        shutil.copy2(article_path, next_version(article_path))
    if source_path.exists():
        shutil.copy2(source_path, next_version(source_path))
    write_json(output / "article.document.json", document)
    source_path.write_text(markdown, encoding="utf-8")
    render_article(markdown, record.get("scenes", []), output, max_images=None)
    print(article_path)


if __name__ == "__main__":
    main()
