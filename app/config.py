import json
import secrets
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

APP_DIR = Path(__file__).resolve().parent
ROOT_DIR = APP_DIR.parent
DEV_SECRET_KEY = "dev-only-insecure-secret-change-me"
DEV_CRON_SECRET = "dev-only-cron-secret"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "The Feather Press"
    tagline: str = "Three good finds. Delivered daily."
    base_url: str = "http://localhost:8000"
    asset_base_url: str = ""  # host for emailed images; defaults to base_url
    database_url: str = f"sqlite:///{ROOT_DIR / 'data' / 'feather.db'}"

    secret_key: str = DEV_SECRET_KEY
    cron_secret: str = DEV_CRON_SECRET
    dev_tools_enabled: bool = True

    email_provider: str = "auto"  # auto | resend | console
    resend_api_key: str = ""
    email_from: str = "The Feather Press <onboarding@resend.dev>"
    email_reply_to: str = ""
    email_allowed_recipients: str = ""  # comma separated; empty means no restriction

    delivery_hour_local: int = 8
    scheduler_enabled: bool = True
    outbox_dir: Path = ROOT_DIR / "data" / "outbox"


def _generated_secrets(path: Path) -> dict[str, str]:
    """Random signing secrets kept on disk, so links survive restarts without an env var."""
    if path.exists():
        return json.loads(path.read_text())
    values = {"secret_key": secrets.token_urlsafe(48), "cron_secret": secrets.token_urlsafe(24)}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(values))
    path.chmod(0o600)
    return values


@lru_cache
def get_settings() -> Settings:
    loaded = Settings()
    if loaded.secret_key == DEV_SECRET_KEY or loaded.cron_secret == DEV_CRON_SECRET:
        generated = _generated_secrets(loaded.outbox_dir.parent / "secrets.json")
        if loaded.secret_key == DEV_SECRET_KEY:
            loaded.secret_key = generated["secret_key"]
        if loaded.cron_secret == DEV_CRON_SECRET:
            loaded.cron_secret = generated["cron_secret"]
    return loaded


settings = get_settings()

# Set explicitly (env or .env), so request hosts must not override it.
BASE_URL_CONFIGURED = "base_url" in settings.model_fields_set
