from pathlib import Path

import cv2
import numpy as np

from video_analyzer.frame import VideoProcessor


def _write_video(path: Path, colors, seconds_per_color=1, fps=10):
    writer = cv2.VideoWriter(
        str(path), cv2.VideoWriter_fourcc(*"MJPG"), fps, (96, 64)
    )
    assert writer.isOpened()
    for color in colors:
        image = np.full((64, 96, 3), color, dtype=np.uint8)
        cv2.rectangle(image, (10, 10), (30, 30), tuple(reversed(color)), -1)
        for _ in range(seconds_per_color * fps):
            writer.write(image)
    writer.release()


def test_dhash_and_hamming_distance_detect_visual_change(tmp_path):
    processor = VideoProcessor(tmp_path / "unused.mp4", tmp_path / "frames", "test")
    left = np.zeros((64, 96, 3), dtype=np.uint8)
    right = left.copy()
    left[:, :48] = 255
    right[:, 48:] = 255

    assert processor._hamming_distance(processor._dhash(left), processor._dhash(left)) == 0
    assert processor._hamming_distance(processor._dhash(left), processor._dhash(right)) > 0


def test_hybrid_extraction_is_chronological_bounded_and_persistent(tmp_path):
    video_path = tmp_path / "cuts.avi"
    _write_video(video_path, [(30, 40, 180), (40, 180, 30), (180, 30, 40)])
    output_dir = tmp_path / "frames"
    processor = VideoProcessor(video_path, output_dir, "test")

    frames = processor.extract_keyframes(frames_per_minute=120, max_frames=5)

    assert 1 <= len(frames) <= 5
    assert [frame.timestamp for frame in frames] == sorted(frame.timestamp for frame in frames)
    assert all(frame.path.exists() for frame in frames)
    assert all(frame.path.name.startswith("scene_") for frame in frames)
    assert {frame.source for frame in frames} <= {"scene", "uniform"}
    assert any(frame.source == "scene" for frame in frames)


def test_static_video_uses_uniform_coverage(tmp_path, monkeypatch):
    video_path = tmp_path / "static.avi"
    _write_video(video_path, [(80, 120, 160)], seconds_per_color=2)
    processor = VideoProcessor(video_path, tmp_path / "frames", "test")
    monkeypatch.setattr(processor, "_scene_timestamps", lambda duration, threshold: [])

    frames = processor.extract_keyframes(frames_per_minute=60, max_frames=4)

    assert frames
    assert all(frame.source == "uniform" for frame in frames)


def test_black_frame_detection():
    black = np.zeros((16, 16, 3), dtype=np.uint8)
    visible = np.full((16, 16, 3), 30, dtype=np.uint8)
    assert VideoProcessor._is_black_frame(black, threshold=10)
    assert not VideoProcessor._is_black_frame(visible, threshold=10)
