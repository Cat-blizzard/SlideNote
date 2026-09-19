from io import BytesIO
import json
import sys
from types import SimpleNamespace

import pytest

from slidenote.llm import LLMClient


def generate(client, image_input, tmp_path):
    if image_input:
        image_path = tmp_path / "slide.png"
        image_path.write_bytes(b"image bytes for mocked transport")
        return client.generate_image_with_usage(image_path, "Describe this slide")
    return client.generate_with_usage("Describe this slide")


@pytest.mark.parametrize("timeout_seconds", [None, 2.5])
@pytest.mark.parametrize("image_input", [False, True])
def test_openai_transport_timeout(monkeypatch, tmp_path, timeout_seconds, image_input):
    client_options = []
    requests = []

    def create(**kwargs):
        requests.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))],
            usage=None,
        )

    def openai_client(**kwargs):
        client_options.append(kwargs)
        return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=openai_client))
    options = {} if timeout_seconds is None else {"timeout_seconds": timeout_seconds}
    client = LLMClient("openai", model="test-model", api_key="test-key", **options)

    result = generate(client, image_input, tmp_path)

    assert result.text == "ok"
    assert len(client_options) == len(requests) == 1
    if timeout_seconds is None:
        assert "timeout" not in client_options[0]
    else:
        assert client_options[0]["timeout"] == timeout_seconds
    assert "timeout_seconds" not in requests[0]


@pytest.mark.parametrize("provider", ["gemini", "claude"])
@pytest.mark.parametrize("timeout_seconds", [None, 2.5])
@pytest.mark.parametrize("image_input", [False, True])
def test_urllib_transport_timeout(monkeypatch, tmp_path, provider, timeout_seconds, image_input):
    calls = []
    response = {
        "candidates": [{"content": {"parts": [{"text": "ok"}]}}],
        "content": [{"type": "text", "text": "ok"}],
    }

    def urlopen(request, *, timeout):
        calls.append((request, timeout))
        return BytesIO(json.dumps(response).encode())

    monkeypatch.setattr("slidenote.llm.urllib.request.urlopen", urlopen)
    options = {} if timeout_seconds is None else {"timeout_seconds": timeout_seconds}
    client = LLMClient(provider, model="test-model", api_key="test-key", **options)

    result = generate(client, image_input, tmp_path)

    assert result.text == "ok"
    assert len(calls) == 1
    request, actual_timeout = calls[0]
    assert request.get_method() == "POST"
    assert actual_timeout == (120 if timeout_seconds is None else timeout_seconds)
    assert "timeout_seconds" not in json.loads(request.data)


def test_timeout_is_applied_to_each_retry(monkeypatch):
    timeouts = []

    def urlopen(request, *, timeout):
        timeouts.append(timeout)
        if len(timeouts) < 3:
            raise TimeoutError("request timed out")
        return BytesIO(b'{"content": [{"type": "text", "text": "ok"}]}')

    monkeypatch.setattr("slidenote.llm.urllib.request.urlopen", urlopen)
    monkeypatch.setattr("slidenote.api_retry.time.sleep", lambda _seconds: None)
    client = LLMClient("claude", api_key="test-key", timeout_seconds=1.25)

    result = client.generate_with_usage("Describe this slide")

    assert result.text == "ok"
    assert result.usage["retries"] == 2
    assert timeouts == [1.25, 1.25, 1.25]


@pytest.mark.parametrize("timeout_seconds", [0, -1, float("inf"), float("nan")])
def test_invalid_timeout_is_rejected(timeout_seconds):
    with pytest.raises(ValueError, match="timeout_seconds must be a positive finite number"):
        LLMClient("openai", api_key="test-key", timeout_seconds=timeout_seconds)
