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


def test_homepage_polling_preserves_unsubmitted_form_drafts():
    source = APP_JS.read_text(encoding="utf-8")

    assert "function captureDashboardDraft()" in source
    assert "function restoreDashboardDraft(draft)" in source
    assert "const draft = captureDashboardDraft();" in source
    assert "restoreDashboardDraft(draft);" in source


def test_failed_resumable_job_has_continue_action():
    source = APP_JS.read_text(encoding="utf-8")

    assert "job.resume_supported" in source
    assert "data-resume-job" in source
    assert "/resume`" in source
    assert "function bindResumeButtons()" in source


def test_homepage_has_a_separate_non_playable_article_route():
    source = APP_JS.read_text(encoding="utf-8")

    assert 'id="article-form"' in source
    assert 'jsonFetch("/api/article-jobs"' in source
    assert "图文文章" in source
    assert "不纳入播放课程" in source
    assert "function articlePage(id)" in source
    assert "articleMatch" in source

    article_page = source[source.index("async function articlePage(id)") :]
    assert "<video" not in article_page
