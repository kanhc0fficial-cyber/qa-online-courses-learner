import json
from pathlib import Path

import pytest

import analysis_runner
import article_builder
import server


def _write_complete_article_record(record_dir: Path) -> None:
    frame = record_dir / "assets" / "frames" / "slide_001.jpg"
    frame.parent.mkdir(parents=True)
    frame.write_bytes(b"jpeg")
    (record_dir / "article.md").write_text(
        "# 标题\n\n![关键画面](assets/frames/slide_001.jpg)\n",
        encoding="utf-8",
    )
    (record_dir / "record.json").write_text(
        json.dumps(
            {
                "metadata": {
                    "validation_profile": "strict_course",
                    "duration": 60,
                    "metadata": {"title": "严格图文测试"},
                },
                "scenes": [{"frame_number": 1, "timestamp": 3}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (record_dir / "run_state.json").write_text(
        json.dumps({"status": "complete"}), encoding="utf-8"
    )


def test_shared_runner_preserves_strict_ppt_parameters(monkeypatch, tmp_path):
    source_dir = tmp_path / "downloads" / "BV1ab411c7xY-p01-existing"
    source_dir.mkdir(parents=True)
    (source_dir / "video.mp4").write_bytes(b"video")
    monkeypatch.setenv("XIAOMI_MIMO_API_KEY_TEM1", "test")
    monkeypatch.setenv("XIAOMI_MIMO_BASE_URL", "https://example.invalid")
    calls = []
    monkeypatch.setattr(
        analysis_runner.subprocess,
        "run",
        lambda command, **kwargs: calls.append((command, kwargs)),
    )

    result = analysis_runner.run_bilibili_analysis(
        "https://www.bilibili.com/video/BV1ab411c7xY?p=1",
        "BV1ab411c7xY",
        1,
        project_root=tmp_path,
        analyzer_root=tmp_path / "video-analyzer",
        reuse_download=True,
        ppt_complete=True,
        validation_profile="strict_course",
    )

    command = calls[0][0]
    assert result["source_dir"] == source_dir
    assert command[command.index("--validation-profile") + 1] == "strict_course"
    assert command[command.index("--model") + 1] == "mimo-v2.5"
    assert command[command.index("--api-key-env") + 1] == "XIAOMI_MIMO_API_KEY_TEM1"
    assert command[command.index("--ppt-ignore-head") + 1] == "18"
    assert command[command.index("--ppt-ignore-tail") + 1] == "10"
    assert "--ppt-complete" in command


def test_shared_source_parser_keeps_filename_part_compatibility():
    url, source_id, part = analysis_runner.parse_bilibili_source(
        "BV1ab411c7xY-p16"
    )

    assert source_id == "BV1ab411c7xY"
    assert part == 16
    assert url.endswith("?p=16")


def test_article_publication_requires_a_valid_local_image(tmp_path):
    record_dir = tmp_path / "record"
    _write_complete_article_record(record_dir)

    entry = article_builder.build_article_entry(
        record_dir,
        "article-test",
        "https://www.bilibili.com/video/BV1ab411c7xY?p=1",
        "BV1ab411c7xY",
        1,
    )

    assert entry["playable"] is False
    assert entry["image_count"] == 1
    assert "video_path" not in entry

    (record_dir / "assets" / "frames" / "slide_001.jpg").unlink()
    with pytest.raises(RuntimeError, match="没有可读取的本地图片"):
        article_builder.validate_illustrated_article(record_dir)


def test_article_job_uses_separate_index_and_never_creates_lesson(
    monkeypatch, tmp_path
):
    started = []

    class FakeThread:
        def __init__(self, *, target, args, daemon, name):
            self.target = target
            self.args = args

        def start(self):
            started.append((self.target, self.args))

    jobs_dir = tmp_path / "jobs"
    articles_dir = tmp_path / "articles"
    lessons_dir = tmp_path / "lessons"
    for directory in (jobs_dir, articles_dir, lessons_dir):
        directory.mkdir()
    monkeypatch.setattr(server, "JOBS_DIR", jobs_dir)
    monkeypatch.setattr(server, "ARTICLES_DIR", articles_dir)
    monkeypatch.setattr(server, "LESSONS_DIR", lessons_dir)
    monkeypatch.setattr(server.threading, "Thread", FakeThread)

    job = server.create_article_job(
        server.ArticleJobRequest(source="BV1ab411c7xY", part=2)
    )

    assert job["kind"] == "article"
    assert job["playable"] is False
    assert started[0][0] is server.run_article_job
    assert list(lessons_dir.iterdir()) == []


def test_standalone_article_content_rewrites_images_to_article_api(
    monkeypatch, tmp_path
):
    record_dir = tmp_path / "record"
    _write_complete_article_record(record_dir)
    articles_dir = tmp_path / "articles"
    articles_dir.mkdir()
    entry = article_builder.build_article_entry(
        record_dir,
        "article-test",
        "https://www.bilibili.com/video/BV1ab411c7xY?p=1",
        "BV1ab411c7xY",
        1,
    )
    (articles_dir / "article-test.json").write_text(
        json.dumps(entry, ensure_ascii=False), encoding="utf-8"
    )
    monkeypatch.setattr(server, "ARTICLES_DIR", articles_dir)

    content = server.get_standalone_article_content("article-test")

    markdown = "\n".join(block["markdown"] for block in content["blocks"])
    assert "/api/articles/article-test/files/assets/frames/slide_001.jpg" in markdown
    assert "/api/lessons/" not in markdown
