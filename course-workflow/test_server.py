import json

import pytest
from fastapi import HTTPException

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


def test_find_download_accepts_video_without_subtitle(monkeypatch, tmp_path):
    downloads = tmp_path / "downloads"
    source = downloads / "BV1pS4y1g7D9-p16-720p"
    source.mkdir(parents=True)
    (source / "lesson.mp4").write_bytes(b"video")
    monkeypatch.setattr(server, "PROJECT_ROOT", tmp_path)

    assert server.find_download("BV1pS4y1g7D9", 16) == source
    assert server.find_download(
        "BV1pS4y1g7D9", 16, require_subtitle=True
    ) is None

    (source / "lesson.zh.srt").write_text("subtitle", encoding="utf-8")
    assert server.find_download("BV1pS4y1g7D9", 16) == source


def test_transcript_json_becomes_vtt_when_downloaded_subtitle_is_missing(
    monkeypatch, tmp_path
):
    record_dir = tmp_path / "record"
    record_dir.mkdir()
    (record_dir / "transcript.json").write_text(json.dumps({
        "source": "local_whisper_medium",
        "segments": [
            {"start": 1.25, "end": 3.5, "text": "第一句"},
            {"start": 3.5, "end": 5.0, "text": "second line"},
        ],
    }), encoding="utf-8")
    lesson = {
        "id": "p29-test",
        "record_dir": str(record_dir),
        "subtitle_path": None,
    }
    monkeypatch.setattr(server, "load_lesson", lambda lesson_id: lesson)

    response = server.get_subtitles("p29-test")

    assert response.headers["x-subtitle-source"] == "generated_from_transcript"
    assert "00:00:01.250 --> 00:00:03.500" in response.body.decode()
    assert "第一句" in response.body.decode()
    assert server.public_lesson(lesson)["subtitle_url"] == "/subtitles/p29-test.vtt"


def test_downloaded_subtitle_still_has_priority_over_transcript(
    monkeypatch, tmp_path
):
    record_dir = tmp_path / "record"
    record_dir.mkdir()
    subtitle = tmp_path / "lesson.srt"
    subtitle.write_text(
        "1\n00:00:01,000 --> 00:00:02,000\n下载字幕\n",
        encoding="utf-8",
    )
    (record_dir / "transcript.json").write_text(json.dumps({
        "segments": [{"start": 1, "end": 2, "text": "Whisper 字幕"}],
    }), encoding="utf-8")
    lesson = {
        "id": "priority-test",
        "record_dir": str(record_dir),
        "subtitle_path": str(subtitle),
    }
    monkeypatch.setattr(server, "load_lesson", lambda lesson_id: lesson)

    response = server.get_subtitles("priority-test")

    text = response.body.decode()
    assert response.headers["x-subtitle-source"] == "downloaded_subtitle"
    assert "下载字幕" in text
    assert "Whisper 字幕" not in text


def test_phonetics_profile_is_explicit_and_disables_ppt_mode(monkeypatch, tmp_path):
    started = []

    class FakeThread:
        def __init__(self, *, target, args, daemon, name):
            self.args = args

        def start(self):
            started.append(self.args)

    monkeypatch.setattr(server, "JOBS_DIR", tmp_path)
    monkeypatch.setattr(server, "find_existing_lesson", lambda source_id, part: None)
    monkeypatch.setattr(server.threading, "Thread", FakeThread)

    result = server.create_job(server.JobRequest(
        source="BV1iV411z7Nj",
        part=3,
        validation_profile="phonetics_course",
        ppt_complete=True,
    ))

    assert result["validation_profile"] == "phonetics_course"
    assert result["evidence_mode"] == "ocr_primary"
    assert result["ppt_complete"] is False
    assert started[0][-1] == "phonetics_course"


def test_concurrent_limit_allows_distinct_parts_until_full(monkeypatch, tmp_path):
    started = []

    class FakeThread:
        def __init__(self, *, target, args, daemon, name):
            self.args = args

        def start(self):
            started.append(self.args)

    monkeypatch.setattr(server, "JOBS_DIR", tmp_path)
    monkeypatch.setattr(server, "MAX_ACTIVE_JOBS", 2)
    monkeypatch.setattr(server, "find_existing_lesson", lambda source_id, part: None)
    monkeypatch.setattr(server.threading, "Thread", FakeThread)

    first = server.create_job(server.JobRequest(source="BV1pS4y1g7D9", part=17))
    second = server.create_job(server.JobRequest(source="BV1pS4y1g7D9", part=18))

    assert first["part"] == 17
    assert second["part"] == 18
    assert len(started) == 2

    with pytest.raises(HTTPException, match="并发制造已达到上限 2"):
        server.create_job(server.JobRequest(source="BV1pS4y1g7D9", part=19))


def test_concurrent_limit_rejects_duplicate_part(monkeypatch, tmp_path):
    (tmp_path / "p17-running.json").write_text(json.dumps({
        "id": "p17-running",
        "status": "running",
        "source_id": "BV1pS4y1g7D9",
        "part": 17,
    }), encoding="utf-8")
    monkeypatch.setattr(server, "JOBS_DIR", tmp_path)
    monkeypatch.setattr(server, "MAX_ACTIVE_JOBS", 3)
    monkeypatch.setattr(server, "find_existing_lesson", lambda source_id, part: None)

    with pytest.raises(HTTPException, match="P17 已在制造中"):
        server.create_job(server.JobRequest(source="BV1pS4y1g7D9", part=17))


def test_resume_failed_job_reuses_record_directory(monkeypatch, tmp_path):
    jobs_dir = tmp_path / "jobs"
    records_dir = tmp_path / "records"
    record_dir = records_dir / "p39-record"
    jobs_dir.mkdir()
    record_dir.mkdir(parents=True)
    (record_dir / "frames.json").write_text("[]", encoding="utf-8")
    job = {
        "id": "p39-failed",
        "status": "failed",
        "stage": "failed",
        "progress": 82,
        "part": 39,
        "source_id": "BV1pS4y1g7D9",
        "source_url": "https://www.bilibili.com/video/BV1pS4y1g7D9?p=39",
        "record_dir": str(record_dir),
        "ppt_complete": True,
        "validation_profile": "strict_course",
        "error": "temporary API failure",
    }
    (jobs_dir / "p39-failed.json").write_text(
        json.dumps(job), encoding="utf-8"
    )
    started = []

    class FakeThread:
        def __init__(self, *, target, args, daemon, name):
            self.args = args

        def start(self):
            started.append(self.args)

    monkeypatch.setattr(server, "JOBS_DIR", jobs_dir)
    monkeypatch.setattr(server, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(server.threading, "Thread", FakeThread)

    result = server.resume_job("p39-failed")

    assert result["status"] == "running"
    assert result["stage_label"] == "从保留现场继续"
    assert result["previous_error"] == "temporary API failure"
    assert started[0][-1] == str(record_dir.resolve())


def test_audio_only_failure_is_not_resumable(monkeypatch, tmp_path):
    record_dir = tmp_path / "records" / "p19-record"
    record_dir.mkdir(parents=True)
    (record_dir / "audio.wav").write_bytes(b"audio")
    monkeypatch.setattr(server, "PROJECT_ROOT", tmp_path)

    assert server.resumable_record_dir({
        "status": "failed",
        "record_dir": str(record_dir),
    }) is None


def test_series_hides_failed_jobs_for_completed_parts(monkeypatch, tmp_path):
    lessons_dir = tmp_path / "lessons"
    jobs_dir = tmp_path / "jobs"
    lessons_dir.mkdir()
    jobs_dir.mkdir()
    (lessons_dir / "completed-p21.json").write_text(json.dumps({
        "id": "completed-p21",
        "series_id": "BV1pS4y1g7D9",
        "part": 21,
        "title": "MOSFET",
    }), encoding="utf-8")
    jobs = [
        {
            "id": "failed-p21",
            "status": "failed",
            "source_id": "BV1pS4y1g7D9",
            "part": 21,
        },
        {
            "id": "running-p21",
            "status": "running",
            "source_id": "BV1pS4y1g7D9",
            "part": 21,
        },
        {
            "id": "failed-other-series-p21",
            "status": "failed",
            "source_id": "BVother",
            "part": 21,
        },
    ]
    for job in jobs:
        (jobs_dir / f"{job['id']}.json").write_text(
            json.dumps(job), encoding="utf-8"
        )
    monkeypatch.setattr(server, "LESSONS_DIR", lessons_dir)
    monkeypatch.setattr(server, "JOBS_DIR", jobs_dir)

    result = server.get_series()
    visible_ids = {job["id"] for job in result["jobs"]}

    assert "failed-p21" not in visible_ids
    assert "running-p21" in visible_ids
    assert "failed-other-series-p21" in visible_ids


def test_download_batch_creates_720p_jobs_for_requested_range(monkeypatch, tmp_path):
    started = []

    class FakeThread:
        def __init__(self, *, target, args, daemon, name):
            self.args = args

        def start(self):
            started.append(self.args)

    monkeypatch.setattr(server, "JOBS_DIR", tmp_path)
    monkeypatch.setattr(server.threading, "Thread", FakeThread)
    result = server.create_download_batch(server.DownloadBatchRequest(
        source="BV1ab411c7xY",
        start_part=3,
        end_part=5,
        execution_mode="parallel",
    ))

    assert result["parts"] == [3, 4, 5]
    assert result["quality"] == "720p"
    assert len(started) == 1
    jobs = [json.loads(path.read_text(encoding="utf-8")) for path in tmp_path.glob("*.json")]
    assert [job["part"] for job in jobs] == [3, 4, 5]
    assert all(job["kind"] == "download" and job["quality"] == "720p" for job in jobs)


def test_course_batch_skips_completed_parts(monkeypatch, tmp_path):
    started = []

    class FakeThread:
        def __init__(self, *, target, args, daemon, name):
            self.args = args

        def start(self):
            started.append(self.args)

    monkeypatch.setattr(server, "JOBS_DIR", tmp_path)
    monkeypatch.setattr(
        server,
        "find_existing_lesson",
        lambda source_id, part: {"id": "existing-p4"} if part == 4 else None,
    )
    monkeypatch.setattr(server.threading, "Thread", FakeThread)
    result = server.create_course_batch(server.CourseBatchRequest(
        source="BV1ab411c7xY",
        start_part=3,
        end_part=5,
        execution_mode="parallel",
        strict_validation=False,
    ))

    assert result["parts"] == [3, 5]
    assert result["skipped_parts"] == [4]
    assert len(started) == 1
    jobs = [json.loads(path.read_text(encoding="utf-8")) for path in tmp_path.glob("*.json")]
    assert [job["part"] for job in jobs] == [3, 5]
    assert all(job["kind"] == "lesson" for job in jobs)


def test_batch_range_rejects_reversed_or_excessive_range():
    for start, end, message in ((5, 3, "起始 P"), (1, 31, "最多处理 30")):
        with pytest.raises(HTTPException, match=message):
            server.validate_part_range(start, end)


def test_serial_batch_runners_keep_part_order(monkeypatch):
    downloads = []
    courses = []
    monkeypatch.setattr(
        server,
        "run_download_job",
        lambda job_id, source_url, source_id, part, reuse: downloads.append(part),
    )
    monkeypatch.setattr(
        server,
        "run_job",
        lambda job_id, source_url, source_id, part, reuse, ppt, strict: courses.append(part),
    )

    server.run_download_batch([
        (f"d-{part}", "url", "BV1ab411c7xY", part, True)
        for part in (7, 8, 9)
    ], "serial")
    server.run_course_batch([
        (f"c-{part}", "url", "BV1ab411c7xY", part, True, True, True)
        for part in (10, 11, 12)
    ], "serial")

    assert downloads == [7, 8, 9]
    assert courses == [10, 11, 12]


def test_home_layout_keeps_only_valid_sources_and_lesson_ids(monkeypatch, tmp_path):
    lessons_dir = tmp_path / "lessons"
    lessons_dir.mkdir()
    for lesson_id, source_id, part in (
        ("source-a-p1", "BVsourceA", 1),
        ("source-a-p2", "BVsourceA", 2),
        ("source-b-p1", "BVsourceB", 1),
    ):
        (lessons_dir / f"{lesson_id}.json").write_text(json.dumps({
            "id": lesson_id,
            "series_id": source_id,
            "part": part,
        }), encoding="utf-8")
    monkeypatch.setattr(server, "LESSONS_DIR", lessons_dir)

    layout = server.normalized_home_layout({
        "source_order": ["missing", "BVsourceB", "BVsourceB", "BVsourceA"],
        "lesson_order": {
            "BVsourceA": ["source-a-p2", "source-b-p1", "source-a-p2", "source-a-p1"],
            "missing": ["unknown"],
        },
        "titles": {
            "BVsourceA": "  主课程  ",
            "missing": "不应保留",
        },
    })

    assert layout["source_order"] == ["BVsourceB", "BVsourceA"]
    assert layout["lesson_order"]["BVsourceA"] == ["source-a-p2", "source-a-p1"]
    assert layout["titles"] == {"BVsourceA": "主课程"}


def test_home_layout_round_trip_is_persisted(monkeypatch, tmp_path):
    lessons_dir = tmp_path / "lessons"
    lessons_dir.mkdir()
    (lessons_dir / "p1.json").write_text(json.dumps({
        "id": "p1",
        "series_id": "BVsourceA",
        "part": 1,
    }), encoding="utf-8")
    layout_path = tmp_path / "home_layout.json"
    monkeypatch.setattr(server, "LESSONS_DIR", lessons_dir)
    monkeypatch.setattr(server, "HOME_LAYOUT_PATH", layout_path)

    saved = server.update_home_layout(server.HomeLayoutRequest(
        source_order=["BVsourceA"],
        lesson_order={"BVsourceA": ["p1"]},
        titles={"BVsourceA": "电力电子课程"},
    ))

    assert saved == server.get_home_layout()
    assert json.loads(layout_path.read_text(encoding="utf-8")) == saved
