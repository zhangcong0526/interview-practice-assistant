from fastapi import APIRouter, HTTPException

from ..schemas import LlmConfigTestRequest, LlmConfigUpdate
from ..services import llm_config

router = APIRouter(prefix="/api/config", tags=["config"])


@router.get("/llm")
async def read_llm_config():
    return llm_config.get_config()


@router.post("/llm")
async def update_llm_config(request: LlmConfigUpdate):
    try:
        return llm_config.update_config(request)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except OSError as exc:
        raise HTTPException(500, f"保存模型配置失败：{exc}") from exc


@router.post("/llm/test")
async def test_llm_config(request: LlmConfigTestRequest):
    try:
        return llm_config.test_config(request)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
