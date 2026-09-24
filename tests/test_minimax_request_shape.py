"""Verifies MiniMaxHailuoProvider builds the request/poll/retrieve sequence
documented for MiniMax's video-generation task API, without any network
call, using a fake requests.Session."""
from pathlib import Path

from production.providers.minimax_hailuo import DEFAULT_MODEL, MiniMaxHailuoProvider
from production.schemas.models import ShotJob


class _FakeResponse:
    def __init__(self, json_body, content=b"", status_code=200):
        self._json = json_body
        self.content = content
        self.status_code = status_code

    def raise_for_status(self):
        pass

    def json(self):
        return self._json


class _FakeSession:
    def __init__(self, poll_sequence, create_responses=None):
        self.poll_sequence = list(poll_sequence)
        self.create_responses = list(create_responses) if create_responses else None
        self.create_calls = []
        self.poll_calls = []
        self.retrieve_calls = []

    def post(self, url, json, headers, timeout):
        self.create_calls.append({"url": url, "json": json, "headers": headers})
        if self.create_responses:
            return self.create_responses.pop(0)
        return _FakeResponse({"task_id": "task-abc"})

    def get(self, url, headers=None, params=None, timeout=None):
        if url.endswith("/query/video_generation"):
            self.poll_calls.append(params)
            return _FakeResponse(self.poll_sequence.pop(0))
        if url.endswith("/files/retrieve"):
            self.retrieve_calls.append(params)
            return _FakeResponse({"download_url": "https://example/video.mp4"})
        return _FakeResponse({}, content=b"fake-mp4-bytes")


def test_minimax_generate_shot_builds_request_and_parses_success(monkeypatch, tmp_path):
    monkeypatch.setenv("MINIMAX_API_KEY", "fake-key")
    monkeypatch.setattr("production.providers.minimax_hailuo.time.sleep", lambda _: None)
    fake_session = _FakeSession(poll_sequence=[
        {"status": "Queueing"},
        {"status": "Processing"},
        {"status": "Success", "file_id": "file-123"},
    ])
    provider = MiniMaxHailuoProvider(session=fake_session)

    shot = ShotJob(shot_id="shot-00", order=0, duration_sec=6, video_prompt="a cat drinking milk", narration_text="hi")
    result = provider.generate_shot(shot, tmp_path)

    assert result.success is True
    assert Path(result.output_path).read_bytes() == b"fake-mp4-bytes"
    body = fake_session.create_calls[0]["json"]
    assert body["model"] == DEFAULT_MODEL
    assert body["content"] == [{"type": "text", "text": shot.video_prompt}]
    assert 4 <= body["duration"] <= 15
    assert body["ratio"] == "9:16"
    assert fake_session.retrieve_calls[0] == {"file_id": "file-123"}


def test_minimax_terminal_fail_status_reports_error(monkeypatch, tmp_path):
    monkeypatch.setenv("MINIMAX_API_KEY", "fake-key")
    monkeypatch.setattr("production.providers.minimax_hailuo.time.sleep", lambda _: None)
    fake_session = _FakeSession(poll_sequence=[{"status": "Fail", "error": "content policy"}])
    provider = MiniMaxHailuoProvider(session=fake_session)

    shot = ShotJob(shot_id="shot-00", order=0, duration_sec=6, video_prompt="a cat", narration_text="hi")
    result = provider.generate_shot(shot, tmp_path)
    assert result.success is False


def test_minimax_retries_task_creation_on_5xx_then_succeeds(monkeypatch, tmp_path):
    monkeypatch.setenv("MINIMAX_API_KEY", "fake-key")
    monkeypatch.setattr("production.providers.minimax_hailuo.time.sleep", lambda _: None)
    fake_session = _FakeSession(
        poll_sequence=[{"status": "Success", "file_id": "file-123"}],
        create_responses=[
            _FakeResponse({}, status_code=503),
            _FakeResponse({"task_id": "task-abc"}, status_code=200),
        ],
    )
    provider = MiniMaxHailuoProvider(session=fake_session)
    shot = ShotJob(shot_id="shot-00", order=0, duration_sec=6, video_prompt="a cat", narration_text="hi")
    result = provider.generate_shot(shot, tmp_path)
    assert result.success is True
    assert len(fake_session.create_calls) == 2


def test_minimax_cost_estimate_uses_resolution_specific_rate(monkeypatch):
    monkeypatch.delenv("MINIMAX_RESOLUTION", raising=False)
    provider = MiniMaxHailuoProvider()
    shot = ShotJob(shot_id="shot-00", order=0, duration_sec=6, video_prompt="a cat", narration_text="hi")
    assert provider.estimate_cost_usd(shot) == round(6 * 0.08, 3)

    monkeypatch.setenv("MINIMAX_RESOLUTION", "2K")
    assert provider.estimate_cost_usd(shot) == round(6 * 0.13, 3)
