from __future__ import annotations

import json
import base64
import mimetypes
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from slidenote.api_retry import with_api_retries


@dataclass(frozen=True, slots=True)
class ProviderSpec:
    canonical_name: str
    family: str
    default_model: str | None
    api_key_envs: tuple[str, ...]
    base_url: str | None = None
    base_url_envs: tuple[str, ...] = ()
    model_envs: tuple[str, ...] = ()
    supports_image_input: bool = False
    default_vision_model: str | None = None
    vision_model_envs: tuple[str, ...] = ()


@dataclass(slots=True)
class LLMResult:
    text: str
    usage: dict[str, Any]


PROVIDERS: dict[str, ProviderSpec] = {
    "openai": ProviderSpec(
        canonical_name="openai",
        family="openai_compatible",
        default_model="gpt-4.1-mini",
        api_key_envs=("OPENAI_API_KEY",),
        model_envs=("OPENAI_MODEL", "CHATGPT_MODEL"),
        supports_image_input=True,
        default_vision_model="gpt-4.1-mini",
        vision_model_envs=("OPENAI_VISION_MODEL", "CHATGPT_VISION_MODEL"),
    ),
    "deepseek": ProviderSpec(
        canonical_name="deepseek",
        family="openai_compatible",
        default_model="deepseek-v4-flash",
        api_key_envs=("DEEPSEEK_API_KEY",),
        base_url="https://api.deepseek.com",
        model_envs=("DEEPSEEK_MODEL",),
    ),
    "qwen": ProviderSpec(
        canonical_name="qwen",
        family="openai_compatible",
        default_model="qwen-plus",
        api_key_envs=("QWEN_API_KEY", "DASHSCOPE_API_KEY"),
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        base_url_envs=("QWEN_BASE_URL", "DASHSCOPE_BASE_URL"),
        model_envs=("QWEN_MODEL", "DASHSCOPE_MODEL"),
        supports_image_input=True,
        default_vision_model="qwen-vl-plus",
        vision_model_envs=("QWEN_VISION_MODEL", "DASHSCOPE_VISION_MODEL"),
    ),
    "doubao": ProviderSpec(
        canonical_name="doubao",
        family="openai_compatible",
        default_model=None,
        api_key_envs=("DOUBAO_API_KEY", "ARK_API_KEY", "VOLCENGINE_API_KEY"),
        base_url="https://ark.cn-beijing.volces.com/api/v3",
        base_url_envs=("DOUBAO_BASE_URL", "ARK_BASE_URL", "VOLCENGINE_BASE_URL"),
        model_envs=("DOUBAO_MODEL", "ARK_MODEL"),
        supports_image_input=True,
        vision_model_envs=("DOUBAO_VISION_MODEL", "ARK_VISION_MODEL", "VOLCENGINE_VISION_MODEL"),
    ),
    "glm": ProviderSpec(
        canonical_name="glm",
        family="openai_compatible",
        default_model="glm-5.1",
        api_key_envs=("GLM_API_KEY", "ZAI_API_KEY", "ZHIPUAI_API_KEY"),
        base_url="https://open.bigmodel.cn/api/paas/v4/",
        base_url_envs=("GLM_BASE_URL", "ZAI_BASE_URL", "ZHIPUAI_BASE_URL"),
        model_envs=("GLM_MODEL", "ZAI_MODEL", "ZHIPUAI_MODEL"),
    ),
    "gemini": ProviderSpec(
        canonical_name="gemini",
        family="gemini",
        default_model="gemini-3-flash-preview",
        api_key_envs=("GEMINI_API_KEY", "GOOGLE_API_KEY"),
        base_url="https://generativelanguage.googleapis.com/v1beta",
        base_url_envs=("GEMINI_BASE_URL", "GOOGLE_GENERATIVE_LANGUAGE_BASE_URL"),
        model_envs=("GEMINI_MODEL",),
        supports_image_input=True,
        default_vision_model="gemini-3-flash-preview",
        vision_model_envs=("GEMINI_VISION_MODEL", "GOOGLE_VISION_MODEL"),
    ),
    "claude": ProviderSpec(
        canonical_name="claude",
        family="claude",
        default_model="claude-sonnet-4-20250514",
        api_key_envs=("ANTHROPIC_API_KEY", "CLAUDE_API_KEY"),
        base_url="https://api.anthropic.com",
        base_url_envs=("ANTHROPIC_BASE_URL", "CLAUDE_BASE_URL"),
        model_envs=("ANTHROPIC_MODEL", "CLAUDE_MODEL"),
        supports_image_input=True,
        default_vision_model="claude-sonnet-4-20250514",
        vision_model_envs=("ANTHROPIC_VISION_MODEL", "CLAUDE_VISION_MODEL"),
    ),
}

ALIASES = {
    "chatgpt": "openai",
    "gpt": "openai",
    "openai-chatgpt": "openai",
    "deepseek": "deepseek",
    "deep-seek": "deepseek",
    "qwen": "qwen",
    "tongyi": "qwen",
    "dashscope": "qwen",
    "千问": "qwen",
    "通义千问": "qwen",
    "doubao": "doubao",
    "ark": "doubao",
    "volcengine": "doubao",
    "豆包": "doubao",
    "火山方舟": "doubao",
    "glm": "glm",
    "zai": "glm",
    "zhipu": "glm",
    "智谱": "glm",
    "gemini": "gemini",
    "google": "gemini",
    "claude": "claude",
    "anthropic": "claude",
}

SYSTEM_PROMPT = (
    "你是课程笔记写作助手。输出必须直接进入 Markdown 正文，不要写寒暄、任务复述、JSON 说明或规则遵循说明。"
    "你要把幻灯片中的零散 bullet 改写成自然、连贯、适合复习的课程笔记，并保留必要来源标记。"
    "如果输入没有提供图片 OCR 或视觉摘要，你不能根据图片路径、文件名或占位 caption 猜测图片内容，也不要解释自己无法解析图片。"
)


class LLMClient:
    def __init__(
        self,
        provider: str,
        model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        max_output_tokens: int = 4096,
        temperature: float | None = None,
    ) -> None:
        self.spec = get_provider_spec(provider)
        self.model = _resolve_model(self.spec, model)
        self.api_key = _resolve_api_key(self.spec, api_key)
        self.base_url = _resolve_base_url(self.spec, base_url)
        self.max_output_tokens = max_output_tokens
        self.temperature = temperature

    @property
    def provider_name(self) -> str:
        return self.spec.canonical_name

    @property
    def supports_image_input(self) -> bool:
        return self.spec.supports_image_input

    def generate(self, user_prompt: str, system_prompt: str = SYSTEM_PROMPT) -> str:
        return self.generate_with_usage(user_prompt, system_prompt).text

    def generate_with_usage(self, user_prompt: str, system_prompt: str = SYSTEM_PROMPT) -> LLMResult:
        def call() -> LLMResult:
            if self.spec.family == "openai_compatible":
                return self._generate_openai_compatible(system_prompt, user_prompt)
            if self.spec.family == "gemini":
                return self._generate_gemini(system_prompt, user_prompt)
            if self.spec.family == "claude":
                return self._generate_claude(system_prompt, user_prompt)
            raise RuntimeError(f"Unsupported provider family: {self.spec.family}")

        retry_result = with_api_retries(call)
        return _with_retry_usage(retry_result.value, retry_result.retries)

    def generate_image_with_usage(
        self,
        image_path: Path,
        user_prompt: str,
        system_prompt: str = SYSTEM_PROMPT,
        image_detail: str = "low",
    ) -> LLMResult:
        if not self.supports_image_input:
            raise RuntimeError(f"Provider `{self.provider_name}` does not support image input in SlideNote.")
        image_bytes = image_path.read_bytes()
        mime_type = _guess_mime_type(image_path)
        def call() -> LLMResult:
            if self.spec.family == "openai_compatible":
                return self._generate_openai_image(system_prompt, user_prompt, image_bytes, mime_type, image_detail)
            if self.spec.family == "gemini":
                return self._generate_gemini_image(system_prompt, user_prompt, image_bytes, mime_type)
            if self.spec.family == "claude":
                return self._generate_claude_image(system_prompt, user_prompt, image_bytes, mime_type)
            raise RuntimeError(f"Unsupported vision provider family: {self.spec.family}")

        retry_result = with_api_retries(call)
        return _with_retry_usage(retry_result.value, retry_result.retries)

    def _generate_openai_compatible(self, system_prompt: str, user_prompt: str) -> LLMResult:
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError("OpenAI SDK is required for OpenAI-compatible providers. Install with `pip install openai`.") from exc

        client_kwargs: dict[str, Any] = {"api_key": self.api_key}
        if self.base_url:
            client_kwargs["base_url"] = self.base_url
        client = OpenAI(**client_kwargs)

        request: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        if self.max_output_tokens:
            request["max_tokens"] = self.max_output_tokens
        if self.temperature is not None:
            request["temperature"] = self.temperature

        response = client.chat.completions.create(**request)
        content = response.choices[0].message.content
        return LLMResult(text=content.strip() if content else "", usage=_normalize_openai_usage(getattr(response, "usage", None)))

    def _generate_openai_image(
        self,
        system_prompt: str,
        user_prompt: str,
        image_bytes: bytes,
        mime_type: str,
        image_detail: str,
    ) -> LLMResult:
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError("OpenAI SDK is required for OpenAI-compatible providers. Install with `pip install openai`.") from exc

        client_kwargs: dict[str, Any] = {"api_key": self.api_key}
        if self.base_url:
            client_kwargs["base_url"] = self.base_url
        client = OpenAI(**client_kwargs)
        data_url = f"data:{mime_type};base64,{base64.b64encode(image_bytes).decode('ascii')}"
        request: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user_prompt},
                        {"type": "image_url", "image_url": {"url": data_url, "detail": image_detail}},
                    ],
                },
            ],
        }
        if self.max_output_tokens:
            request["max_tokens"] = self.max_output_tokens
        if self.temperature is not None:
            request["temperature"] = self.temperature
        response = client.chat.completions.create(**request)
        content = response.choices[0].message.content
        return LLMResult(text=content.strip() if content else "", usage=_normalize_openai_usage(getattr(response, "usage", None)))

    def _generate_gemini(self, system_prompt: str, user_prompt: str) -> LLMResult:
        model = self.model.removeprefix("models/")
        endpoint = f"{self.base_url.rstrip('/')}/models/{urllib.parse.quote(model, safe='')}:generateContent"
        payload: dict[str, Any] = {
            "system_instruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
        }
        generation_config: dict[str, Any] = {}
        if self.max_output_tokens:
            generation_config["maxOutputTokens"] = self.max_output_tokens
        if self.temperature is not None:
            generation_config["temperature"] = self.temperature
        if generation_config:
            payload["generationConfig"] = generation_config

        data = _post_json(endpoint, payload, {"x-goog-api-key": self.api_key})
        candidates = data.get("candidates") or []
        if not candidates:
            raise RuntimeError(f"Gemini returned no candidates: {data}")
        parts = candidates[0].get("content", {}).get("parts", [])
        return LLMResult(
            text="".join(part.get("text", "") for part in parts).strip(),
            usage=_normalize_gemini_usage(data.get("usageMetadata")),
        )

    def _generate_gemini_image(self, system_prompt: str, user_prompt: str, image_bytes: bytes, mime_type: str) -> LLMResult:
        model = self.model.removeprefix("models/")
        endpoint = f"{self.base_url.rstrip('/')}/models/{urllib.parse.quote(model, safe='')}:generateContent"
        payload: dict[str, Any] = {
            "system_instruction": {"parts": [{"text": system_prompt}]},
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {
                            "inline_data": {
                                "mime_type": mime_type,
                                "data": base64.b64encode(image_bytes).decode("ascii"),
                            }
                        },
                        {"text": user_prompt},
                    ],
                }
            ],
        }
        generation_config: dict[str, Any] = {}
        if self.max_output_tokens:
            generation_config["maxOutputTokens"] = self.max_output_tokens
        if self.temperature is not None:
            generation_config["temperature"] = self.temperature
        if generation_config:
            payload["generationConfig"] = generation_config
        data = _post_json(endpoint, payload, {"x-goog-api-key": self.api_key})
        candidates = data.get("candidates") or []
        if not candidates:
            raise RuntimeError(f"Gemini returned no candidates: {data}")
        parts = candidates[0].get("content", {}).get("parts", [])
        return LLMResult(
            text="".join(part.get("text", "") for part in parts).strip(),
            usage=_normalize_gemini_usage(data.get("usageMetadata")),
        )

    def _generate_claude(self, system_prompt: str, user_prompt: str) -> LLMResult:
        endpoint = f"{self.base_url.rstrip('/')}/v1/messages"
        payload: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_output_tokens,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_prompt}],
        }
        if self.temperature is not None:
            payload["temperature"] = self.temperature

        data = _post_json(
            endpoint,
            payload,
            {
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
            },
        )
        blocks = data.get("content") or []
        return LLMResult(
            text="".join(block.get("text", "") for block in blocks if block.get("type") == "text").strip(),
            usage=_normalize_claude_usage(data.get("usage")),
        )

    def _generate_claude_image(self, system_prompt: str, user_prompt: str, image_bytes: bytes, mime_type: str) -> LLMResult:
        endpoint = f"{self.base_url.rstrip('/')}/v1/messages"
        payload: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_output_tokens,
            "system": system_prompt,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": mime_type,
                                "data": base64.b64encode(image_bytes).decode("ascii"),
                            },
                        },
                        {"type": "text", "text": user_prompt},
                    ],
                }
            ],
        }
        if self.temperature is not None:
            payload["temperature"] = self.temperature
        data = _post_json(
            endpoint,
            payload,
            {
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
            },
        )
        blocks = data.get("content") or []
        return LLMResult(
            text="".join(block.get("text", "") for block in blocks if block.get("type") == "text").strip(),
            usage=_normalize_claude_usage(data.get("usage")),
        )


def get_provider_spec(provider: str) -> ProviderSpec:
    normalized = ALIASES.get(provider.strip().lower(), provider.strip().lower())
    spec = PROVIDERS.get(normalized)
    if not spec:
        supported = ", ".join(sorted(PROVIDERS))
        raise ValueError(f"Unsupported LLM provider `{provider}`. Supported providers: {supported}")
    return spec


def supported_provider_names() -> list[str]:
    return sorted(PROVIDERS)


def _with_retry_usage(result: LLMResult, retries: int) -> LLMResult:
    usage = dict(result.usage or {})
    usage["retries"] = retries
    return LLMResult(text=result.text, usage=usage)


def resolve_provider_runtime(provider: str, model: str | None = None, base_url: str | None = None, for_vision: bool = False) -> dict[str, Any]:
    spec = get_provider_spec(provider)
    return {
        "provider": spec.canonical_name,
        "model": _resolve_model(spec, model, for_vision=for_vision),
        "base_url": _resolve_base_url(spec, base_url),
        "supports_image_input": spec.supports_image_input,
    }


def _resolve_model(spec: ProviderSpec, explicit_model: str | None, for_vision: bool = False) -> str:
    if for_vision:
        model = explicit_model or _first_env(("SLIDENOTE_VISION_MODEL",) + spec.vision_model_envs) or spec.default_vision_model
    else:
        model = explicit_model or _first_env(("SLIDENOTE_MODEL",) + spec.model_envs) or spec.default_model
    if not model:
        model_envs = spec.vision_model_envs if for_vision else spec.model_envs
        generic_env = "SLIDENOTE_VISION_MODEL" if for_vision else "SLIDENOTE_MODEL"
        raise RuntimeError(
            f"`{spec.canonical_name}` requires a model name. Pass `--model ...` or set one of: "
            f"{', '.join(model_envs) or generic_env}"
        )
    return model


def _resolve_api_key(spec: ProviderSpec, explicit_api_key: str | None) -> str:
    key = explicit_api_key or _first_env(spec.api_key_envs)
    if not key:
        raise RuntimeError(
            f"Missing API key for provider `{spec.canonical_name}`. Set one of: {', '.join(spec.api_key_envs)} "
            "or pass `--api-key`."
        )
    return key


def _resolve_base_url(spec: ProviderSpec, explicit_base_url: str | None) -> str | None:
    return explicit_base_url or _first_env(("SLIDENOTE_BASE_URL",) + spec.base_url_envs) or spec.base_url


def _first_env(names: tuple[str, ...]) -> str | None:
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return None


def _post_json(url: str, payload: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            **headers,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            response_body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"LLM request failed with HTTP {exc.code}: {error_body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"LLM request failed: {exc}") from exc
    return json.loads(response_body)


def _guess_mime_type(path: Path) -> str:
    mime_type, _ = mimetypes.guess_type(path.name)
    if mime_type in {"image/jpeg", "image/png", "image/gif", "image/webp"}:
        return mime_type
    return "image/png"


def _dump_usage_object(usage: Any) -> dict[str, Any]:
    if usage is None:
        return {}
    if isinstance(usage, dict):
        return usage
    if hasattr(usage, "model_dump"):
        return usage.model_dump()
    if hasattr(usage, "dict"):
        return usage.dict()
    if hasattr(usage, "__dict__"):
        return {key: value for key, value in vars(usage).items() if not key.startswith("_")}
    return {}


def _normalize_openai_usage(usage: Any) -> dict[str, Any]:
    raw = _dump_usage_object(usage)
    prompt_details = raw.get("prompt_tokens_details") or {}
    completion_details = raw.get("completion_tokens_details") or {}
    return {
        "input_tokens": raw.get("prompt_tokens"),
        "output_tokens": raw.get("completion_tokens"),
        "total_tokens": raw.get("total_tokens"),
        "provider_cached_input_tokens": prompt_details.get("cached_tokens"),
        "raw": raw,
        "completion_tokens_details": completion_details,
    }


def _normalize_gemini_usage(usage: Any) -> dict[str, Any]:
    raw = _dump_usage_object(usage)
    return {
        "input_tokens": raw.get("promptTokenCount"),
        "output_tokens": raw.get("candidatesTokenCount"),
        "total_tokens": raw.get("totalTokenCount"),
        "provider_cached_input_tokens": raw.get("cachedContentTokenCount"),
        "raw": raw,
    }


def _normalize_claude_usage(usage: Any) -> dict[str, Any]:
    raw = _dump_usage_object(usage)
    cache_read = raw.get("cache_read_input_tokens")
    cache_creation = raw.get("cache_creation_input_tokens")
    return {
        "input_tokens": raw.get("input_tokens"),
        "output_tokens": raw.get("output_tokens"),
        "total_tokens": _safe_sum(raw.get("input_tokens"), raw.get("output_tokens")),
        "provider_cached_input_tokens": cache_read,
        "provider_cache_creation_input_tokens": cache_creation,
        "raw": raw,
    }


def _safe_sum(left: Any, right: Any) -> int | None:
    if isinstance(left, int) and isinstance(right, int):
        return left + right
    return None
