"""LLM runtime configuration with local persistence in backend/.env."""

import os
import re
import threading
import time
from pathlib import Path

from openai import OpenAI

from ..config import settings
from ..schemas import LlmConfigTestRequest, LlmConfigUpdate


ENV_PATH = Path(__file__).resolve().parents[2] / ".env"
ENV_PATTERN = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)(\s*=\s*)(.*)$")

FIELD_ENV_KEYS = {
    "deepseek_api_key": "DEEPSEEK_API_KEY",
    "deepseek_base_url": "DEEPSEEK_BASE_URL",
    "deepseek_model": "DEEPSEEK_MODEL",
    "openai_api_key": "OPENAI_API_KEY",
    "openai_base_url": "OPENAI_BASE_URL",
    "openai_model": "OPENAI_MODEL",
    "ark_api_key": "ARK_API_KEY",
    "ark_base_url": "ARK_BASE_URL",
    "ark_model": "ARK_MODEL",
    "minimax_api_key": "MINIMAX_API_KEY",
    "minimax_base_url": "MINIMAX_BASE_URL",
    "minimax_model": "MINIMAX_MODEL",
}

PROVIDER_FIELDS = {
    "deepseek": ("deepseek_api_key", "deepseek_base_url", "deepseek_model"),
    "ark": ("ark_api_key", "ark_base_url", "ark_model"),
    "minimax": ("minimax_api_key", "minimax_base_url", "minimax_model"),
    "openai": ("openai_api_key", "openai_base_url", "openai_model"),
}

_write_lock = threading.RLock()


def _mask_key(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 10:
        return "***"
    return f"{value[:3]}***{value[-4:]}"


def _provider_status(provider: str) -> dict:
    fields = PROVIDER_FIELDS.get(provider)
    if not fields:
        raise ValueError(f"不支持的模型厂商: {provider}")
    key_field, base_url_field, model_field = fields
    key = getattr(settings, key_field)
    base_url = getattr(settings, base_url_field)
    model = getattr(settings, model_field)
    return {
        "configured": bool(key),
        "masked_key": _mask_key(key),
        "base_url": base_url,
        "model": model,
    }


def get_config() -> dict:
    return {
        "provider": settings.llm_provider,
        "configured": bool(_provider_status(settings.llm_provider)["configured"]),
        "deepseek": _provider_status("deepseek"),
        "ark": _provider_status("ark"),
        "minimax": _provider_status("minimax"),
        "openai": _provider_status("openai"),
    }


def update_config(request: LlmConfigUpdate) -> dict:
    values: dict[str, str] = {"LLM_PROVIDER": request.provider}

    for field, env_key in FIELD_ENV_KEYS.items():
        value = str(getattr(request, field) or "").strip()
        if not value:
            # 空密钥表示保留原值，避免界面刷新或误触清空本地凭据。
            continue
        if field.endswith("_api_key"):
            if len(value) > 500:
                raise ValueError("API Key 长度超过限制。")
        elif field.endswith("_base_url") and not value.startswith(("http://", "https://")):
            raise ValueError("Base URL 必须以 http:// 或 https:// 开头。")
        elif field.endswith("_model") and len(value) > 200:
            raise ValueError("模型名称过长。")
        values[env_key] = value

    _write_env(values)
    _apply_runtime(values)
    return get_config()


def _apply_runtime(values: dict[str, str]) -> None:
    settings.llm_provider = values.get("LLM_PROVIDER", settings.llm_provider)
    settings.deepseek_api_key = values.get("DEEPSEEK_API_KEY", settings.deepseek_api_key)
    settings.deepseek_base_url = values.get("DEEPSEEK_BASE_URL", settings.deepseek_base_url)
    settings.deepseek_model = values.get("DEEPSEEK_MODEL", settings.deepseek_model)
    settings.openai_api_key = values.get("OPENAI_API_KEY", settings.openai_api_key)
    settings.openai_base_url = values.get("OPENAI_BASE_URL", settings.openai_base_url)
    settings.openai_model = values.get("OPENAI_MODEL", settings.openai_model)
    settings.ark_api_key = values.get("ARK_API_KEY", settings.ark_api_key)
    settings.ark_base_url = values.get("ARK_BASE_URL", settings.ark_base_url)
    settings.ark_model = values.get("ARK_MODEL", settings.ark_model)
    settings.minimax_api_key = values.get("MINIMAX_API_KEY", settings.minimax_api_key)
    settings.minimax_base_url = values.get("MINIMAX_BASE_URL", settings.minimax_base_url)
    settings.minimax_model = values.get("MINIMAX_MODEL", settings.minimax_model)


def _write_env(values: dict[str, str]) -> None:
    with _write_lock:
        try:
            lines = ENV_PATH.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            lines = []
        except UnicodeDecodeError as exc:
            raise ValueError("backend/.env 不是有效的 UTF-8 文本，请修复后再配置。") from exc

        seen: set[str] = set()
        for index, line in enumerate(lines):
            match = ENV_PATTERN.match(line)
            if not match:
                continue
            key = match.group(1)
            if key in values:
                lines[index] = f"{key}={values[key]}"
                seen.add(key)

        missing = [key for key in values if key not in seen]
        if missing:
            if lines and lines[-1].strip():
                lines.append("")
            lines.extend(f"{key}={values[key]}" for key in missing)

        temp_path = ENV_PATH.with_name(f".env.tmp-{os.getpid()}")
        try:
            ENV_PATH.parent.mkdir(parents=True, exist_ok=True)
            temp_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
            temp_path.replace(ENV_PATH)
        finally:
            temp_path.unlink(missing_ok=True)


def test_config(request: LlmConfigTestRequest) -> dict:
    status = _provider_status(request.provider)
    api_key = request.api_key.strip()
    if not api_key:
        key_field = PROVIDER_FIELDS[request.provider][0]
        api_key = getattr(settings, key_field)
    base_url = request.base_url.strip() or status["base_url"]
    model = request.model.strip() or status["model"]
    if not api_key:
        raise ValueError("请先填写或保存 API Key。")
    if not base_url:
        raise ValueError("Base URL 不能为空。")
    if not model:
        raise ValueError("模型名不能为空。")

    client = OpenAI(api_key=api_key, base_url=base_url, timeout=15.0, max_retries=0)
    started = time.perf_counter()
    try:
        client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "ping"}],
            temperature=0,
            max_tokens=1,
        )
    except Exception as exc:
        raise ValueError(f"连通性测试失败：{exc}") from exc

    latency_ms = round((time.perf_counter() - started) * 1000)
    return {
        "ok": True,
        "provider": request.provider,
        "base_url": base_url,
        "model": model,
        "latency_ms": latency_ms,
        "message": "连通性正常，模型可调用。",
    }
def test_config(request) -> dict:
    status = _provider_status(request.provider)
    api_key = request.api_key.strip()
    if not api_key:
        key_field = PROVIDER_FIELDS[request.provider][0]
        api_key = getattr(settings, key_field)
    base_url = request.base_url.strip() or status["base_url"]
    model = request.model.strip() or status["model"]
    if not api_key:
        raise ValueError("请先填写或保存 API Key。")
    if not base_url:
        raise ValueError("Base URL 不能为空。")
    if not model:
        raise ValueError("模型名不能为空。")

    client = OpenAI(api_key=api_key, base_url=base_url, timeout=15.0, max_retries=0)
    started = time.perf_counter()
    try:
        client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "ping"}],
            temperature=0,
            max_tokens=1,
        )
    except Exception as exc:
        raise ValueError(f"连通性测试失败：{exc}") from exc

    latency_ms = round((time.perf_counter() - started) * 1000)
    return {
        "ok": True,
        "provider": request.provider,
        "base_url": base_url,
        "model": model,
        "latency_ms": latency_ms,
        "message": "连通性正常，模型可调用。",
    }
