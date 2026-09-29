"""Translation support for user-facing pages and emails (Flask-Babel).

Translations share the Transifex project used by trends.earth-api-ui.
"""

from pathlib import Path

from flask import has_request_context, request
from flask_babel import Babel, get_locale

SUPPORTED_LANGUAGES = {
    "en": "English",
    "ar": "العربية",
    "es": "Español",
    "fa": "فارسی",
    "fr": "Français",
    "pt": "Português",
    "ru": "Русский",
    "sw": "Kiswahili",
    "zh": "中文",
}
RTL_LANGUAGES = frozenset({"ar", "fa"})
DEFAULT_LANGUAGE = "en"
# Same cookie name as the API UI so a choice made there carries over.
LANGUAGE_COOKIE_NAME = "trendsearth_language"
TRANSLATIONS_DIR = Path(__file__).parent / "translations"


def N_(message):  # noqa: N802  # gettext convention; babel.cfg extracts -k N_
    """Mark a message for extraction; it is translated later where it is shown."""
    return message


def _supported(value):
    """Return the first supported language in a space/comma separated list."""
    for tag in (value or "").replace(",", " ").split():
        code = tag.split("-")[0].split("_")[0].lower()
        if code in SUPPORTED_LANGUAGES:
            return code
    return None


def select_locale():
    """Pick a language: ui_locales/lang parameter, cookie, Accept-Language."""
    if not has_request_context():
        return DEFAULT_LANGUAGE
    return (
        _supported(request.values.get("ui_locales"))
        or _supported(request.values.get("lang"))
        or _supported(request.cookies.get(LANGUAGE_COOKIE_NAME))
        or request.accept_languages.best_match(SUPPORTED_LANGUAGES.keys())
        or DEFAULT_LANGUAGE
    )


def current_language():
    locale = get_locale()
    return locale.language if locale else DEFAULT_LANGUAGE


def text_direction():
    return "rtl" if current_language() in RTL_LANGUAGES else "ltr"


def init_i18n(app):
    app.config.setdefault("BABEL_DEFAULT_LOCALE", DEFAULT_LANGUAGE)
    app.config.setdefault("BABEL_TRANSLATION_DIRECTORIES", str(TRANSLATIONS_DIR))
    Babel(app, locale_selector=select_locale)
    app.jinja_env.globals.update(
        current_language=current_language,
        text_direction=text_direction,
        supported_languages=SUPPORTED_LANGUAGES,
    )
