"""Regenerate only the lecture article from preserved scenes and transcript."""

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
from video_analyzer.audio_processor import AudioTranscript
from video_analyzer.clients.generic_openai_api import GenericOpenAIAPIClient
from video_analyzer.prompt import PromptLoader

PROMPTS = [
    {"name": "Frame Record", "path": "frame_analysis/frame_analysis.txt"},
    {"name": "Chronological Record", "path": "frame_analysis/record.txt"},
]


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def preserve_version(path: Path) -> Path | None:
    if not path.exists():
        return None
    version = 1
    while path.with_name(f"{path.stem}.v{version}{path.suffix}").exists():
        version += 1
    destination = path.with_name(f"{path.stem}.v{version}{path.suffix}")
    shutil.copy2(path, destination)
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(description="仅重生成课程文章，不重复视觉调用")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", default="mimo-v2.5")
    parser.add_argument("--api-key-env", default="XIAOMI_MIMO_API_KEY_TEM1")
    parser.add_argument("--base-url-env", default="XIAOMI_MIMO_BASE_URL")
    args = parser.parse_args()

    output = args.output.resolve()
    record = read_json(output / "record.json")
    transcript_data = read_json(output / "transcript.json")
    transcript = AudioTranscript(
        transcript_data.get("text", ""),
        transcript_data.get("segments", []),
        transcript_data.get("language", "zh"),
    )
    manifest = record.get("metadata", {})
    source_context = manifest.get("metadata", manifest)

    old_article = preserve_version(output / "article.md")
    old_source = preserve_version(output / "article.source.md")

    client = GenericOpenAIAPIClient(
        os.environ[args.api_key_env],
        os.environ[args.base_url_env],
        max_retries=1,
        api_key_header="api-key",
    )
    analyzer = VideoAnalyzer(client, args.model, PromptLoader("", PROMPTS), 0.0)
    article = analyzer.compose_lecture_article(record.get("scenes", []), transcript, source_context)
    if article.startswith("# 课程记录生成失败"):
        raise RuntimeError(article)

    (output / "article.source.md").write_text(article + "\n", encoding="utf-8")
    render_article(article, record.get("scenes", []), output, max_images=None)
    write_json(output / "api_calls.article_regeneration.json", analyzer.call_log)

    state_path = output / "run_state.json"
    state = read_json(state_path) if state_path.exists() else {}
    state.update({
        "status": "complete",
        "article_regenerated": True,
        "previous_article": old_article.name if old_article else None,
        "previous_article_source": old_source.name if old_source else None,
        "article_regeneration_api_calls": len(analyzer.call_log),
    })
    write_json(state_path, state)
    print(output / "article.md")


if __name__ == "__main__":
    main()
