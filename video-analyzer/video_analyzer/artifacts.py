from pathlib import Path
from typing import Any, Dict, List
import re

from PIL import Image

from .frame import Frame


def _safe_name(value: str) -> str:
    value = re.sub(r"[^\w\-]+", "_", value.strip(), flags=re.UNICODE).strip("_")
    return value[:48] or "object"


def crop_key_objects(scenes: List[Dict[str, Any]], frames: List[Frame],
                     output_dir: Path, padding: float = 0.07) -> None:
    objects_dir = output_dir / "assets" / "objects"
    objects_dir.mkdir(parents=True, exist_ok=True)
    frame_map = {frame.number: frame for frame in frames}
    for scene in scenes:
        frame = frame_map.get(scene.get("frame_number"))
        if not frame or not frame.path.exists():
            continue
        with Image.open(frame.path) as image:
            width, height = image.size
            for index, item in enumerate(scene.get("key_objects", [])[:3], start=1):
                if not isinstance(item, dict):
                    continue
                bbox = item.get("bbox")
                if not isinstance(bbox, list) or len(bbox) != 4:
                    continue
                try:
                    left, top, right, bottom = [float(value) for value in bbox]
                except (TypeError, ValueError):
                    continue
                if not (0 <= left < right <= 1 and 0 <= top < bottom <= 1):
                    continue
                pad_x, pad_y = (right - left) * padding, (bottom - top) * padding
                box = (
                    max(0, round((left - pad_x) * width)),
                    max(0, round((top - pad_y) * height)),
                    min(width, round((right + pad_x) * width)),
                    min(height, round((bottom + pad_y) * height)),
                )
                name = _safe_name(str(item.get("name", "object")))
                destination = objects_dir / f"scene_{frame.number:03d}_{index}_{name}.jpg"
                image.crop(box).convert("RGB").save(destination, quality=92)
                item["crop_path"] = destination.relative_to(output_dir).as_posix()


def crop_article_frames(scenes: List[Dict[str, Any]], frames: List[Frame],
                        output_dir: Path, minimum_confidence: float = 0.70,
                        selected_frame_numbers: set[int] | None = None) -> None:
    """Create conservatively padded article crops from model grounding boxes."""
    destination_dir = output_dir / "assets" / "article_frames"
    destination_dir.mkdir(parents=True, exist_ok=True)
    frame_map = {frame.number: frame for frame in frames}
    for scene in scenes:
        if selected_frame_numbers is not None and scene.get("frame_number") not in selected_frame_numbers:
            continue
        frame = frame_map.get(scene.get("frame_number"))
        if not frame or not frame.path.exists():
            continue
        bbox = scene.get("content_bbox")
        try:
            values = [float(value) for value in bbox]
            confidence = float(scene.get("content_confidence", 0.0))
        except (TypeError, ValueError):
            continue
        if len(values) != 4 or confidence < minimum_confidence:
            continue
        if max(values) > 1.0 and max(values) <= 1000.0:
            values = [value / 1000.0 for value in values]
            scene["content_bbox_source_space"] = "0-1000"
        left, top, right, bottom = values
        if not (0 <= left < right <= 1 and 0 <= top < bottom <= 1):
            continue

        # Expand the model box, then enforce a broad minimum crop so a precise
        # grounding box cannot accidentally cut off surrounding labels/context.
        padding = 0.06
        left, top = max(0.0, left - padding), max(0.0, top - padding)
        right, bottom = min(1.0, right + padding), min(1.0, bottom + padding)
        minimum_width, minimum_height = 0.60, 0.60
        if right - left < minimum_width:
            center = (left + right) / 2
            left, right = max(0.0, center - minimum_width / 2), min(1.0, center + minimum_width / 2)
            if right - left < minimum_width:
                left, right = (0.0, minimum_width) if left == 0 else (1 - minimum_width, 1.0)
        if bottom - top < minimum_height:
            center = (top + bottom) / 2
            top, bottom = max(0.0, center - minimum_height / 2), min(1.0, center + minimum_height / 2)
            if bottom - top < minimum_height:
                top, bottom = (0.0, minimum_height) if top == 0 else (1 - minimum_height, 1.0)
        retained = (right - left) * (bottom - top)
        if retained > 0.96:
            continue

        with Image.open(frame.path) as image:
            width, height = image.size
            box = (
                max(0, round(left * width)),
                max(0, round(top * height)),
                min(width, round(right * width)),
                min(height, round(bottom * height)),
            )
            destination = destination_dir / f"scene_{frame.number:03d}.jpg"
            image.crop(box).convert("RGB").save(destination, quality=92)
        scene["article_frame_path"] = destination.relative_to(output_dir).as_posix()
        scene["article_crop"] = {
            "normalized_bbox": [left, top, right, bottom],
            "retained_area_ratio": retained,
            "confidence": confidence,
            "reason": scene.get("crop_reason", ""),
        }


def render_record(scenes: List[Dict[str, Any]], output_dir: Path) -> Path:
    lines = ["# 视频事实记录", ""]
    for scene in scenes:
        timestamp = float(scene.get("timestamp", 0.0))
        minutes, seconds = divmod(timestamp, 60)
        lines.extend([f"## {int(minutes):02d}:{seconds:05.2f}", ""])
        frame_path = Path(str(scene.get("frame_path", "")))
        try:
            relative_frame = frame_path.relative_to(output_dir).as_posix()
        except ValueError:
            relative_frame = frame_path.as_posix()
        lines.extend([f"![关键帧]({relative_frame})", "", "### 可见事实", ""])
        facts = scene.get("visible_facts", [])
        lines.extend([f"- {fact}" for fact in facts] or ["- 未获得可靠的画面事实。"])
        speech = str(scene.get("speech", "")).strip()
        if speech:
            lines.extend(["", "### 语音", "", f"> {speech}"])
        screen_text = scene.get("screen_text", [])
        if screen_text:
            lines.extend(["", "### 画面文字", ""])
            lines.extend(f"- {value}" for value in screen_text)
        objects = [item for item in scene.get("key_objects", []) if isinstance(item, dict)]
        if objects:
            lines.extend(["", "### 关键物品", "", "| 物品 | 截图 | 记录 |", "|---|---|---|"])
            for item in objects:
                crop = item.get("crop_path")
                image = f"![]({crop})" if crop else "未生成"
                lines.append(f"| {item.get('name', '')} | {image} | {item.get('importance', '')} |")
        uncertainties = scene.get("uncertainties", [])
        if uncertainties:
            lines.extend(["", "### 不确定事项", ""])
            lines.extend(f"- {value}" for value in uncertainties)
        lines.append("")
    record_path = output_dir / "record.md"
    record_path.write_text("\n".join(lines), encoding="utf-8")
    return record_path


def render_article(article_text: str, scenes: List[Dict[str, Any]], output_dir: Path,
                   max_images: int | None = 8) -> Path:
    """Resolve model-selected frame markers into local Markdown image links."""
    frame_paths = {}
    for scene in scenes:
        chosen_path = scene.get("article_frame_path") or scene.get("frame_path")
        if not scene.get("frame_number") or not chosen_path:
            continue
        path = Path(str(chosen_path))
        if path.is_absolute():
            try:
                path = path.relative_to(output_dir)
            except ValueError:
                pass
        frame_paths[int(scene["frame_number"])] = path.as_posix()
    marker = re.compile(r"<!--\s*FRAME\s*:\s*(\d+)\s*-->", re.IGNORECASE)
    used = []
    valid_markers = [
        int(match.group(1)) for match in marker.finditer(article_text)
        if int(match.group(1)) in frame_paths
    ]
    if max_images == 1 and valid_markers:
        selected_positions = {0}
    elif max_images is not None and max_images > 1 and len(valid_markers) > max_images:
        selected_positions = {
            round(index * (len(valid_markers) - 1) / (max_images - 1))
            for index in range(max_images)
        }
    else:
        selected_positions = set(range(len(valid_markers)))
    marker_position = 0

    def replace(match: re.Match) -> str:
        nonlocal marker_position
        number = int(match.group(1))
        path = frame_paths.get(number)
        if not path:
            return ""
        current_position = marker_position
        marker_position += 1
        if current_position not in selected_positions:
            return ""
        used.append(number)
        return f"\n\n![关键画面]({path})\n\n"

    article_text = re.sub(r"^```(?:markdown)?\s*|\s*```$", "", article_text.strip(), flags=re.IGNORECASE)
    rendered = marker.sub(replace, article_text).strip()
    has_images = "![关键画面](" in rendered
    if not used and not has_images and frame_paths:
        candidates = sorted(frame_paths)
        chosen = [candidates[0], candidates[len(candidates) // 2], candidates[-1]]
        images = "\n\n".join(f"![关键画面]({frame_paths[number]})" for number in dict.fromkeys(chosen))
        rendered = f"{rendered}\n\n{images}".strip()
    article_path = output_dir / "article.md"
    article_path.write_text(f"{rendered}\n", encoding="utf-8")
    return article_path
