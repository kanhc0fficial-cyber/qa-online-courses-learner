import requests
import pytest

from video_analyzer.clients.generic_openai_api import GenericOpenAIAPIClient


class FakeResponse:
    def __init__(self, status_code=200, payload=None, headers=None):
        self.status_code = status_code
        self._payload = payload or {
            "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
            "model": "test-model",
        }
        self.headers = headers or {}
        self.text = "fake response"

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(response=self)

    def json(self):
        return self._payload


def make_client(max_retries=3):
    return GenericOpenAIAPIClient(
        "secret", "https://example.invalid/v1", max_retries=max_retries
    )


def test_retries_transient_connection_error(monkeypatch):
    calls = []
    sleeps = []

    def post(*args, **kwargs):
        calls.append((args, kwargs))
        if len(calls) < 3:
            raise requests.exceptions.ProxyError("proxy disconnected")
        return FakeResponse()

    monkeypatch.setattr(requests, "post", post)
    monkeypatch.setattr("video_analyzer.clients.generic_openai_api.time.sleep", sleeps.append)

    result = make_client().generate("hello", model="test-model")

    assert result["response"] == "ok"
    assert len(calls) == 3
    assert sleeps == [2, 4]


def test_does_not_retry_non_transient_client_error(monkeypatch):
    calls = []

    def post(*args, **kwargs):
        calls.append((args, kwargs))
        return FakeResponse(status_code=400)

    monkeypatch.setattr(requests, "post", post)

    with pytest.raises(RuntimeError, match=r"after 1 attempt\(s\)"):
        make_client().generate("bad request")

    assert len(calls) == 1


def test_rate_limit_honors_retry_after(monkeypatch):
    responses = [
        FakeResponse(status_code=429, headers={"Retry-After": "7"}),
        FakeResponse(),
    ]
    sleeps = []
    monkeypatch.setattr(requests, "post", lambda *args, **kwargs: responses.pop(0))
    monkeypatch.setattr("video_analyzer.clients.generic_openai_api.time.sleep", sleeps.append)

    result = make_client().generate("hello")

    assert result["response"] == "ok"
    assert sleeps == [7.0]


def test_rejects_zero_retries():
    with pytest.raises(ValueError, match="at least 1"):
        make_client(max_retries=0)
