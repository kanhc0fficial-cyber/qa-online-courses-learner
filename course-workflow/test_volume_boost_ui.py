from pathlib import Path


STATIC_DIR = Path(__file__).parent / "static"


def test_lesson_player_has_persisted_volume_boost_controls():
    app_js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    assert 'const VOLUME_BOOST_LEVELS = [1, 1.5, 2]' in app_js
    assert 'localStorage.setItem(VOLUME_BOOST_KEY, String(boost))' in app_js
    assert 'data-volume-boost="1.5"' in app_js
    assert 'data-volume-boost="2"' in app_js
    assert "createDynamicsCompressor" in app_js


def test_volume_boost_has_responsive_styles():
    styles = (STATIC_DIR / "styles.css").read_text(encoding="utf-8")

    assert ".volume-boost-options button.active" in styles
    assert "grid-template-columns:repeat(3,1fr)" in styles
