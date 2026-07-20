from pathlib import Path

import server


def test_series_identifies_canonical_runtime(monkeypatch, tmp_path):
    lessons_dir = tmp_path / "lessons"
    jobs_dir = tmp_path / "jobs"
    lessons_dir.mkdir()
    jobs_dir.mkdir()
    monkeypatch.setattr(server, "LESSONS_DIR", lessons_dir)
    monkeypatch.setattr(server, "JOBS_DIR", jobs_dir)

    result = server.get_series()

    assert result["canonical"] is True
    assert Path(result["canonical_root"]) == server.ROOT.resolve()
    assert Path(result["data_dir"]) == server.DATA_DIR.resolve()


def test_historical_server_exports_canonical_app():
    historical_server = (
        server.ROOT.parent.parent
        / "course-workflow"
        / "server.py"
    )
    source = historical_server.read_text(encoding="utf-8")

    assert "CANONICAL_SERVER" in source
    assert "app = canonical_module.app" in source
