from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # LLM 提供方：deepseek | ark | minimax | openai
    llm_provider: str = "deepseek"
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"
    ark_api_key: str = ""
    ark_base_url: str = "https://ark.cn-beijing.volces.com/api/v3"
    ark_model: str = "doubao-seed-2-1-pro-260628"
    minimax_api_key: str = ""
    minimax_base_url: str = "https://api.minimaxi.com/v1"
    minimax_model: str = "MiniMax-M2"
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4o-mini"

    # 语音转写
    # local：本地 faster-whisper，无需 API Key（默认）
    # openai：调用 OpenAI Whisper，需要 openai_api_key
    asr_backend: str = "local"
    # tiny / base / small / medium / large-v3，越大越准也越慢
    # auto：有 GPU 用 medium，否则用 small；也可显式指定 tiny/base/small/medium/large-v3
    asr_model: str = "auto"
    # auto：有 GPU 用 GPU，否则 CPU；也可强制 cuda / cpu
    asr_device: str = "auto"
    asr_compute_type: str = "float16"
    whisper_model: str = "whisper-1"
    # 留空自动检测语言；也可设为 zh / en
    asr_language: str = ""

    # 上传
    max_upload_size_mb: int = 2048
    upload_chunk_mb: int = 8

    # OpenAI Whisper API 单次请求硬限制 25MB，留安全余量
    whisper_max_bytes: int = 24 * 1024 * 1024

    data_dir: Path = Path(__file__).resolve().parent.parent / "data"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


settings = Settings()
