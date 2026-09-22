"""Verifies SeedanceProvider builds the request BytePlus ModelArk's official
video-generation task API expects (model id, content/text part, duration
clamped into [4,30], resolution, aspect_ratio, generate_audio=False), and
parses a real-shaped response envelope correctly - all without any network
call, using a fake requests.Session."""
from pathlib import Path

from production.providers.seedance import SeedanceProvider, DEFAULT_MODEL
from production.schemas.models import ShotJob


class _FakeResponse:
    def __init__(self, json_body, content=b""):
        self._json = json_body
        self.content = content

    def raise_for_status(self):
        pass

    def json(self):
        return self._json


class _FakeSession:
    def __init__(self, poll_sequence, video_bytes=b"fake-mp4-bytes"):
        self.poll_sequence = list(poll_sequence)
        self.video_bytes = video_bytes
        self.create_calls = []
        self.poll_calls = []
        self.download_calls = []

    def post(self, url, json, headers, timeout):
        self.create_calls.append({"url": url, "json": json, "headers": headers})
        return _FakeResponse({"id": "task-123"})

    def get(self, url, headers=None, timeout=None):
        if headers is not None:
            self.poll_calls.append(url)
            return _FakeResponse(self.poll_sequence.pop(0))
        self.download_calls.append(url)
        return _FakeResponse({}, content=self.video_bytes)


def test_seedance_generate_shot_builds_official_request_and_parses_success(monkeypatch, tmp_path):
    monkeypatch.setenv("ARK_API_KEY", "fake-key-for-test")
    fake_session = _FakeSession(poll_sequence=[
        {"model": DEFAULT_MODEL, "status": "queued"},
        {"model": DEFAULT_MODEL, "status": "running"},
        {
            "model": DEFAULT_MODEL, "status": "succeeded",
            "content": {"video_url": "https://ark-content-generation.example/video.mp4"},
            "usage": {"completion_tokens": 129600, "total_tokens": 129600},
        },
    ])
    provider = SeedanceProvider(session=fake_session)
    monkeypatch.setattr("production.providers.seedance.time.sleep", lambda _: None)

    shot = ShotJob(
        shot_id="shot-00", order=0, duration_sec=6,
        video_prompt="a woman drinking coffee by a window",
        narration_text="hi",
    )
    result = provider.generate_shot(shot, tmp_path)

    assert result.success is True
    assert Path(result.output_path).read_bytes() == b"fake-mp4-bytes"

    body = fake_session.create_calls[0]["json"]
    assert body["model"] == DEFAULT_MODEL
    assert body["content"] == [{"type": "text", "text": shot.video_prompt}]
    assert 4 <= body["duration"] <= 30
    assert body["aspect_ratio"] == "9:16"
    assert body["generate_audio"] is False
    assert fake_session.create_calls[0]["headers"]["Authorization"] == "Bearer fake-key-for-test"


def test_seedance_duration_is_clamped_into_official_4_to_30_range(monkeypatch, tmp_path):
    monkeypatch.setenv("ARK_API_KEY", "fake-key-for-test")
    fake_session = _FakeSession(poll_sequence=[
        {"status": "succeeded", "content": {"video_url": "https://example/v.mp4"}},
    ])
    provider = SeedanceProvider(session=fake_session)
    monkeypatch.setattr("production.providers.seedance.time.sleep", lambda _: None)

    shot = ShotJob(shot_id="shot-00", order=0, duration_sec=2, video_prompt="a cat", narration_text="hi")
    provider.generate_shot(shot, tmp_path)
    assert fake_session.create_calls[0]["json"]["duration"] == 4


def test_seedance_terminal_failure_status_reports_error_message(monkeypatch, tmp_path):
    monkeypatch.setenv("ARK_API_KEY", "fake-key-for-test")
    fake_session = _FakeSession(poll_sequence=[
        {"status": "failed", "error": {"message": "prompt violates content policy"}},
    ])
    provider = SeedanceProvider(session=fake_session)
    monkeypatch.setattr("production.providers.seedance.time.sleep", lambda _: None)

    shot = ShotJob(shot_id="shot-00", order=0, duration_sec=6, video_prompt="a cat", narration_text="hi")
    result = provider.generate_shot(shot, tmp_path)
    assert result.success is False
    assert "prompt violates content policy" in result.error
