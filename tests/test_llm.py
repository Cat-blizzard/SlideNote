import pytest

from slidenote.llm import LLMClient, LLMResult, get_provider_spec, resolve_provider_runtime
from slidenote.models import ImageAsset, SlidePage
from slidenote.notes import _llm_page_prompt


def test_provider_aliases():
    assert get_provider_spec("chatgpt").canonical_name == "openai"
    assert get_provider_spec("千问").canonical_name == "qwen"
    assert get_provider_spec("豆包").canonical_name == "doubao"
    assert get_provider_spec("Claude").canonical_name == "claude"


def test_missing_key_message_is_provider_specific(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    with pytest.raises(RuntimeError) as exc:
        LLMClient(provider="deepseek")

    assert "DEEPSEEK_API_KEY" in str(exc.value)


def test_doubao_requires_model_when_no_env(monkeypatch):
    monkeypatch.setenv("ARK_API_KEY", "test-key")
    monkeypatch.delenv("DOUBAO_MODEL", raising=False)
    monkeypatch.delenv("ARK_MODEL", raising=False)
    monkeypatch.delenv("SLIDENOTE_MODEL", raising=False)

    with pytest.raises(RuntimeError) as exc:
        LLMClient(provider="doubao")

    assert "--model" in str(exc.value)


def test_qwen_has_default_vision_model(monkeypatch):
    monkeypatch.delenv("QWEN_VISION_MODEL", raising=False)
    monkeypatch.delenv("SLIDENOTE_VISION_MODEL", raising=False)

    runtime = resolve_provider_runtime("qwen", for_vision=True)

    assert runtime["supports_image_input"] is True
    assert runtime["model"] == "qwen-vl-plus"


def test_doubao_vision_requires_endpoint_model(monkeypatch):
    for name in [
        "DOUBAO_VISION_MODEL",
        "ARK_VISION_MODEL",
        "VOLCENGINE_VISION_MODEL",
        "SLIDENOTE_VISION_MODEL",
        "SLIDENOTE_MODEL",
        "DOUBAO_MODEL",
        "ARK_MODEL",
    ]:
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(RuntimeError) as exc:
        resolve_provider_runtime("doubao", for_vision=True)

    assert "DOUBAO_VISION_MODEL" in str(exc.value)


def test_non_vision_prompt_marks_images_as_not_provided():
    page = SlidePage(slide_id=1, images=[ImageAsset(id="s1_img1", path="images/a.png")])

    prompt = _llm_page_prompt(page, supports_image_input=False)

    assert "image pixels are not attached to this note-writing call" in prompt
    assert "不要写“未提供图片像素”" in prompt
    assert "s1_img1" in prompt


def test_llm_client_retries_transient_errors(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr("slidenote.api_retry.time.sleep", lambda _seconds: None)
    calls = {"count": 0}

    def flaky_call(self, system_prompt, user_prompt):
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("HTTP 429 rate limit")
        return LLMResult(text="ok", usage={"total_tokens": 3})

    monkeypatch.setattr("slidenote.llm.LLMClient._generate_openai_compatible", flaky_call)

    result = LLMClient(provider="openai", model="gpt-test").generate_with_usage("system", "user")

    assert result.text == "ok"
    assert result.usage["retries"] == 1
    assert calls["count"] == 2


def test_llm_client_raises_after_retry_budget(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr("slidenote.api_retry.time.sleep", lambda _seconds: None)
    calls = {"count": 0}

    def always_rate_limited(self, system_prompt, user_prompt):
        calls["count"] += 1
        raise RuntimeError("HTTP 503 service unavailable")

    monkeypatch.setattr("slidenote.llm.LLMClient._generate_openai_compatible", always_rate_limited)

    with pytest.raises(RuntimeError, match="503"):
        LLMClient(provider="openai", model="gpt-test").generate_with_usage("system", "user")

    assert calls["count"] == 3
