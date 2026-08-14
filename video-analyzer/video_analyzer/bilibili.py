"""Bilibili-specific source discovery, subtitle parsing, downloading and safe cropping."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
import html
import importlib.util
import math
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET

import cv2
import numpy as np

from .audio_processor import AudioTranscript
from .frame import Frame

YUTTO_720P_QUALITY = "64"


@dataclass
class BilibiliAssets:
    source_dir: Path
    video: Path
    subtitle: Optional[Path]
    metadata: Optional[Path]
    danmaku: Optional[Path]
    cover: Optional[Path]


def discover_bilibili_assets(source_dir: Path) -> BilibiliAssets:
    source_dir = source_dir.resolve()
    if not source_dir.is_dir():
        raise ValueError(f"Bilibili source directory does not exist: {source_dir}")
    files = [path for path in source_dir.iterdir() if path.is_file()]
    videos = [path for path in files if path.suffix.lower() in {".mp4", ".mkv", ".webm"}]
    if not videos:
        raise ValueError(f"No video file found in: {source_dir}")
    video = max(videos, key=lambda path: path.stat().st_size)

    subtitles = [path for path in files if path.suffix.lower() in {".srt", ".vtt"}]
    def subtitle_priority(path: Path) -> Tuple[int, int]:
        name = path.name.lower()
        preferred = any(token in name for token in ("中文", "zh-hans", "zh_cn", ".zh."))
        return (1 if preferred else 0, path.stat().st_size)

    subtitle = max(subtitles, key=subtitle_priority) if subtitles else None
    metadata = next((path for path in files if path.suffix.lower() == ".nfo"), None)
    danmaku = next((path for path in files if path.suffix.lower() == ".ass"), None)
    covers = [path for path in files if path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}]
    cover = max(covers, key=lambda path: path.stat().st_size) if covers else None
    return BilibiliAssets(source_dir, video, subtitle, metadata, danmaku, cover)


def _parse_srt_time(value: str) -> float:
    hours, minutes, rest = value.strip().replace(".", ",").split(":")
    seconds, millis = rest.split(",")
    return int(hours) * 3600 + int(minutes) * 60 + int(seconds) + int(millis) / 1000


def parse_srt(path: Path) -> AudioTranscript:
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    segments: List[Dict[str, Any]] = []
    for block in re.split(r"\r?\n\s*\r?\n", text.strip()):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        timing_index = next((index for index, line in enumerate(lines) if "-->" in line), None)
        if timing_index is None:
            continue
        start_text, end_text = [part.strip().split()[0] for part in lines[timing_index].split("-->")]
        content = " ".join(lines[timing_index + 1:])
        content = html.unescape(re.sub(r"<[^>]+>", "", content)).strip()
        if not content:
            continue
        segments.append({
            "start": _parse_srt_time(start_text),
            "end": _parse_srt_time(end_text),
            "text": content,
            "words": [],
        })
    if not segments:
        raise ValueError(f"No subtitle segments parsed from: {path}")
    return AudioTranscript(
        text=" ".join(segment["text"] for segment in segments),
        segments=segments,
        language="zh",
    )


def subtitle_quality(transcript: AudioTranscript, video_duration: float) -> Dict[str, Any]:
    segments = transcript.segments
    first = float(segments[0]["start"]) if segments else 0.0
    last = float(segments[-1]["end"]) if segments else 0.0
    covered_span = max(0.0, last - first)
    coverage = covered_span / video_duration if video_duration > 0 else 0.0
    chars = len(re.sub(r"\s+", "", transcript.text))
    usable = len(segments) >= 8 and chars >= 200 and coverage >= 0.65
    return {
        "usable": usable,
        "segment_count": len(segments),
        "character_count": chars,
        "first_timestamp": first,
        "last_timestamp": last,
        "span_coverage": coverage,
    }


def parse_nfo(path: Optional[Path]) -> Dict[str, Any]:
    if not path or not path.exists():
        return {}
    root = ET.parse(path).getroot()
    actors = []
    for actor in root.findall("actor"):
        actors.append({
            "name": actor.findtext("name", "").strip(),
            "role": actor.findtext("role", "").strip(),
            "profile": actor.findtext("profile", "").strip(),
        })
    return {
        "title": root.findtext("title", "").strip(),
        "show_title": root.findtext("show_title", "").strip(),
        "description": root.findtext("plot", "").strip(),
        "published": root.findtext("premiered", "").strip(),
        "website": root.findtext("website", "").strip(),
        "tags": [node.text.strip() for node in root.findall("tag") if node.text],
        "authors": actors,
    }


def normalize_bilibili_url(source: str, part: Optional[int] = None) -> str:
    source = source.strip()
    match = re.search(r"BV[0-9A-Za-z]+", source, flags=re.IGNORECASE)
    if match and not source.lower().startswith(("http://", "https://")):
        source = f"https://www.bilibili.com/video/{match.group(0)}"
    if part:
        separator = "&" if "?" in source else "?"
        if re.search(r"[?&]p=\d+", source):
            source = re.sub(r"([?&]p=)\d+", rf"\g<1>{part}", source)
        else:
            source = f"{source}{separator}p={part}"
    return source


def _yutto_prefix() -> List[str]:
    executable = shutil.which("yutto")
    if executable:
        return [executable]
    if importlib.util.find_spec("yutto"):
        return [sys.executable, "-m", "yutto"]
    raise RuntimeError("yutto is not installed. Install with: pip install 'video-analyzer[bilibili]'")


def build_yutto_command(
    source: str,
    output_dir: Path,
    part: Optional[int] = None,
    proxy: str = "no",
) -> List[str]:
    return _yutto_prefix() + [
        normalize_bilibili_url(source, part),
        "--video-quality", YUTTO_720P_QUALITY,
        "--dir", str(output_dir),
        "--with-metadata",
        "--save-cover",
        "--no-color",
        "--no-progress",
        "--vcodec=avc:copy",
        "--proxy", proxy,
    ]


def download_with_yutto(
    source: str,
    output_dir: Path,
    part: Optional[int] = None,
    proxy: str = "no",
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    command = build_yutto_command(source, output_dir, part, proxy)
    with (output_dir / "yutto.stdout.log").open("w", encoding="utf-8") as stdout, \
            (output_dir / "yutto.stderr.log").open("w", encoding="utf-8") as stderr:
        subprocess.run(command, stdout=stdout, stderr=stderr, text=True, encoding="utf-8", check=True)
    return output_dir


def _edge_dark_runs(gray: np.ndarray, threshold: int = 18) -> Tuple[int, int, int, int]:
    row_dark = (np.mean(gray < threshold, axis=1) >= 0.995) & (np.std(gray, axis=1) <= 8)
    col_dark = (np.mean(gray < threshold, axis=0) >= 0.995) & (np.std(gray, axis=0) <= 8)

    def leading(values: np.ndarray) -> int:
        count = 0
        for value in values:
            if not value:
                break
            count += 1
        return count

    return leading(col_dark), leading(row_dark), leading(col_dark[::-1]), leading(row_dark[::-1])


def detect_conservative_crop(video_path: Path, sample_count: int = 12,
                             max_crop_ratio: float = 0.12) -> Dict[str, Any]:
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise ValueError(f"Could not open video for border detection: {video_path}")
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = float(cap.get(cv2.CAP_PROP_FPS))
    frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    duration = frames / fps if fps > 0 else 0
    samples = []
    for timestamp in np.linspace(duration * 0.03, duration * 0.97, sample_count):
        cap.set(cv2.CAP_PROP_POS_MSEC, float(timestamp) * 1000)
        ok, image = cap.read()
        if ok and image is not None:
            samples.append(_edge_dark_runs(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)))
    cap.release()
    if not samples:
        return {"applied": False, "bounds": [0, 0, width, height], "reason": "no_samples"}

    values = np.asarray(samples, dtype=np.float32)
    stable = np.quantile(values, 0.10, axis=0)
    limits = np.asarray([width, height, width, height], dtype=np.float32) * max_crop_ratio
    stable = np.minimum(stable, limits)
    margins = np.asarray([width, height, width, height], dtype=np.float32) * 0.006
    crop = np.maximum(0, stable - margins).astype(int)
    minimum = np.asarray([width, height, width, height], dtype=np.float32) * 0.01
    crop = np.where(crop >= minimum, crop, 0)
    left, top, right, bottom = [int(value) for value in crop]
    bounds = [left, top, width - right, height - bottom]
    retained = (bounds[2] - bounds[0]) * (bounds[3] - bounds[1]) / max(1, width * height)
    applied = any(crop) and retained >= 0.80
    if not applied:
        bounds = [0, 0, width, height]
    confidence = [float(np.mean(values[:, index] >= stable[index])) for index in range(4)]
    return {
        "applied": applied,
        "bounds": bounds,
        "original_size": [width, height],
        "retained_area_ratio": retained if applied else 1.0,
        "side_confidence": confidence,
        "sample_count": len(samples),
        "mode": "stable_near_black_edges_only",
        "reason": "stable_border" if applied else "no_high_confidence_border",
    }


def apply_crop_to_frames(frames: Sequence[Frame], decision: Dict[str, Any]) -> None:
    if not decision.get("applied"):
        return
    left, top, right, bottom = [int(value) for value in decision["bounds"]]
    for frame in frames:
        image = cv2.imread(str(frame.path))
        if image is None:
            continue
        cropped = image[top:bottom, left:right]
        if cropped.size:
            cv2.imwrite(str(frame.path), cropped)


def recommended_bilibili_frame_count(duration: float) -> int:
    """Deliberately tiny visual budget: roughly one frame per 2.5 minutes."""
    return max(6, min(12, math.ceil(duration / 150.0)))
