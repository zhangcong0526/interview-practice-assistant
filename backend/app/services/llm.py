import json
import re
import logging
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

from openai import OpenAI

from ..config import settings


# LLM 调用日志，便于排查 MiniMax 等慢模型的超时/截断问题。
LOG_DIR = Path(__file__).resolve().parents[2] / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
llm_logger = logging.getLogger("interview_assistant.llm")
if not llm_logger.handlers:
    _handler = RotatingFileHandler(
        LOG_DIR / "llm.log",
        maxBytes=2 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    _handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    llm_logger.addHandler(_handler)
    llm_logger.setLevel(logging.INFO)


class LlmError(RuntimeError):
    pass


# SDK 默认超时 600 秒且失败自动重试 2 次；上游卡住时前端会长时间转圈，
# 因此对组卷、对话等长任务显式设置有界超时，并关闭 SDK 层隐藏重试。
CHAT_TIMEOUT_SECONDS = 120.0
DIALOGUE_TIMEOUT_SECONDS = 60.0


def _provider_config(provider: str | None = None) -> tuple[str, str, str]:
    provider = provider or settings.llm_provider
    if provider == "deepseek":
        if not settings.deepseek_api_key:
            raise LlmError("未配置 DEEPSEEK_API_KEY。请在 backend/.env 中设置。")
        return settings.deepseek_api_key, settings.deepseek_base_url, settings.deepseek_model
    if provider == "ark":
        if not settings.ark_api_key:
            raise LlmError("未配置 ARK_API_KEY。请在模型配置页填写。")
        if not settings.ark_model:
            raise LlmError("未配置 ARK_MODEL。请填写火山方舟 Model ID 或接入点 ID。")
        return settings.ark_api_key, settings.ark_base_url, settings.ark_model
    if provider == "minimax":
        if not settings.minimax_api_key:
            raise LlmError("未配置 MINIMAX_API_KEY。请在模型配置页填写。")
        if not settings.minimax_model:
            raise LlmError("未配置 MINIMAX_MODEL。请填写 MiniMax 模型名。")
        return settings.minimax_api_key, settings.minimax_base_url, settings.minimax_model
    if provider == "openai":
        if not settings.openai_api_key:
            raise LlmError("未配置 OPENAI_API_KEY。请在 backend/.env 中设置。")
        return settings.openai_api_key, settings.openai_base_url, settings.openai_model
    raise LlmError(f"不支持的 LLM_PROVIDER: {provider}")


def _completion(client: OpenAI, *, model: str, messages: list[dict], temperature: float, max_tokens: int, json_mode: bool):
    kwargs = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    try:
        return client.chat.completions.create(**kwargs)
    except Exception as exc:
        # MiniMax/Ark 兼容接口的 JSON Mode 支持范围可能随模型变化；提示词本身已要求 JSON，
        # 因此仅在服务端明确不接受 response_format 时退回普通补全。
        text = str(exc).lower()
        if json_mode and any(
            keyword in text
            for keyword in ("response_format", "json_object", "json mode", "unsupported parameter")
        ):
            kwargs.pop("response_format", None)
            return client.chat.completions.create(**kwargs)
        raise


def chat_json(
    system: str,
    user: str,
    max_tokens: int = 8000,
    *,
    provider: str | None = None,
    model: str | None = None,
    timeout: float | None = None,
) -> dict:
    api_key, base_url, resolved_model = _provider_config(provider)
    provider_name = provider or settings.llm_provider
    if timeout is None:
        if provider_name == "minimax":
            try:
                timeout = float(getattr(settings, "minimax_timeout_seconds", 180))
            except (TypeError, ValueError):
                timeout = 180.0
        else:
            timeout = CHAT_TIMEOUT_SECONDS
    client = OpenAI(
        api_key=api_key,
        base_url=base_url,
        timeout=timeout,
        max_retries=0,
    )
    llm_logger.info(
        "call start provider=%s model=%s max_tokens=%d timeout=%.1f",
        provider_name,
        model or resolved_model,
        max_tokens,
        timeout,
    )
    start_ts = time.perf_counter()
    try:
        resp = _completion(
            client,
            model=model or resolved_model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.3,
            max_tokens=max_tokens,
            json_mode=True,
        )
    except Exception as exc:
        raise LlmError(f"调用 LLM 失败: {exc}") from exc
    choice = resp.choices[0]
    content = choice.message.content or ""
    if choice.finish_reason == "length":
        raise LlmError(
            "模型输出达到长度上限被截断，返回的 JSON 不完整。请缩短输入内容后重试。"
        )
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            resp_iter = _completion(
                client,
                model=model or resolved_model,
                messages=messages,
                temperature=0.3 if attempt == 0 else 0.2,
                max_tokens=max_tokens,
                json_mode=True,
            )
        except Exception as exc:
            raise LlmError(f"调用 LLM 失败: {exc}") from exc
        choice_iter = resp_iter.choices[0]
        content_iter = choice_iter.message.content or ""
        elapsed = time.perf_counter() - start_ts
        llm_logger.info(
            "call attempt=%d provider=%s model=%s elapsed=%.2f finish=%s content_len=%d",
            attempt,
            provider_name,
            model or resolved_model,
            elapsed,
            choice_iter.finish_reason,
            len(content_iter),
        )
        if choice_iter.finish_reason == "length":
            raise LlmError(
                "模型输出达到长度上限被截断，返回的 JSON 不完整。请缩短输入内容后重试。"
            )
        try:
            return _parse_json(content_iter)
        except LlmError as exc:
            last_error = exc
            # 第一次失败时追加一条只输出严格 JSON 的指令再试一次。
            messages = messages + [
                {
                    "role": "user",
                    "content": (
                        "上面输出无法解析为 JSON，请只输出严格合法的 JSON，"
                        "不要包含<think>、解释、注释或前后缀文字。"
                    ),
                }
            ]
    llm_logger.warning(
        "call failed provider=%s model=%s last_error=%s",
        provider_name,
        model or resolved_model,
        str(last_error)[:200] if last_error else "",
    )
    raise LlmError(str(last_error) if last_error else "LLM 返回内容无法解析为 JSON。")


def chat_json_messages(
    messages: list[dict],
    temperature: float = 0.6,
    max_tokens: int = 2000,
    retries: int = 2,
) -> dict:
    """多轮对话版本，用于模拟面试官逐轮提问。"""
    api_key, base_url, model = _provider_config()
    client = OpenAI(
        api_key=api_key,
        base_url=base_url,
        timeout=DIALOGUE_TIMEOUT_SECONDS,
        max_retries=0,
    )
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            resp = _completion(
                client,
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                json_mode=True,
            )
        except Exception as exc:
            raise LlmError(f"调用 LLM 失败: {exc}") from exc
        choice = resp.choices[0]
        content = choice.message.content or ""
        if choice.finish_reason == "length":
            # 截断的 JSON 无论重试几次都解析不了，直接报出真实原因。
            raise LlmError(
                "模型输出达到长度上限被截断，返回的 JSON 不完整。请缩短输入内容后重试。"
            )
        if not content.strip():
            # 空白回复通常意味着对话结构异常（例如连续同角色消息）。
            last_error = LlmError("LLM 返回了空内容，请检查对话消息结构。")
            if attempt == retries:
                break
            continue
        try:
            return _parse_json(content)
        except LlmError as exc:
            # 模型偶发返回非严格 JSON，重试一次通常即可恢复。
            last_error = exc
            if attempt == retries:
                break
    raise LlmError(str(last_error) if last_error else "LLM 返回内容无法解析为 JSON。")


def _parse_json(content: str) -> dict:
    # MiniMax 等思考型模型会在正式 JSON 前先输出 <think>...</think>，
    # 解析前先把这部分去掉，避免被误判成非 JSON。
    content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL)
    content = content.strip()
    if content.startswith("```"):
        content = re.sub(r"^```[a-zA-Z]*\s*", "", content)
        content = re.sub(r"\s*```$", "", content).strip()
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass
    # 常见小毛病：尾随逗号、Markdown 残留，先修复再尝试解析。
    repaired = re.sub(r",(\s*[}\]])", r"\1", content)
    if repaired != content:
        try:
            return json.loads(repaired)
        except json.JSONDecodeError:
            pass
    match = re.search(r"\{.*\}", content, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass
    # 逐字符扫描出第一个完整的顶层 JSON 对象，容忍尾部多余内容。
    start = content.find("{")
    if start != -1:
        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(content)):
            char = content[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(content[start : index + 1])
                    except json.JSONDecodeError:
                        break
    raise LlmError("LLM 返回内容无法解析为 JSON，请重试。")
