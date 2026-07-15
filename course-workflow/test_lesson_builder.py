from lesson_builder import (
    _align_checkpoint_notes,
    _article_frame_timeline,
    _extract_json,
    _order_checkpoints,
    _validate_lesson,
)


def _question(identifier: str):
    return {
        "id": identifier,
        "type": "choice",
        "prompt": "题干",
        "options": ["正确", "错误"],
        "answers": ["正确"],
    }


def test_checkpoint_order_is_code_owned_before_validation():
    lesson = {
        "title": "课程",
        "overview": "概览",
        "teaching_plan": "教案",
        "checkpoints": [
            {"time": 300, "questions": [_question("q5"), _question("q6")]},
            {"time": 100, "questions": [_question("q1"), _question("q2")]},
            {"time": 200, "questions": [_question("q3"), _question("q4")]},
        ],
    }
    _order_checkpoints(lesson)
    assert [item["time"] for item in lesson["checkpoints"]] == [100, 200, 300]
    _validate_lesson(lesson, duration=400)


def test_checkpoint_note_alignment_is_persisted_during_generation():
    lesson = {
        "checkpoints": [
            {"time": 100},
            {"time": 220},
            {"time": 300},
        ]
    }
    article = """
![第一页](assets/frames/slide_001.jpg)
<!-- FRAME: 2 -->
![第四页](assets/frames/slide_004.jpg)
"""
    scenes = [
        {"frame_number": 1, "timestamp": 20},
        {"frame_number": 2, "timestamp": 90},
        {"frame_number": 3, "timestamp": 150},
        {"frame_number": 4, "timestamp": 210},
    ]
    assert _article_frame_timeline(article, scenes) == [
        {"frame_number": 1, "time": 20.0},
        {"frame_number": 2, "time": 90.0},
        {"frame_number": 4, "time": 210.0},
    ]
    _align_checkpoint_notes(lesson, article, scenes)
    assert lesson["note_alignment"]["version"] == "article-frame-interval-v1"
    assert lesson["checkpoints"][0]["note_frame_numbers"] == [1, 2]
    assert lesson["checkpoints"][1]["note_frame_numbers"] == [4]
    assert lesson["checkpoints"][2]["note_frame_numbers"] == []
    assert lesson["checkpoints"][1]["note_time_range"] == {
        "start_exclusive": 100.0,
        "end_inclusive": 220.0,
    }


def test_extract_json_repairs_only_invalid_backslash_escapes():
    value = _extract_json(r'{"text":"特性。\    - 下一项","formula":"\Delta v"}')
    assert value == {"text": "特性。    - 下一项", "formula": r"\Delta v"}


def test_general_video_profile_keeps_answerability_but_allows_short_video_lessons():
    lesson = {
        "title": "短视频", "overview": "概览", "teaching_plan": "教案",
        "checkpoints": [{"time": 20, "questions": [_question("q1")]}],
    }
    _validate_lesson(lesson, duration=30, strict=False)
