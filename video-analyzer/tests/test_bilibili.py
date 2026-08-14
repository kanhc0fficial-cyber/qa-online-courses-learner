from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from video_analyzer.artifacts import crop_article_frames
from video_analyzer.bilibili import build_yutto_command, parse_srt, recommended_bilibili_frame_count
from video_analyzer.frame import Frame, VideoProcessor


def test_parse_srt_and_stingy_budget(tmp_path: Path):
    subtitle = tmp_path / "x.中文.srt"
    subtitle.write_text("1\n00:00:01,000 --> 00:00:02,500\n中文和 /p/\n", encoding="utf-8")
    transcript = parse_srt(subtitle)
    assert transcript.text == "中文和 /p/"
    assert transcript.segments[0]["start"] == 1.0
    assert recommended_bilibili_frame_count(1286.357) == 9


def test_yutto_download_is_always_limited_to_720p(monkeypatch, tmp_path: Path):
    monkeypatch.setattr("video_analyzer.bilibili._yutto_prefix", lambda: ["yutto"])

    command = build_yutto_command("BV15J41167CP", tmp_path)

    assert command[command.index("--video-quality") + 1] == "64"
    assert command[command.index("--proxy") + 1] == "no"
    assert "80" not in command


def test_yutto_proxy_can_be_explicitly_overridden(monkeypatch, tmp_path: Path):
    monkeypatch.setattr("video_analyzer.bilibili._yutto_prefix", lambda: ["yutto"])

    command = build_yutto_command("BV15J41167CP", tmp_path, proxy="auto")

    assert command[command.index("--proxy") + 1] == "auto"


def test_grounding_crop_only_selected_frame(tmp_path: Path):
    frame_path = tmp_path / "frame.jpg"
    Image.new("RGB", (1000, 800), "white").save(frame_path)
    frames = [Frame(1, frame_path, 1.0, 1.0), Frame(2, frame_path, 2.0, 1.0)]
    scenes = [
        {"frame_number": 1, "content_bbox": [100, 100, 900, 900], "content_confidence": .9},
        {"frame_number": 2, "content_bbox": [.1, .1, .9, .9], "content_confidence": .9},
    ]
    crop_article_frames(scenes, frames, tmp_path, selected_frame_numbers={1})
    assert (tmp_path / scenes[0]["article_frame_path"]).exists()
    assert "article_frame_path" not in scenes[1]
    assert frame_path.exists()


def test_dense_ocr_track_preserves_each_caption_change(tmp_path: Path):
    video = tmp_path / "captions.mp4"
    writer = cv2.VideoWriter(
        str(video),
        cv2.VideoWriter_fourcc(*"mp4v"),
        4.0,
        (320, 180),
    )
    for caption in ("ONE", "TWO", "THREE"):
        for _ in range(4):
            image = np.full((180, 320, 3), 255, dtype=np.uint8)
            cv2.putText(
                image, caption, (80, 160), cv2.FONT_HERSHEY_SIMPLEX,
                1.0, (0, 0, 0), 2, cv2.LINE_AA,
            )
            writer.write(image)
    writer.release()

    processor = VideoProcessor(video, tmp_path / "content", "test")
    frames, timeline = processor.extract_dense_ocr_segments(
        tmp_path / "ocr",
        sample_interval=0.25,
        changed_pixel_ratio=0.001,
    )

    assert len(frames) == 3
    assert len(timeline) == 3
    assert timeline[0]["start"] == 0.0
    assert timeline[-1]["end"] == 3.0
    assert all(frame.path.is_file() for frame in frames)
