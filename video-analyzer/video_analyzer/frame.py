from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Tuple
import logging
import math
import re
import shutil
import subprocess

import cv2
import numpy as np


logger = logging.getLogger(__name__)


@dataclass
class Frame:
    number: int
    path: Path
    timestamp: float
    score: float
    source: str = "unknown"


@dataclass
class _Candidate:
    timestamp: float
    image: np.ndarray
    score: float
    source: str


class VideoProcessor:
    """Extract a small, chronologically representative set of video frames.

    Scene timestamps come from FFmpeg's scene filter (the strategy used by
    mcp-video-analyzer). Uniform samples are mixed in so static/talking-head
    videos and long stretches without cuts still have temporal coverage. All
    filtering and image writes happen locally.
    """

    FRAME_DIFFERENCE_THRESHOLD = 10.0
    _PTS_TIME_RE = re.compile(r"pts_time:([0-9]+(?:\.[0-9]+)?)")

    def __init__(self, video_path: Path, output_dir: Path, model: str):
        self.video_path = video_path
        self.output_dir = output_dir
        self.model = model
        self.frames: List[Frame] = []

    def _calculate_frame_difference(
        self, frame1: Optional[np.ndarray], frame2: Optional[np.ndarray]
    ) -> float:
        if frame1 is None or frame2 is None:
            return 0.0

        gray1 = cv2.cvtColor(frame1, cv2.COLOR_BGR2GRAY)
        gray2 = cv2.cvtColor(frame2, cv2.COLOR_BGR2GRAY)
        if gray1.shape != gray2.shape:
            gray2 = cv2.resize(gray2, (gray1.shape[1], gray1.shape[0]))
        return float(np.mean(cv2.absdiff(gray1, gray2)))

    def _is_keyframe(
        self,
        current_frame: np.ndarray,
        prev_frame: Optional[np.ndarray],
        threshold: float = FRAME_DIFFERENCE_THRESHOLD,
    ) -> bool:
        if prev_frame is None:
            return True
        return self._calculate_frame_difference(current_frame, prev_frame) > threshold

    @staticmethod
    def _is_black_frame(image: np.ndarray, threshold: float) -> bool:
        if image is None or image.size == 0:
            return True
        return float(np.mean(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY))) < threshold

    @staticmethod
    def _dhash(image: np.ndarray) -> np.ndarray:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        resized = cv2.resize(gray, (9, 8), interpolation=cv2.INTER_AREA)
        return (resized[:, :-1] > resized[:, 1:]).reshape(-1)

    @staticmethod
    def _hamming_distance(hash_a: np.ndarray, hash_b: np.ndarray) -> int:
        return int(np.count_nonzero(hash_a != hash_b))

    def _scene_timestamps(self, duration: float, threshold: float) -> List[float]:
        """Return scene-cut timestamps using FFmpeg, or [] on degradation."""
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            logger.warning("FFmpeg is unavailable; using uniform frame sampling only")
            return []

        command = [
            ffmpeg,
            "-hide_banner",
            "-i",
            str(self.video_path),
            "-vf",
            f"select='gt(scene,{threshold})',showinfo",
            "-fps_mode",
            "vfr",
            "-f",
            "null",
            "-",
        ]
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=max(30.0, min(180.0, duration * 2.0)),
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            logger.warning("Scene detection failed; using uniform sampling: %s", exc)
            return []

        timestamps = [float(value) for value in self._PTS_TIME_RE.findall(result.stderr)]
        if result.returncode != 0 and not timestamps:
            logger.warning(
                "FFmpeg scene detection returned code %s; using uniform sampling",
                result.returncode,
            )
        return [value for value in timestamps if 0.0 <= value < duration]

    @staticmethod
    def _adaptive_limit(duration: float) -> int:
        if duration <= 30:
            return max(1, min(12, max(6, math.ceil(duration / 3))))
        if duration <= 60:
            return min(18, max(10, math.ceil(duration / 4)))
        if duration <= 180:
            return min(30, max(15, math.ceil(duration / 6)))
        return min(60, max(30, math.ceil(duration / 10)))

    @staticmethod
    def _merge_timestamps(
        scene_timestamps: Sequence[float],
        uniform_timestamps: Sequence[float],
        tolerance: float,
    ) -> List[Tuple[float, str]]:
        merged: List[Tuple[float, str]] = []
        for timestamp, source in sorted(
            [(value, "scene") for value in scene_timestamps]
            + [(value, "uniform") for value in uniform_timestamps]
        ):
            if merged and abs(timestamp - merged[-1][0]) <= tolerance:
                if source == "scene":
                    merged[-1] = (timestamp, "scene")
                continue
            merged.append((timestamp, source))
        return merged

    @staticmethod
    def _coverage_bucket(timestamp: float, duration: float, bucket_count: int) -> int:
        if duration <= 0 or bucket_count <= 1:
            return 0
        return min(bucket_count - 1, int(timestamp / duration * bucket_count))

    def _select_candidates(
        self,
        candidates: List[_Candidate],
        duration: float,
        target_count: int,
        hash_distance: int,
    ) -> List[_Candidate]:
        if not candidates:
            return []

        # Consecutive visual dedup mirrors mcp-video-analyzer. A candidate is
        # still retained when it is needed to represent an otherwise empty
        # time bucket, preventing long static clips from collapsing to one frame.
        bucket_count = min(target_count, max(1, math.ceil(duration / 15.0)))
        kept: List[_Candidate] = []
        covered = set()
        last_hash: Optional[np.ndarray] = None
        for candidate in sorted(candidates, key=lambda item: item.timestamp):
            current_hash = self._dhash(candidate.image)
            bucket = self._coverage_bucket(candidate.timestamp, duration, bucket_count)
            visually_distinct = (
                last_hash is None
                or self._hamming_distance(last_hash, current_hash) > hash_distance
            )
            # FFmpeg already classified scene candidates as cuts using a richer
            # metric than dHash. Never let dHash (which intentionally ignores
            # global brightness/color) erase those confirmed transitions.
            if candidate.source == "scene" or visually_distinct or bucket not in covered:
                kept.append(candidate)
                covered.add(bucket)
                last_hash = current_hash

        if len(kept) <= target_count:
            return kept

        # Preserve one frame per time bucket, then spend the remaining budget
        # on the strongest scene/change candidates.
        mandatory = {}
        for candidate in kept:
            bucket = self._coverage_bucket(candidate.timestamp, duration, bucket_count)
            midpoint = duration * (bucket + 0.5) / bucket_count
            previous = mandatory.get(bucket)
            if previous is None or abs(candidate.timestamp - midpoint) < abs(previous.timestamp - midpoint):
                mandatory[bucket] = candidate

        selected = list(mandatory.values())
        selected_ids = {id(candidate) for candidate in selected}
        remaining = sorted(
            (candidate for candidate in kept if id(candidate) not in selected_ids),
            key=lambda item: (item.source == "scene", item.score),
            reverse=True,
        )
        selected.extend(remaining[: max(0, target_count - len(selected))])
        return sorted(selected, key=lambda item: item.timestamp)

    def extract_keyframes(
        self,
        frames_per_minute: int = 10,
        duration: Optional[float] = None,
        max_frames: Optional[int] = None,
        scene_threshold: float = 0.1,
        black_threshold: float = 10.0,
        hash_distance: int = 5,
        detect_scenes: bool = True,
    ) -> List[Frame]:
        """Extract hybrid scene-change and uniform-coverage keyframes."""
        self.output_dir.mkdir(parents=True, exist_ok=True)

        cap = cv2.VideoCapture(str(self.video_path))
        if not cap.isOpened():
            raise ValueError(f"Could not open video file: {self.video_path}")

        fps = float(cap.get(cv2.CAP_PROP_FPS))
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if fps <= 0 or total_frames <= 0:
            cap.release()
            raise ValueError(f"Video has invalid FPS or frame count: {self.video_path}")

        full_duration = total_frames / fps
        video_duration = min(duration, full_duration) if duration else full_duration
        requested_count = max(1, math.ceil(video_duration / 60.0 * frames_per_minute))
        target_count = min(requested_count, self._adaptive_limit(video_duration))
        if max_frames is not None:
            target_count = min(target_count, max(1, int(max_frames)))

        scene_timestamps = self._scene_timestamps(video_duration, scene_threshold) if detect_scenes else []
        # Sample more densely than the final API budget so local filters have
        # choices. This has no model/API cost.
        scan_count = max(target_count * 2, math.ceil(video_duration * min(2.0, max(0.25, frames_per_minute / 60.0))))
        scan_count = max(1, min(scan_count, total_frames))
        last_timestamp = max(0.0, video_duration - 1.0 / fps)
        uniform_timestamps = np.linspace(0.0, last_timestamp, scan_count).tolist()
        timestamps = self._merge_timestamps(
            scene_timestamps, uniform_timestamps, tolerance=max(1.0 / fps, 0.05)
        )

        candidates: List[_Candidate] = []
        previous_image: Optional[np.ndarray] = None
        for timestamp, source in timestamps:
            cap.set(cv2.CAP_PROP_POS_MSEC, timestamp * 1000.0)
            ok, image = cap.read()
            if not ok or image is None:
                continue
            if self._is_black_frame(image, black_threshold):
                continue
            score = self._calculate_frame_difference(image, previous_image)
            if source == "scene":
                score = max(score, self.FRAME_DIFFERENCE_THRESHOLD)
            candidates.append(_Candidate(timestamp, image, score, source))
            previous_image = image
        cap.release()

        selected = self._select_candidates(
            candidates, video_duration, target_count, hash_distance
        )
        if not selected:
            raise ValueError("No usable non-black frames could be extracted from the video")

        self.frames = []
        for index, candidate in enumerate(selected, start=1):
            frame_path = self.output_dir / f"scene_{index:03d}.jpg"
            if not cv2.imwrite(str(frame_path), candidate.image):
                raise OSError(f"Could not write extracted frame: {frame_path}")
            self.frames.append(
                Frame(
                    number=index,
                    path=frame_path,
                    timestamp=candidate.timestamp,
                    score=candidate.score,
                    source=candidate.source,
                )
            )

        logger.info(
            "Extracted %s hybrid keyframes (%s scene cuts considered, API budget %s)",
            len(self.frames),
            len(scene_timestamps),
            target_count,
        )
        return self.frames

    def extract_dense_ocr_segments(
        self,
        output_dir: Path,
        *,
        sample_interval: float = 0.25,
        caption_bounds: Tuple[float, float, float, float] = (0.0, 0.68, 1.0, 0.98),
        changed_pixel_ratio: float = 0.002,
        pixel_threshold: int = 18,
        minimum_segment_seconds: float = 0.20,
    ) -> Tuple[List[Frame], List[dict]]:
        """Preserve every distinct burned-in caption state on a dense timeline.

        This track is intentionally independent from article/keyframe selection.
        It samples at 4 Hz by default, segments changes inside the caption ROI,
        and saves the sharpest crop from every segment for OCR.
        """
        output_dir.mkdir(parents=True, exist_ok=True)
        cap = cv2.VideoCapture(str(self.video_path))
        if not cap.isOpened():
            raise ValueError(f"Could not open video file: {self.video_path}")
        fps = float(cap.get(cv2.CAP_PROP_FPS))
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if fps <= 0 or total_frames <= 0:
            cap.release()
            raise ValueError(f"Video has invalid FPS or frame count: {self.video_path}")

        duration = total_frames / fps
        timestamps = np.arange(0.0, duration, max(sample_interval, 1.0 / fps))
        runs: List[dict] = []
        current: Optional[dict] = None
        reference: Optional[np.ndarray] = None
        for timestamp in timestamps:
            cap.set(cv2.CAP_PROP_POS_MSEC, float(timestamp) * 1000.0)
            ok, image = cap.read()
            if not ok or image is None:
                continue
            crop = self._normalized_crop(image, caption_bounds)
            gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
            signature = cv2.GaussianBlur(gray, (3, 3), 0)
            sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
            changed = False
            change_ratio = 0.0
            if reference is not None:
                difference = cv2.absdiff(reference, signature)
                change_ratio = float(np.mean(difference >= pixel_threshold))
                changed = change_ratio >= changed_pixel_ratio
            if current is None or changed:
                if current is not None:
                    current["end"] = float(timestamp)
                    runs.append(current)
                current = {
                    "start": float(timestamp),
                    "end": min(duration, float(timestamp) + sample_interval),
                    "best_image": crop.copy(),
                    "best_timestamp": float(timestamp),
                    "sharpness": sharpness,
                    "change_ratio": change_ratio,
                }
                reference = signature
            else:
                current["end"] = min(duration, float(timestamp) + sample_interval)
                if sharpness > float(current["sharpness"]):
                    current["best_image"] = crop.copy()
                    current["best_timestamp"] = float(timestamp)
                    current["sharpness"] = sharpness
        cap.release()
        if current is not None:
            current["end"] = duration
            runs.append(current)
        if not runs:
            raise ValueError("No dense OCR samples could be extracted from the video")

        frames: List[Frame] = []
        timeline: List[dict] = []
        for index, run in enumerate(runs, start=1):
            end = max(float(run["end"]), float(run["start"]) + minimum_segment_seconds)
            path = output_dir / f"caption_{index:04d}.jpg"
            enlarged = cv2.resize(
                run["best_image"],
                None,
                fx=2.0,
                fy=2.0,
                interpolation=cv2.INTER_CUBIC,
            )
            if not cv2.imwrite(str(path), enlarged):
                raise OSError(f"Could not write dense OCR frame: {path}")
            frames.append(Frame(
                number=index,
                path=path,
                timestamp=float(run["best_timestamp"]),
                score=float(run["change_ratio"]),
                source="dense_caption_ocr",
            ))
            timeline.append({
                "segment": index,
                "start": round(float(run["start"]), 3),
                "end": round(min(duration, end), 3),
                "sample_timestamp": round(float(run["best_timestamp"]), 3),
                "frame_path": str(path),
                "change_ratio": round(float(run["change_ratio"]), 6),
            })
        return frames, timeline

    @staticmethod
    def _normalized_crop(image: np.ndarray, bounds: Tuple[float, float, float, float]) -> np.ndarray:
        height, width = image.shape[:2]
        left, top, right, bottom = bounds
        x1, y1 = max(0, round(left * width)), max(0, round(top * height))
        x2, y2 = min(width, round(right * width)), min(height, round(bottom * height))
        if x2 <= x1 or y2 <= y1:
            raise ValueError(f"Invalid normalized crop bounds: {bounds}")
        return image[y1:y2, x1:x2]

    @classmethod
    def _slide_signature(
        cls, image: np.ndarray, detector_bounds: Tuple[float, float, float, float]
    ) -> np.ndarray:
        crop = cls._normalized_crop(image, detector_bounds)
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        gray = cv2.resize(gray, (320, 180), interpolation=cv2.INTER_AREA)
        return cv2.GaussianBlur(gray, (5, 5), 0)

    @staticmethod
    def _slide_difference(first: np.ndarray, second: np.ndarray) -> Tuple[float, float, float]:
        difference = cv2.absdiff(first, second)
        mean_difference = float(np.mean(difference))
        changed_ratio = float(np.mean(difference >= 18))
        title_height = max(1, round(difference.shape[0] * 0.22))
        title_changed_ratio = float(np.mean(difference[:title_height] >= 15))
        return mean_difference, changed_ratio, title_changed_ratio

    def extract_ppt_slides(
        self,
        detector_bounds: Tuple[float, float, float, float] = (0.035, 0.075, 0.60, 0.78),
        content_bounds: Tuple[float, float, float, float] = (0.02, 0.055, 0.73, 0.79),
        sample_interval: float = 1.0,
        minimum_change_ratio: float = 0.075,
        minimum_title_change_ratio: float = 0.035,
        stability_ratio: float = 0.035,
        minimum_gap: float = 2.0,
        ignore_head_seconds: float = 0.0,
        ignore_tail_seconds: float = 0.0,
    ) -> List[Frame]:
        """Capture stable PPT states while ignoring movement outside the fixed screen ROI.

        Detection uses the left/upper portion of the display where the lecturer rarely
        occludes the slide. After transitions are found, the brightest sample in each
        interval is selected, which generally minimizes the dark lecturer silhouette.
        Saved images are conservatively cropped to the complete physical display.
        """
        self.output_dir.mkdir(parents=True, exist_ok=True)
        cap = cv2.VideoCapture(str(self.video_path))
        if not cap.isOpened():
            raise ValueError(f"Could not open video file: {self.video_path}")
        fps = float(cap.get(cv2.CAP_PROP_FPS))
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if fps <= 0 or total_frames <= 0:
            cap.release()
            raise ValueError(f"Video has invalid FPS or frame count: {self.video_path}")

        step = max(1, round(fps * sample_interval))
        samples = []
        frame_index = 0
        while frame_index < total_frames:
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
            ok, image = cap.read()
            if not ok or image is None:
                frame_index += step
                continue
            timestamp = frame_index / fps
            duration = total_frames / fps
            if timestamp < ignore_head_seconds or timestamp > duration - ignore_tail_seconds:
                frame_index += step
                continue
            signature = self._slide_signature(image, detector_bounds)
            panel = self._normalized_crop(image, content_bounds)
            panel_gray = cv2.cvtColor(panel, cv2.COLOR_BGR2GRAY)
            # A lecturer standing in front of the bright display lowers this value.
            visibility = float(np.mean(panel_gray))
            samples.append({"timestamp": timestamp, "signature": signature, "visibility": visibility})
            frame_index += step
        cap.release()
        if not samples:
            raise ValueError("No PPT samples could be read from the video")

        transition_indices = [0]
        accepted_signature = samples[0]["signature"]
        last_transition_time = float(samples[0]["timestamp"])
        for index in range(1, len(samples)):
            current = samples[index]
            previous = samples[index - 1]
            _, current_change, current_title_change = self._slide_difference(
                accepted_signature, current["signature"]
            )
            _, instability, _ = self._slide_difference(previous["signature"], current["signature"])
            is_new_slide = (
                current_change >= minimum_change_ratio
                or current_title_change >= minimum_title_change_ratio
            )
            is_stable = instability <= stability_ratio
            far_enough = float(current["timestamp"]) - last_transition_time >= minimum_gap
            if is_new_slide and is_stable and far_enough:
                transition_indices.append(index)
                accepted_signature = current["signature"]
                last_transition_time = float(current["timestamp"])

        # Pick the least-occluded frame from each stable slide interval.
        chosen = []
        for interval_index, start in enumerate(transition_indices):
            end = transition_indices[interval_index + 1] if interval_index + 1 < len(transition_indices) else len(samples)
            candidates = samples[start:end]
            if len(candidates) > 2:
                candidates = candidates[1:-1]
            best = max(candidates, key=lambda item: item["visibility"])
            chosen.append(best)

        deduplicated = []
        for item in chosen:
            if deduplicated:
                mean_difference, changed_ratio, _ = self._slide_difference(
                    deduplicated[-1]["signature"], item["signature"]
                )
                if mean_difference < 2.0 and changed_ratio < 0.01:
                    # The transition was caused by a temporary pointer/lecturer
                    # movement; the least-occluded PPT states are identical.
                    continue
            deduplicated.append(item)
        chosen = deduplicated

        cap = cv2.VideoCapture(str(self.video_path))
        self.frames = []
        for number, item in enumerate(chosen, start=1):
            cap.set(cv2.CAP_PROP_POS_MSEC, float(item["timestamp"]) * 1000.0)
            ok, image = cap.read()
            if not ok or image is None:
                continue
            cropped = self._normalized_crop(image, content_bounds)
            frame_path = self.output_dir / f"slide_{number:03d}.jpg"
            if not cv2.imwrite(str(frame_path), cropped):
                raise OSError(f"Could not write PPT slide: {frame_path}")
            self.frames.append(Frame(
                number=number,
                path=frame_path,
                timestamp=float(item["timestamp"]),
                score=float(item["visibility"]),
                source="ppt_roi_stable_change",
            ))
        cap.release()
        if not self.frames:
            raise ValueError("No stable PPT slides could be extracted")
        logger.info(
            "Extracted %s stable PPT states from fixed ROI %s",
            len(self.frames), detector_bounds,
        )
        return self.frames
