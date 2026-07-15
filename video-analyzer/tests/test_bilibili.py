from pathlib import Path

from PIL import Image

from video_analyzer.artifacts import crop_article_frames
from video_analyzer.bilibili import parse_srt, recommended_bilibili_frame_count
from video_analyzer.frame import Frame


def test_parse_srt_and_stingy_budget(tmp_path: Path):
    subtitle = tmp_path / "x.中文.srt"
    subtitle.write_text("1\n00:00:01,000 --> 00:00:02,500\n中文和 /p/\n", encoding="utf-8")
    transcript = parse_srt(subtitle)
    assert transcript.text == "中文和 /p/"
    assert transcript.segments[0]["start"] == 1.0
    assert recommended_bilibili_frame_count(1286.357) == 9


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
