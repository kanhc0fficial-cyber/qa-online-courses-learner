import json
from pathlib import Path

from PIL import Image

from video_analyzer.analyzer import VideoAnalyzer
from video_analyzer.artifacts import crop_key_objects, render_article, render_record
from video_analyzer.frame import Frame


class _PromptLoader:
    def get_by_index(self, index):
        return "{FRAME_NOTES}\n{TRANSCRIPT}\n{FIRST_FRAME}\n{prompt}"


class _Client:
    def __init__(self):
        self.calls = []

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs.get("image_paths"):
            scenes = []
            for number in range(1, len(kwargs["image_paths"]) + 1):
                scenes.append({
                    "frame_number": number,
                    "timestamp": float(number),
                    "visible_facts": [f"fact {number}"],
                    "screen_text": [],
                    "changes": [],
                    "key_objects": [{
                        "name": "phone",
                        "bbox": [0.2, 0.2, 0.8, 0.8],
                        "importance": "demonstrated object",
                    }],
                    "uncertainties": [],
                    "inferences": [],
                })
            return {"response": "```json\n" + json.dumps({"scenes": scenes}) + "\n```"}
        return {"response": "summary"}


def _frames(tmp_path: Path, count=2):
    result = []
    frames_dir = tmp_path / "assets" / "frames"
    frames_dir.mkdir(parents=True)
    for number in range(1, count + 1):
        path = frames_dir / f"scene_{number:03d}.jpg"
        Image.new("RGB", (100, 80), (40 * number, 80, 120)).save(path)
        result.append(Frame(number, path, float(number), 1.0, "uniform"))
    return result


def test_grouped_recording_uses_one_multi_image_request(tmp_path):
    frames = _frames(tmp_path, 2)
    client = _Client()
    analyzer = VideoAnalyzer(client, "model", _PromptLoader(), 0.0)

    scenes = analyzer.analyze_frames(frames, group_size=6)

    assert len(client.calls) == 1
    assert client.calls[0]["image_paths"] == [str(frame.path) for frame in frames]
    assert [scene["visible_facts"] for scene in scenes] == [["fact 1"], ["fact 2"]]


def test_json_response_repairs_common_llm_json_damage():
    parsed = VideoAnalyzer._parse_json_response({
        "response": "```json\n{'title': '课程', 'sections': [{'heading': '第一节',}],}\n```"
    })
    assert parsed["title"] == "课程"
    assert parsed["sections"][0]["heading"] == "第一节"


def test_object_crops_and_record_are_created(tmp_path):
    frames = _frames(tmp_path, 1)
    scenes = [{
        "frame_number": 1,
        "timestamp": 1.0,
        "frame_path": str(frames[0].path),
        "visible_facts": ["A person holds a phone."],
        "speech": "Testing the phone.",
        "screen_text": ["100W"],
        "key_objects": [{"name": "phone", "bbox": [0.2, 0.2, 0.8, 0.8], "importance": "main object"}],
        "uncertainties": ["Model is not visible."],
    }]

    crop_key_objects(scenes, frames, tmp_path)
    record = render_record(scenes, tmp_path)

    crop = tmp_path / scenes[0]["key_objects"][0]["crop_path"]
    assert crop.exists()
    assert record.name == "record.md"
    text = record.read_text(encoding="utf-8")
    assert "![关键帧](assets/frames/scene_001.jpg)" in text
    assert scenes[0]["key_objects"][0]["crop_path"] in text


def test_article_renderer_resolves_only_existing_frame_markers(tmp_path):
    frames = _frames(tmp_path, 2)
    scenes = [
        {"frame_number": frame.number, "frame_path": str(frame.path.relative_to(tmp_path))}
        for frame in frames
    ]

    article = render_article("# 标题\n\n<!-- FRAME: 2 -->\n\n正文\n<!-- FRAME: 99 -->", scenes, tmp_path)

    text = article.read_text(encoding="utf-8")
    assert "![关键画面](assets/frames/scene_002.jpg)" in text
    assert "FRAME: 99" not in text


def test_article_renderer_caps_many_markers_at_eight_frames(tmp_path):
    frames = _frames(tmp_path, 10)
    scenes = [
        {"frame_number": frame.number, "frame_path": str(frame.path.relative_to(tmp_path))}
        for frame in frames
    ]
    source = "\n".join(f"<!-- FRAME: {number} -->" for number in range(1, 11))

    article = render_article(source, scenes, tmp_path)

    assert article.read_text(encoding="utf-8").count("![关键画面]") == 8
