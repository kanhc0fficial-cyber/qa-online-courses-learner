import json

import server


def _request(*, force_rebuild=False):
    return server.JobRequest(
        source="https://www.bilibili.com/video/BV1pS4y1g7D9?p=10",
        part=10,
        reuse_download=True,
        force_rebuild=force_rebuild,
    )


def test_existing_lesson_requires_confirmation_before_rebuild(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "JOBS_DIR", tmp_path)
    monkeypatch.setattr(server, "find_existing_lesson", lambda source_id, part: {
        "id": "existing-p10", "part": 10, "title": "现有课程"
    })

    result = server.create_job(_request())

    assert result["confirmation_required"] is True
    assert result["existing_lesson_url"] == "/lessons/existing-p10"
    assert list(tmp_path.glob("*.json")) == []


def test_confirmed_rebuild_creates_visible_job(monkeypatch, tmp_path):
    started = []

    class FakeThread:
        def __init__(self, *, target, args, daemon, name):
            self.args = args

        def start(self):
            started.append(self.args)

    monkeypatch.setattr(server, "JOBS_DIR", tmp_path)
    monkeypatch.setattr(server, "find_existing_lesson", lambda source_id, part: {
        "id": "existing-p10", "part": 10, "title": "现有课程"
    })
    monkeypatch.setattr(server.threading, "Thread", FakeThread)

    result = server.create_job(_request(force_rebuild=True))

    assert result["status"] == "running"
    assert result["part"] == 10
    assert len(started) == 1
    saved = json.loads((tmp_path / f"{result['id']}.json").read_text(encoding="utf-8"))
    assert saved["stage_label"] == "等待开始"


def test_parse_source_accepts_any_bilibili_bv_and_defaults_to_first_part():
    url, source_id, part = server.parse_source("BV1ab411c7xY")
    assert source_id == "BV1ab411c7xY"
    assert part == 1
    assert url == "https://www.bilibili.com/video/BV1ab411c7xY?p=1"


def test_find_download_requires_video_and_subtitle(monkeypatch, tmp_path):
    downloads = tmp_path / "downloads"
    source = downloads / "BV1pS4y1g7D9-p16-720p"
    source.mkdir(parents=True)
    (source / "lesson.mp4").write_bytes(b"video")
    monkeypatch.setattr(server, "PROJECT_ROOT", tmp_path)

    assert server.find_download("BV1pS4y1g7D9", 16) is None

    (source / "lesson.zh.srt").write_text("subtitle", encoding="utf-8")
    assert server.find_download("BV1pS4y1g7D9", 16) == source
