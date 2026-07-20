from pathlib import Path


APP_JS = Path(__file__).parent / "static" / "app.js"


def test_homepage_groups_lessons_by_source_and_defaults_to_part_order():
    source = APP_JS.read_text(encoding="utf-8")

    assert "function buildCourseShelves(lessons, layout)" in source
    assert "lesson.series_id" in source
    assert "Number(a.part || 0) - Number(b.part || 0)" in source


def test_homepage_exposes_persistent_layout_controls():
    source = APP_JS.read_text(encoding="utf-8")

    assert 'jsonFetch("/api/home-layout")' in source
    assert 'method:"PUT"' in source
    assert "调整陈列" in source
    assert "data-shelf-move" in source
    assert "data-lesson-move" in source


def test_homepage_shows_tasks_before_completed_course_shelves():
    source = APP_JS.read_text(encoding="utf-8")

    task_heading = source.index("当前与最近任务")
    course_heading = source.index("课程陈列")
    assert task_heading < course_heading
    assert "音标课程 · OCR" in source
    assert "job.source_id" in source
