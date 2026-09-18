"""语音转写。

默认走本地 faster-whisper，不需要任何 API Key；如果配置了 openai_api_key
且把 asr_backend 设为 openai，则回退到原来的云端转写路径。
"""

import os
import threading
from pathlib import Path

from ..config import settings


class AsrError(RuntimeError):
    pass


# 模型加载一次约 1~2 秒、占用数百 MB 内存，必须复用。
_model = None
_model_lock = threading.Lock()
_resolved: dict[str, str] = {}

# os.add_dll_directory 返回的句柄一旦被回收，目录就会失效，必须持有引用。
_dll_handles: list = []


def _register_cuda_dlls() -> None:
    """pip 安装的 nvidia-*-cu12 把 DLL 藏在 site-packages 里，
    Windows 默认搜索不到，需要显式注册目录，ctranslate2 才能加载 cublas/cudnn。"""
    if os.name != "nt" or _dll_handles:
        return
    try:
        import nvidia
    except ImportError:
        return
    # nvidia 是命名空间包，没有 __file__，只能通过 __path__ 定位。
    added: list[str] = []
    for base in list(getattr(nvidia, "__path__", [])):
        for folder in Path(base).glob("*/bin"):
            try:
                _dll_handles.append(os.add_dll_directory(str(folder)))
            except (OSError, AttributeError):
                continue
            added.append(str(folder))
    # ctranslate2 内部用 LoadLibrary 加载 cublas/cudnn，不读 add_dll_directory
    # 注册的目录，必须同时写进 PATH 才能找到。
    if added:
        os.environ["PATH"] = os.pathsep.join(added + [os.environ.get("PATH", "")])

# ctranslate2 在构造阶段不校验 CUDA 运行库，直到真正推理才抛错，
# 所以 GPU 不可用的判定要同时覆盖加载和推理两个阶段。
_CUDA_ERROR_MARKERS = ("cublas", "cudnn", "cuda", "gpu")


def _looks_like_cuda_error(exc: Exception) -> bool:
    message = f"{type(exc).__name__}: {exc}".lower()
    return any(marker in message for marker in _CUDA_ERROR_MARKERS)


def _pick_model(device: str) -> str:
    """auto 时按设备选模型：GPU 用 medium 保准确率，CPU 用 small 保速度。"""
    configured = (settings.asr_model or "auto").strip()
    if configured.lower() != "auto":
        return configured
    return "medium" if device == "cuda" else "small"


def _force_cpu(reason: str) -> None:
    """放弃 GPU，后续一律用 CPU int8。"""
    global _model
    with _model_lock:
        _model = None
        _resolved.clear()
        _resolved.update(device="cpu", compute_type="int8", fallback=reason[:200])


def _resolve_device() -> tuple[str, str]:
    """优先使用 GPU，环境不满足时回退 CPU int8。"""
    preference = (settings.asr_device or "auto").strip().lower()
    if preference == "cpu":
        return "cpu", "int8"
    _register_cuda_dlls()
    try:
        import ctranslate2

        if ctranslate2.get_cuda_device_count() > 0:
            return "cuda", settings.asr_compute_type or "float16"
    except Exception:  # noqa: BLE001 - 探测失败一律按无 GPU 处理
        pass
    if preference == "cuda":
        raise AsrError(
            "配置要求使用 CUDA，但当前环境未检测到可用的 NVIDIA GPU。"
            "请把 ASR_DEVICE 改为 auto 或 cpu。"
        )
    return "cpu", "int8"


def _load_model():
    global _model
    if _model is not None:
        return _model
    with _model_lock:
        if _model is not None:
            return _model
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise AsrError(
                "本地语音转写组件未安装。请在 backend 目录执行："
                " .venv\\Scripts\\python.exe -m pip install faster-whisper"
            ) from exc

        # _force_cpu 已写入结论时不再重复探测，否则会又选回 cuda。
        if _resolved.get("device"):
            device = _resolved["device"]
            compute_type = _resolved.get("compute_type", "int8")
        else:
            device, compute_type = _resolve_device()
        model_name = _pick_model(device)
        try:
            model = WhisperModel(model_name, device=device, compute_type=compute_type)
            _resolved.update(device=device, compute_type=compute_type, model=model_name)
        except Exception as exc:  # noqa: BLE001
            if device != "cuda":
                raise AsrError(f"加载本地语音模型失败：{type(exc).__name__}: {exc}") from exc
            # CUDA 依赖缺失（如 cublas64_12.dll）时不能让整个流程失败，退回 CPU。
            try:
                fallback_name = _pick_model("cpu")
                model = WhisperModel(fallback_name, device="cpu", compute_type="int8")
                _resolved.update(
                    device="cpu",
                    compute_type="int8",
                    model=fallback_name,
                    fallback=str(exc)[:200],
                )
            except Exception as cpu_exc:  # noqa: BLE001
                raise AsrError(
                    f"本地语音模型加载失败：{type(cpu_exc).__name__}: {cpu_exc}"
                ) from cpu_exc
        _model = model
        return _model


def asr_status() -> dict:
    return {
        "backend": settings.asr_backend,
        "model": _resolved.get("model") or settings.asr_model,
        "loaded": _model is not None,
        **_resolved,
    }


def _decode(model, path: Path) -> dict:
    segments, info = model.transcribe(
        str(path),
        language=settings.asr_language or None,
        vad_filter=True,
        beam_size=5,
        # 引导简体输出，否则中文容易出繁体字。
        initial_prompt=(
            "以下是普通话面试或表达练习的逐字转写。请使用简体中文，"
            "保留“嗯、呃、那个、就是、然后”等真实语气词和口头停顿，"
            "不要把口语润色成书面语。常见术语包括软件测试、硬件测试、"
            "机器人、物联网、测试用例、需求评审、异常场景、接口测试、"
            "自动化测试、性能测试、AI 智能体、大模型、幻觉、缺陷、日志、"
            "监控、回归测试、小程序、Web 页面。"
        ),
        # 长音频按片段独立解码，避免上一段错误累积传播。
        condition_on_previous_text=False,
    )

    texts: list[str] = []
    collected: list[dict] = []
    for seg in segments:
        text = (seg.text or "").strip()
        if not text:
            continue
        texts.append(text)
        collected.append(
            {
                "start": round(float(seg.start), 2),
                "end": round(float(seg.end), 2),
                "text": text,
            }
        )
    return {
        "text": "".join(texts),
        "segments": collected,
        "language": getattr(info, "language", "") or "",
    }


def _transcribe_local(path: Path) -> dict:
    model = _load_model()
    try:
        return _decode(model, path)
    except Exception as exc:  # noqa: BLE001
        # 显存不足、缺 cublas/cudnn 等只在推理时暴露，此时降级到 CPU 重试一次。
        if _resolved.get("device") == "cuda" and _looks_like_cuda_error(exc):
            _force_cpu(str(exc))
            return _decode(_load_model(), path)
        raise


def _transcribe_openai(path: Path) -> dict:
    from openai import OpenAI

    if not settings.openai_api_key:
        raise AsrError("未配置 OPENAI_API_KEY，无法调用云端语音转写。")
    client = OpenAI(api_key=settings.openai_api_key, base_url=settings.openai_base_url)
    with path.open("rb") as f:
        kwargs: dict = {
            "model": settings.whisper_model,
            "file": f,
            "response_format": "verbose_json",
        }
        if settings.asr_language:
            kwargs["language"] = settings.asr_language
        resp = client.audio.transcriptions.create(**kwargs)
    data = resp.model_dump() if hasattr(resp, "model_dump") else dict(resp)
    if data.get("segments") is None:
        data["segments"] = []
    return data


def transcribe_file(path: Path) -> dict:
    if (settings.asr_backend or "local").strip().lower() == "openai":
        return _transcribe_openai(path)
    return _transcribe_local(path)


def join_transcripts(parts: list[str]) -> str:
    """长音频会切多片，中文片段之间直接相连，只在英文/数字之间补空格。"""
    merged = ""
    for part in parts:
        piece = (part or "").strip()
        if not piece:
            continue
        if merged and merged[-1].isascii() and piece[0].isascii():
            merged += " "
        merged += piece
    return merged
