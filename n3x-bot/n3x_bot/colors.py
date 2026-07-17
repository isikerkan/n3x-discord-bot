"""Editable achievement TIER / CATEGORY colour resolver (de-hardcode Phase 3).

Achievement colours resolve to a DB override (`color_config`) else the code
default from `cards`. Mirrors the `content.ContentTexts` resolver pattern, except
overrides MERGE onto the ordered `cards` colour tables (a single tier override
recolours only that tier; every other tier/category keeps its default) rather
than filtering to a fixed key set.
"""
from n3x_bot.cards import (
    ACTIVITY_CATEGORY_COLORS,
    GATE_TIER_COLORS,
    _parse_hex_color,
)

WHITE = (255, 255, 255)


class ColorConfig:
    def __init__(self, overrides: dict[str, str] | None = None) -> None:
        self._overrides = dict(overrides or {})

    def tier_color(self, title: str) -> tuple[int, int, int]:
        t = title.lower()
        for key, color in GATE_TIER_COLORS:
            if key in t:
                parsed = _parse_hex_color(self._overrides.get(f"tier:{key}"))
                return parsed if parsed is not None else color
        return WHITE

    def category_color(self, category: str) -> tuple[int, int, int]:
        parsed = _parse_hex_color(self._overrides.get(f"category:{category}"))
        if parsed is not None:
            return parsed
        return ACTIVITY_CATEGORY_COLORS.get(category, WHITE)

    async def refresh(self, repo) -> None:
        self._overrides = dict(await repo.all_color_config())

    @classmethod
    async def load(cls, repo) -> "ColorConfig":
        cfg = cls()
        await cfg.refresh(repo)
        return cfg
