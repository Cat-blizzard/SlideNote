from __future__ import annotations

import base64
import json
import os
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from PIL import Image

from slidenote.llm_cache import LLM_CACHE_SCHEMA_VERSION, LLMCache, make_cache_key, sha256_text, utc_now_iso
from slidenote.modality import page_has_hint
from slidenote.models import Deck, ImageAsset, SlidePage

OCR_SCHEMA_VERSION = 1


@dataclass(slots=True)
class OCRTarget:
    slide_id: int
    kind: str
    path: str
    image_id: str | None = None
    reason: str = ""


@dataclass(slots=True)
class OCRResult:
    text: str
    usage: dict[str, Any]
    raw: dict[str, Any]


class OCRClient:
    def __init__(
        self,
        provider: str,
        api_key: str | None = None,
        secret_key: str | None = None,
        endpoint: str | None = None,
        language: str = "CHN_ENG",
    ) -> None:
        self.provider = _normalize_provider(provider)
        self.api_key = api_key
        self.secret_key = secret_key
        self.endpoint = endpoint
        self.language = language

    def recognize(self, image_path: Path) -> OCRResult:
        if self.provider == "baidu":
            return self._recognize_baidu(image_path)
        if self.provider == "mathpix":
            return self._recognize_mathpix(image_path)
        if self.provider == "google":
            return self._recognize_google(image_path)
        raise ValueError(f"Unsupported OCR provider: {self.provider}")

    def _recognize_baidu(self, image_path: Path) -> OCRResult:
        api_key = self.api_key or _first_env(("BAIDU_OCR_API_KEY", "BAIDU_API_KEY"))
        secret_key = self.secret_key or _first_env(("BAIDU_OCR_SECRET_KEY", "BAIDU_SECRET_KEY"))
        if not api_key or not secret_key:
            raise RuntimeError("Baidu OCR requires BAIDU_OCR_API_KEY and BAIDU_OCR_SECRET_KEY, or --ocr-api-key/--ocr-secret-key.")
        token = _baidu_access_token(api_key, secret_key)
        endpoint = self.endpoint or "https://aip.baidubce.com/rest/2.0/ocr/v1/accurate_basic"
        url = endpoint + ("&" if "?" in endpoint else "?") + "access_token=" + urllib.parse.quote(token)
        payload = urllib.parse.urlencode(
            {
                "image": base64.b64encode(image_path.read_bytes()).decode("ascii"),
                "language_type": self.language,
                "detect_direction": "true",
                "paragraph": "true",
            }
        ).encode("utf-8")
        data = _post(url, payload, {"Content-Type": "application/x-www-form-urlencoded"})
        if "error_code" in data:
            raise RuntimeError(f"Baidu OCR failed: {data}")
        words = [item.get("words", "") for item in data.get("words_result", [])]
        text = "\n".join(word for word in words if word)
        return OCRResult(text=text, usage={"words_result_num": data.get("words_result_num")}, raw=data)

    def _recognize_mathpix(self, image_path: Path) -> OCRResult:
        app_id = self.api_key or _first_env(("MATHPIX_APP_ID",))
        app_key = self.secret_key or _first_env(("MATHPIX_APP_KEY",))
        if not app_id or not app_key:
            raise RuntimeError("Mathpix OCR requires MATHPIX_APP_ID and MATHPIX_APP_KEY, or --ocr-api-key/--ocr-secret-key.")
        endpoint = self.endpoint or "https://api.mathpix.com/v3/text"
        src = "data:image/jpeg;base64," + base64.b64encode(image_path.read_bytes()).decode("ascii")
        payload = json.dumps(
            {
                "src": src,
                "formats": ["text", "data"],
                "data_options": {"include_asciimath": True, "include_latex": True},
            },
            ensure_ascii=False,
        ).encode("utf-8")
        data = _post(endpoint, payload, {"Content-Type": "application/json", "app_id": app_id, "app_key": app_key})
        text = data.get("text") or data.get("latex_styled") or ""
        return OCRResult(text=text, usage={"confidence": data.get("confidence")}, raw=data)

    def _recognize_google(self, image_path: Path) -> OCRResult:
        api_key = self.api_key or _first_env(("GOOGLE_VISION_API_KEY", "GOOGLE_API_KEY"))
        if not api_key:
            raise RuntimeError("Google Vision OCR requires GOOGLE_VISION_API_KEY or --ocr-api-key.")
        endpoint = self.endpoint or "https://vision.googleapis.com/v1/images:annotate"
        url = endpoint + ("&" if "?" in endpoint else "?") + "key=" + urllib.parse.quote(api_key)
        payload = json.dumps(
            {
                "requests": [
                    {
                        "image": {"content": base64.b64encode(image_path.read_bytes()).decode("ascii")},
                        "features": [{"type": "DOCUMENT_TEXT_DETECTION"}],
                        "imageContext": {"languageHints": _google_language_hints(self.language)},
                    }
                ]
            },
            ensure_ascii=False,
        ).encode("utf-8")
        data = _post(url, payload, {"Content-Type": "application/json"})
        response = (data.get("responses") or [{}])[0]
        if response.get("error"):
            raise RuntimeError(f"Google Vision OCR failed: {response['error']}")
        text = response.get("fullTextAnnotation", {}).get("text") or ""
        return OCRResult(text=text, usage={}, raw=response)


def enrich_deck_with_ocr(
    deck: Deck,
    output_root: Path,
    mode: str = "off",
    provider: str = "baidu",
    api_key: str | None = None,
    secret_key: str | None = None,
    endpoint: str | None = None,
    language: str = "CHN_ENG",
    cache_mode: str = "on",
    cache_dir: Path | None = None,
    max_targets: int = 120,
    min_text_chars: int = 80,
    min_area: int = 120_000,
    max_edge: int = 1800,
    concurrency: int = 1,
    refresh_slide_ids: set[int] | None = None,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any] | None:
    if mode == "off":
        return None
    if mode not in {"auto", "all"}:
        raise ValueError("OCR mode must be one of: off, auto, all")

    targets = select_ocr_targets(deck, output_root, mode=mode, min_text_chars=min_text_chars, min_area=min_area, max_targets=max_targets)
    resolved_cache_dir = (cache_dir or (output_root / ".cache" / "ocr")).resolve()
    cache = LLMCache(resolved_cache_dir, mode=cache_mode)
    records_by_index: dict[int, dict[str, Any]] = {}
    provider_name = _normalize_provider(provider)
    refresh_ids = refresh_slide_ids or set()
    workers = max(1, int(concurrency or 1))

    if progress_callback:
        progress_callback({"event": "start", "total": len(targets)})

    def process(index: int, target: OCRTarget) -> tuple[int, OCRTarget, dict[str, Any], str | None, str]:
        return (
            index,
            target,
            *_process_ocr_target(
                target=target,
                output_root=output_root,
                cache=cache,
                cache_mode=cache_mode,
                provider_name=provider_name,
                api_key=api_key,
                secret_key=secret_key,
                endpoint=endpoint,
                language=language,
                max_edge=max_edge,
                force_refresh=target.slide_id in refresh_ids,
            ),
        )

    results = []
    if workers == 1:
        for index, target in enumerate(targets):
            result = process(index, target)
            results.append(result)
            if progress_callback:
                _, completed_target, record, _, _ = result
                progress_callback({"event": "advance", "record": record, "slide_id": completed_target.slide_id})
    else:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(process, index, target): (index, target) for index, target in enumerate(targets)}
            for future in as_completed(futures):
                result = future.result()
                results.append(result)
                if progress_callback:
                    _, completed_target, record, _, _ = result
                    progress_callback({"event": "advance", "record": record, "slide_id": completed_target.slide_id})

    for index, target, record, text, status in sorted(results, key=lambda item: item[0]):
        _apply_ocr_result(deck, target, ocr_text=text, ocr_status=status)
        records_by_index[index] = record

    records = [records_by_index[index] for index in sorted(records_by_index)]
    return _build_report(deck, provider_name, mode, language, cache_mode, resolved_cache_dir, output_root, max_targets, min_text_chars, min_area, max_edge, records)


def _process_ocr_target(
    target: OCRTarget,
    output_root: Path,
    cache: LLMCache,
    cache_mode: str,
    provider_name: str,
    api_key: str | None,
    secret_key: str | None,
    endpoint: str | None,
    language: str,
    max_edge: int,
    force_refresh: bool,
) -> tuple[dict[str, Any], str | None, str]:
    source_path = (output_root / target.path).resolve()
    if not source_path.exists():
        return _skipped_record(target, "missing_file"), None, "missing_file"
    prepared = _prepare_image_for_ocr(source_path, max_edge=max_edge)
    if prepared is None:
        return _skipped_record(target, "unsupported_or_unreadable_image"), None, "unsupported_or_unreadable_image"

    prepared_path, image_meta = prepared
    try:
        source_hash = _file_sha256(source_path)
        cache_key_payload = {
            "schema_version": OCR_SCHEMA_VERSION,
            "provider": provider_name,
            "endpoint": endpoint,
            "language": language,
            "max_edge": max_edge,
            "source_path": target.path,
            "source_image_hash": source_hash,
        }
        cache_key = make_cache_key(cache_key_payload)
        cache_path = cache.path_for(cache_key)
        cached = None if force_refresh else cache.read(cache_key)
        record = _base_record(target, cache_key, cache_path, output_root, image_meta)

        if cached:
            text = cached["output_text"]
            provider_usage = cached.get("response_usage") or {}
            record.update(
                {
                    "cache_status": "local_hit",
                    "api_call": False,
                    "text_chars": len(text),
                    "cached_entry_usage": provider_usage,
                    "cached_at": cached.get("created_at"),
                }
            )
        else:
            client = OCRClient(provider=provider_name, api_key=api_key, secret_key=secret_key, endpoint=endpoint, language=language)
            result = client.recognize(prepared_path)
            text = result.text
            provider_usage = result.usage
            raw = result.raw
            cache_status = "disabled" if cache_mode == "off" else "refresh" if cache_mode == "refresh" or force_refresh else "miss"
            written_path = cache.write(
                cache_key,
                {
                    "provider": provider_name,
                    "endpoint": endpoint,
                    "language": language,
                    "slide_id": target.slide_id,
                    "target": asdict(target),
                    "image_meta": image_meta,
                    "output_text": text,
                    "response_usage": provider_usage,
                    "raw_response": raw,
                },
            )
            if written_path is not None:
                cache_path = written_path
            record.update(
                {
                    "cache_status": cache_status,
                    "api_call": True,
                    "text_chars": len(text),
                    "provider_usage": provider_usage,
                }
            )

        record["text_preview"] = _preview(text)
        record["cache_file"] = _display_path(cache_path, output_root)
        return record, text, "parsed"
    finally:
        _cleanup_temp_image(prepared_path)


def select_ocr_targets(
    deck: Deck,
    output_root: Path,
    mode: str,
    min_text_chars: int = 80,
    min_area: int = 120_000,
    max_targets: int = 120,
) -> list[OCRTarget]:
    targets: list[OCRTarget] = []
    for page in deck.pages:
        text_len = sum(len(block.content.strip()) for block in page.text_blocks)
        needs_page_ocr = page_has_hint(page, "ocr_page_screenshot") or text_len < min_text_chars or bool(page.warnings)
        if mode == "all" or needs_page_ocr:
            if page.page_screenshot:
                reason = "all_page_screenshot" if mode == "all" else page.page_modality or "low_extracted_text"
                targets.append(OCRTarget(page.slide_id, "page_screenshot", page.page_screenshot, reason=reason))
                continue
            targets.extend(_large_image_targets(page, output_root, min_area=0 if mode == "all" else min_area, first_only=mode != "all"))
    if max_targets > 0:
        return targets[:max_targets]
    return targets


def _large_image_targets(page: SlidePage, output_root: Path, min_area: int, first_only: bool) -> list[OCRTarget]:
    targets: list[OCRTarget] = []
    for image in page.images:
        if image.ignored:
            continue
        area = _image_area(output_root / image.path)
        if area is not None and area < min_area:
            continue
        targets.append(OCRTarget(page.slide_id, "image", image.path, image_id=image.id, reason="large_embedded_image"))
        if first_only:
            break
    return targets


def _apply_ocr_result(deck: Deck, target: OCRTarget, ocr_text: str | None = None, ocr_status: str | None = None) -> None:
    page = next((item for item in deck.pages if item.slide_id == target.slide_id), None)
    if page is None:
        return
    if target.kind == "page_screenshot":
        page.page_ocr_text = ocr_text or page.page_ocr_text
        page.page_ocr_status = ocr_status or page.page_ocr_status
        return
    if target.image_id:
        image = next((item for item in page.images if item.id == target.image_id), None)
        if image:
            image.ocr_text = ocr_text or image.ocr_text
            image.ocr_status = ocr_status or image.ocr_status


def _prepare_image_for_ocr(path: Path, max_edge: int) -> tuple[Path, dict[str, Any]] | None:
    try:
        with Image.open(path) as image:
            original = {"width": image.width, "height": image.height, "mode": image.mode, "format": image.format}
            image = image.convert("RGB")
            scale = min(1.0, max_edge / max(image.width, image.height))
            if scale < 1.0:
                image = image.resize((max(1, int(image.width * scale)), max(1, int(image.height * scale))))
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".jpg")
            tmp_path = Path(tmp.name)
            tmp.close()
            image.save(tmp_path, format="JPEG", quality=90, optimize=True)
            meta = {
                "original": original,
                "prepared": {"width": image.width, "height": image.height, "mime_type": "image/jpeg", "bytes": tmp_path.stat().st_size},
            }
            return tmp_path, meta
    except Exception:
        return None


def _cleanup_temp_image(path: Path) -> None:
    if path.parent != Path(tempfile.gettempdir()):
        return
    try:
        path.unlink()
    except OSError:
        pass


def _baidu_access_token(api_key: str, secret_key: str) -> str:
    query = urllib.parse.urlencode({"grant_type": "client_credentials", "client_id": api_key, "client_secret": secret_key})
    data = _get_json(f"https://aip.baidubce.com/oauth/2.0/token?{query}")
    token = data.get("access_token")
    if not token:
        raise RuntimeError(f"Could not get Baidu OCR access token: {data}")
    return str(token)


def _post(url: str, body: bytes, headers: dict[str, str]) -> dict[str, Any]:
    request = urllib.request.Request(url, data=body, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            response_body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"OCR request failed with HTTP {exc.code}: {error_body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"OCR request failed: {exc}") from exc
    return json.loads(response_body)


def _get_json(url: str) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(url, timeout=60) as response:
            response_body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"OCR token request failed with HTTP {exc.code}: {error_body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"OCR token request failed: {exc}") from exc
    return json.loads(response_body)


def _build_report(
    deck: Deck,
    provider: str,
    mode: str,
    language: str,
    cache_mode: str,
    cache_dir: Path,
    output_root: Path,
    max_targets: int,
    min_text_chars: int,
    min_area: int,
    max_edge: int,
    records: list[dict[str, Any]],
) -> dict[str, Any]:
    summary = {
        "targets_total": len(records),
        "local_cache_hits": sum(1 for record in records if record.get("cache_status") == "local_hit"),
        "api_calls": sum(1 for record in records if record.get("api_call")),
        "skipped": sum(1 for record in records if record.get("cache_status") == "skipped"),
        "text_chars": sum(record.get("text_chars", 0) for record in records if isinstance(record.get("text_chars"), int)),
    }
    return {
        "schema_version": OCR_SCHEMA_VERSION,
        "generated_at": utc_now_iso(),
        "source_path": deck.source_path,
        "source_type": deck.source_type,
        "provider": provider,
        "mode": mode,
        "language": language,
        "cache": {"mode": cache_mode, "dir": _display_path(cache_dir, output_root)},
        "selection": {"max_targets": max_targets, "min_text_chars": min_text_chars, "min_area": min_area, "max_edge": max_edge},
        "summary": summary,
        "targets": records,
    }


def _base_record(target: OCRTarget, cache_key: str, cache_path: Path, output_root: Path, image_meta: dict[str, Any]) -> dict[str, Any]:
    return {
        "slide_id": target.slide_id,
        "kind": target.kind,
        "image_id": target.image_id,
        "path": target.path,
        "reason": target.reason,
        "cache_key": cache_key,
        "cache_file": _display_path(cache_path, output_root),
        "image_meta": image_meta,
    }


def _skipped_record(target: OCRTarget, status: str) -> dict[str, Any]:
    return {
        "slide_id": target.slide_id,
        "kind": target.kind,
        "image_id": target.image_id,
        "path": target.path,
        "reason": target.reason,
        "cache_status": "skipped",
        "skip_reason": status,
        "api_call": False,
    }


def _image_area(path: Path) -> int | None:
    try:
        with Image.open(path) as image:
            return image.width * image.height
    except Exception:
        return None


def _file_sha256(path: Path) -> str:
    return sha256_text(path.read_bytes().hex())


def _normalize_provider(provider: str) -> str:
    aliases = {
        "baidu": "baidu",
        "baidu-ocr": "baidu",
        "百度": "baidu",
        "mathpix": "mathpix",
        "google": "google",
        "google-vision": "google",
    }
    normalized = aliases.get(provider.strip().lower())
    if not normalized:
        raise ValueError("Unsupported OCR provider. Supported providers: baidu, mathpix, google")
    return normalized


def _google_language_hints(language: str) -> list[str]:
    mapping = {"CHN_ENG": ["zh", "en"], "ENG": ["en"], "CHN": ["zh"]}
    return mapping.get(language.upper(), [language])


def _first_env(names: tuple[str, ...]) -> str | None:
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return None


def _preview(text: str, limit: int = 160) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _display_path(path: Path, output_root: Path) -> str:
    try:
        return path.resolve().relative_to(output_root.resolve()).as_posix()
    except ValueError:
        return str(path)
