"""Headless entry point for the canonical strict illustrated-article route."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

WORKFLOW_ROOT = Path(__file__).resolve().parents[1]
if str(WORKFLOW_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKFLOW_ROOT))

from article_builder import generate_strict_article


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "复用规范视频分析管线，生成严格、配图且不进入播放器课程的 Markdown 文章。"
        )
    )
    parser.add_argument("source", help="B站 BV 号或分集链接")
    parser.add_argument("--part", type=int, help="分集 P；链接含 p 参数时可省略")
    parser.add_argument("--output", type=Path, help="可选的记录输出目录")
    parser.add_argument(
        "--no-reuse-download",
        action="store_true",
        help="不复用已有下载，重新下载最高 720p 资源",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="从 --output 中保留的 frames/scenes/草稿继续",
    )
    parser.add_argument(
        "--no-publish",
        action="store_true",
        help="只生成记录，不写入前端 data/articles 索引",
    )
    parser.add_argument("--model", default="mimo-v2.5")
    parser.add_argument(
        "--proxy",
        default="no",
        help="仅控制本次 yutto 的代理；默认 no，不读取系统代理",
    )
    args = parser.parse_args()

    if args.resume and args.output is None:
        parser.error("--resume 必须同时提供 --output")
    if (
        args.output is not None
        and args.output.exists()
        and any(args.output.iterdir())
        and not args.resume
    ):
        parser.error("--output 已非空；为保护产物，请改用新目录或显式 --resume")

    publish_dir = None if args.no_publish else WORKFLOW_ROOT / "data" / "articles"

    def report(stage: str, label: str, progress: int, details: dict) -> None:
        print(
            json.dumps(
                {
                    "event": "progress",
                    "stage": stage,
                    "label": label,
                    "progress": progress,
                    **details,
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
            flush=True,
        )

    result = generate_strict_article(
        args.source,
        part=args.part,
        output=args.output,
        reuse_download=not args.no_reuse_download,
        resume=args.resume,
        publish_dir=publish_dir,
        model=args.model,
        yutto_proxy=args.proxy,
        progress=report,
    )
    print(json.dumps(result, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
