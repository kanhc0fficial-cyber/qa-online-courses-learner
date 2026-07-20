from pathlib import Path


APP_JS = Path(__file__).parent / "static" / "app.js"


def test_automatic_checkpoint_exits_and_continue_restores_fullscreen():
    app_js = APP_JS.read_text(encoding="utf-8")

    assert "await exitFullscreenForCheckpoint(openedManually)" in app_js
    assert "document.exitFullscreen || document.webkitExitFullscreen" in app_js
    assert "checkpointFullscreenTarget = target" in app_js
    assert "restoreFullscreenAfterCheckpoint();completed.add(active.id)" in app_js
    assert (
        "connectedTarget.requestFullscreen || "
        "connectedTarget.webkitRequestFullscreen"
    ) in app_js


def test_manual_checkpoint_does_not_schedule_fullscreen_restore():
    app_js = APP_JS.read_text(encoding="utf-8")

    assert "if (openedManually) return;" in app_js
    assert (
        'if (manualOpen) modalRoot.querySelector("#modal-close").onclick='
        '()=>{checkpointFullscreenTarget=null;'
    ) in app_js
