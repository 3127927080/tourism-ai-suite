from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


class Settings(BaseSettings):
    """AI Service 运行配置。"""

    ai_database_url: str = (
        "postgresql+psycopg://customer_service:customer_service"
        "@127.0.0.1:5432/ai_customer_service"
    )
    api_host: str = "0.0.0.0"
    api_port: int = 8002
    internal_service_token: str = "customer-internal-token"
    jwt_secret: str = "ecommerce-secret"
    jwt_algorithm: str = "HS256"
    llm_provider: Literal["openai_compatible", "deepseek", "qwen"] = "openai_compatible"
    # 旅游数据服务（tourism-service，8001）
    tourism_data_base_url: str = ""
    tourism_data_timeout_seconds: float | None = None
    # 兼容旧变量：未配置 TOURISM_DATA_BASE_URL 时回退到 ECOMMERCE_BASE_URL
    ecommerce_base_url: str = "http://127.0.0.1:8001/api/v1"
    ecommerce_timeout_seconds: float | None = None
    knowledge_base_url: str = "http://127.0.0.1:8005/api/v1"
    knowledge_timeout_seconds: float = Field(default=45.0, gt=0)
    knowledge_top_k: int = Field(default=5, ge=1, le=10)
    knowledge_service_token: str = ""
    llm_model: str = ""
    llm_api_key: str = ""
    llm_base_url: str = "https://api.deepseek.com"
    llm_thinking_mode: Literal["disabled", "enabled"] = "disabled"
    llm_timeout_seconds: float = Field(default=30, gt=0)
    history_message_limit: int = Field(default=30, ge=1, le=60)
    history_character_budget: int = Field(default=12000, ge=1)
    prompt_version: str = "tourism-support-v1"
    agent_correction_limit: int = Field(default=2, ge=1)
    # 单次 Run 内的工具调用上限与整体超时
    agent_tool_call_limit: int = Field(default=8, ge=1)
    agent_run_timeout_seconds: float = Field(default=90.0, gt=0)
    # 流式输出时每次下发的文本块字符数
    stream_chunk_size: int = Field(default=3, ge=1)

    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        extra="ignore"
    )

    @property
    def resolved_tourism_data_base_url(self) -> str:
        """优先使用 TOURISM_DATA_BASE_URL，未配置时回退到旧 ECOMMERCE_BASE_URL。"""
        return (
            self.tourism_data_base_url
            or self.ecommerce_base_url
            or "http://127.0.0.1:8001/api/v1"
        )

    @property
    def resolved_tourism_timeout(self) -> float:
        """旅游数据服务超时，优先新变量。"""
        return (
            self.tourism_data_timeout_seconds
            or self.ecommerce_timeout_seconds
            or 8.0
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
