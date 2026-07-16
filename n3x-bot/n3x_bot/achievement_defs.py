"""DB-backed achievement-definition resolver (de-hardcode Phase 2a).

Achievement DEFINITIONS resolve to DB overrides (`achievement_defs` table) else
the code default `ACHIEVEMENTS` list. Mirrors `content.ContentTexts`, except a
non-empty table fully replaces the code list. Behaviour is preserved when the
table is empty.
"""
from n3x_bot.achievements import ACHIEVEMENTS, Achievement


class AchievementDefs:
    def __init__(self, defs: list[Achievement] | None = None):
        self._defs = list(defs) if defs is not None else list(ACHIEVEMENTS)

    def all(self) -> list[Achievement]:
        return list(self._defs)

    @property
    def total(self) -> int:
        return len(self._defs)

    def for_metric(self, metric: str) -> list[Achievement]:
        return [a for a in self._defs if a.metric == metric]

    def by_id(self, aid: str) -> Achievement | None:
        return next((a for a in self._defs if a.id == aid), None)

    def metrics(self) -> list[str]:
        return sorted({a.metric for a in self._defs})

    async def refresh(self, repo) -> None:
        try:
            rows = await repo.all_achievement_defs()
        except Exception:
            rows = []
        if not rows:
            self._defs = list(ACHIEVEMENTS)
            return
        converted: list[Achievement] = []
        for row in rows:
            try:
                converted.append(Achievement(
                    id=row["id"], category=row["category"], metric=row["metric"],
                    threshold=int(row["threshold"]), title=row["title"],
                    secret=bool(row["secret"]), color=row.get("color")))
            except (KeyError, TypeError, ValueError):
                continue
        self._defs = converted

    @classmethod
    async def load(cls, repo) -> "AchievementDefs":
        inst = cls()
        await inst.refresh(repo)
        return inst
