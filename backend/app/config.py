from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="KP_", extra="ignore")

    app_name: str = "智能文档与知识工作台"
    environment: str = "development"
    debug: bool = False

    database_url: str = "postgresql+psycopg://kp:kp@localhost:5432/kp"

    # 应用连接建立后切换到该角色，使行级安全真正生效；留空表示不切换。
    # 该角色不能是超级用户、表所有者，也不能带 BYPASSRLS。
    rls_role: str = "kp_app"

    session_cookie_name: str = "kp_session"
    session_max_age_seconds: int = 60 * 60 * 24 * 7
    secret_key: str = "change-me-in-deployment"
    # 本地 Mac mini 验收通常只有 HTTP，没有 HTTPS；公网部署时须显式开启。
    session_cookie_secure: bool = False

    # 浏览器写请求必须来自这些来源之一；不匹配或缺失一律拒绝。
    allowed_origins: list[str] = ["http://localhost:5173", "http://localhost:8080"]
    csrf_cookie_name: str = "kp_csrf"
    # 未登录时的预登录 CSRF 令牌有效期。
    csrf_prelogin_seconds: int = 30 * 60

    storage_root: str = "./data/documents"
    max_upload_bytes: int = 20 * 1024 * 1024
    # 每团队原始文件总容量与文档数量上限，和单文件限制分开。
    team_max_bytes: int = 5 * 1024 * 1024 * 1024
    team_max_documents: int = 500

    # 为空时使用确定性替身，仅供开发与自动化测试。
    embedding_base_url: str = ""
    embedding_model: str = "BAAI/bge-m3"
    embedding_api_key: str = ""

    retrieval_top_k: int = 6
    answer_model: str = "deepseek-chat"
    answer_base_url: str = ""
    answer_api_key: str = ""
    # 同步问答请求中途崩溃时，遗留 answering 轮次超过此窗口后收敛为 interrupted。
    turn_timeout_seconds: int = 180

    allow_public_signup: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
