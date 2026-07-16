"""Editable narrative copy resolver (de-hardcode Phase 1).

Player-facing German strings resolve to a DB override (`content_texts`) else a
code default (`CONTENT_DEFAULTS`). Mirrors the `runtime_config.RuntimeConfig`
resolver pattern, except the fallback is a code constant rather than `Settings`.
"""
from n3x_bot.kodex import KODEX_TEXT

CONTENT_DEFAULTS: dict[str, str] = {
    "kodex_text": KODEX_TEXT,
    "reminder_aceball": "*EVENT REMINDER*: ACE-BALL beginnt in 30 Minuten! @everyone",
    "reminder_invasion": "*EVENT REMINDER*: Invasion beginnt in 30 Minuten! @everyone",
    "record_lucky": (
        "🍀 **Neuer Glückspilz!** <@{user}> hat den neuen Tiefpreis-Rekord "
        "für das **{name}** aufgestellt: **{cost}**"
    ),
    "record_unlucky": (
        "💀 **Neuer Pechvogel!** <@{user}> hat den neuen Höchstpreis-Rekord "
        "für das **{name}** aufgestellt: **{cost}**"
    ),
    "welcome_dm": "Willkommen {mention}!",
}

CONTENT_KEYS = frozenset(CONTENT_DEFAULTS)


class ContentTexts:
    def __init__(self, overrides: dict[str, str] | None = None):
        self._overrides = {k: v for k, v in (overrides or {}).items()
                           if k in CONTENT_KEYS}

    def get(self, key: str) -> str:
        return self._overrides.get(key, CONTENT_DEFAULTS[key])

    async def refresh(self, repo) -> None:
        raw = await repo.all_content_texts()
        self._overrides = {k: v for k, v in raw.items() if k in CONTENT_KEYS}

    @classmethod
    async def load(cls, repo) -> "ContentTexts":
        ct = cls()
        await ct.refresh(repo)
        return ct
