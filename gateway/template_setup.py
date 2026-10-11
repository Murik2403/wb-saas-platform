"""Shared metadata/consent context for public, billing and admin templates."""
from pathlib import Path
from fastapi.templating import Jinja2Templates
import config
import growth_web


def create_templates() -> Jinja2Templates:
    templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))
    templates.env.globals.update(
        site_base_url=config.base_url,
        yandex_metrika_id=config.YANDEX_METRIKA_ID,
        yandex_verification=config.YANDEX_VERIFICATION,
        google_verification=config.GOOGLE_VERIFICATION,
        analytics_consent=growth_web.consent_choice,
        analytics_available=growth_web.optional_analytics_available,
        public_metrika_counter=growth_web.metrika_counter,
        telegram_available=lambda: bool(config.TELEGRAM_ENABLED and config.TELEGRAM_BOT_TOKEN),
    )
    return templates
