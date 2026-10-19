from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="KP_", extra="ignore")

    app_name: str = "智能文档与知识工作台"
    environment: str = "development"
    debug: bool = False

    database_url: str = "postgresql+psycopg://kp:kp@localhost:5432/kp"

    session_cookie_name: str = "kp_session"
    session_max_age_seconds: int = 60 * 60 * 24 * 7
    secret_key: str = "change-me-in-deployment"
    # 本地 Mac mini 验收通常只有 HTTP，没有 HTTPS；公网部署时须显式开启。
    session_cookie_secure: bool = False

    storage_root: str = "./data/documents"
    max_upload_bytes: int = 20 * 1024 * 1024

    # 为空时使用确定性替身，仅供开发与自动化测试。
    embedding_base_url: str = ""
    embedding_model: str = "BAAI/bge-m3"
    embedding_api_key: str = ""

    retrieval_top_k: int = 6
    answer_model: str = "deepseek-chat"
    answer_base_url: str = ""
    answer_api_key: str = ""

    allow_public_signup: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
