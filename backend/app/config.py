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

    allow_public_signup: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
