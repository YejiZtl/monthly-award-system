from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parent
LOCAL_ENV_FILE = PROJECT_ROOT / ".env"


def local_env(name: str, default: str = "") -> str:
    """Read a local .env value without overriding a real process environment.

    Local preview may use service credentials from the project .env. Container
    and production deployments continue to use injected environment variables first.
    """
    if name in os.environ:
        return os.environ[name]
    if not LOCAL_ENV_FILE.is_file():
        return default
    try:
        for raw_line in LOCAL_ENV_FILE.read_text(encoding="utf-8-sig").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key.strip() == name:
                value = value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
                    value = value[1:-1]
                return value
    except OSError:
        return default
    return default


LLM_PROVIDER = local_env("LLM_PROVIDER", "openai_compatible").strip().lower()
LLM_DEFAULTS = {
    "bigmodel": {
        "base_url": "https://open.bigmodel.cn/api/paas/v4/chat/completions",
        "model": "glm-4.6v-flash",
        "temperature": "0.1",
    },
    "openai_compatible": {
        "base_url": "",
        "model": "Qwen3.5-122B-A10B",
        "temperature": "0",
    },
}
LLM_PROVIDER_DEFAULTS = LLM_DEFAULTS.get(LLM_PROVIDER, LLM_DEFAULTS["openai_compatible"])


@dataclass(frozen=True)
class Settings:
    standard_template_path: Path = Path(os.getenv("STANDARD_TEMPLATE_PATH", PROJECT_ROOT / "templates" / "excel" / "monthly_award_standard_2026_10.xlsx"))
    app_env: str = os.getenv("APP_ENV", "development")
    database_path: Path = Path(os.getenv("DATABASE_PATH", BACKEND_ROOT / "storage" / "monthly_award.sqlite3"))
    upload_root: Path = Path(os.getenv("UPLOAD_ROOT", BACKEND_ROOT / "storage" / "uploads"))
    export_root: Path = Path(os.getenv("EXPORT_ROOT", BACKEND_ROOT / "storage" / "exports"))
    rule_version: str = os.getenv("RULE_VERSION", "RV-2026.08")
    template_version: str = os.getenv("TEMPLATE_VERSION", "TMPL-2026.07")
    template_path: Path = Path(os.getenv("TEMPLATE_PATH", BACKEND_ROOT.parent / "templates" / "excel" / "monthly_award_official_v2026_07.xlsx"))
    llm_provider: str = LLM_PROVIDER
    llm_base_url: str = local_env("LLM_BASE_URL", LLM_PROVIDER_DEFAULTS["base_url"]).rstrip("/")
    llm_api_key: str = local_env("ZAI_API_KEY") if LLM_PROVIDER == "bigmodel" else local_env("LLM_API_KEY")
    llm_model: str = local_env("LLM_MODEL", LLM_PROVIDER_DEFAULTS["model"])
    llm_temperature: float = float(local_env("LLM_TEMPERATURE", LLM_PROVIDER_DEFAULTS["temperature"]))
    llm_timeout_seconds: float = float(local_env("LLM_TIMEOUT_SECONDS", "120"))
    llm_max_attempts: int = int(local_env("LLM_MAX_ATTEMPTS", "3"))
    llm_image_max_edge: int = int(local_env("LLM_IMAGE_MAX_EDGE", "2200"))
    llm_image_quality: int = int(local_env("LLM_IMAGE_QUALITY", "92"))
    llm_enable_thinking: bool = local_env("LLM_ENABLE_THINKING", "false").lower() in {"1", "true", "yes", "on"}
    prompt_version: str = local_env("PROMPT_VERSION", "PROMPT-2026.08.1")
    ocr_enabled: bool = local_env("OCR_ENABLED", "true").lower() in {"1", "true", "yes", "on"}
    ocr_engine: str = local_env("OCR_ENGINE", "RapidOCR")
    ocr_image_max_edge: int = int(local_env("OCR_IMAGE_MAX_EDGE", "2400"))
    ocr_confidence_threshold: float = float(local_env("OCR_CONFIDENCE_THRESHOLD", "0.90"))
    inline_worker: bool = os.getenv("INLINE_WORKER", "true").lower() in {"1", "true", "yes", "on"}
    reviewer_token: str = local_env("REVIEWER_TOKEN")
    admin_password: str = local_env("ADMIN_PASSWORD")
    admin_session_secret: str = local_env("ADMIN_SESSION_SECRET")
    max_file_bytes: int = 10 * 1024 * 1024
    max_batch_files: int = 30
    retention_months: int = 24

    def ensure_directories(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.upload_root.mkdir(parents=True, exist_ok=True)
        self.export_root.mkdir(parents=True, exist_ok=True)

    def validate_runtime(self) -> None:
        if self.llm_provider not in LLM_DEFAULTS:
            raise RuntimeError("LLM_PROVIDER 只支持 bigmodel 或 openai_compatible")
        if self.llm_provider == "bigmodel" and not 0 < self.llm_temperature < 1:
            raise RuntimeError("智谱 LLM_TEMPERATURE 必须在 0—1 之间且不能等于端点值")
        if self.llm_provider != "bigmodel" and not 0 <= self.llm_temperature <= 2:
            raise RuntimeError("LLM_TEMPERATURE 必须在 0—2 之间")
        if not 1 <= self.llm_max_attempts <= 5:
            raise RuntimeError("LLM_MAX_ATTEMPTS 必须在 1—5 之间")
        if not 30 <= self.llm_timeout_seconds <= 600:
            raise RuntimeError("LLM_TIMEOUT_SECONDS 必须在 30—600 秒之间")
        if not 1200 <= self.llm_image_max_edge <= 4096:
            raise RuntimeError("LLM_IMAGE_MAX_EDGE 必须在 1200—4096 之间")
        if not 75 <= self.llm_image_quality <= 100:
            raise RuntimeError("LLM_IMAGE_QUALITY 必须在 75—100 之间")
        if not 1200 <= self.ocr_image_max_edge <= 4096:
            raise RuntimeError("OCR_IMAGE_MAX_EDGE 必须在 1200—4096 之间")
        if not 0.5 <= self.ocr_confidence_threshold <= 1:
            raise RuntimeError("OCR_CONFIDENCE_THRESHOLD 必须在 0.5—1 之间")
        if self.app_env == "production":
            missing = [name for name, value in {
                "REVIEWER_TOKEN": self.reviewer_token,
                "ADMIN_PASSWORD": self.admin_password,
                "ADMIN_SESSION_SECRET": self.admin_session_secret,
            }.items() if not value]
            if missing:
                raise RuntimeError(f"生产环境缺少必要配置：{', '.join(missing)}")


settings = Settings()
