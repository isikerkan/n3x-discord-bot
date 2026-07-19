import json
import os

from n3x_bot.storage.base import StatsRepository

LEGACY_STATS: list[tuple[str, str, str]] = [
    ("tit", "Tit", "{user} hat Erkans Boobies schon {count} mal geshaked! 🤲"),
    ("wahab", "Wahab", "{user} hat Wahab schon {count} mal an den Pranger gestellt :*"),
    ("cry", "Cry", "{user} hat schon {count} mal geheult. 😭"),
    ("afk", "AFK", "{user} ist schon {count} mal AFK gegangen... 💤"),
    ("smart", "Smart", "{user} nennt {target} zum {count}. Mal einen Klugscheisser.. 🤓"),
    ("crash", "Crash", "{user} schickt {target} zum {count}. Mal in den Crashout... 💥"),
    ("home", "Home",
     "{user} lässt den Homelander {target} zum {count}. Mal am Leben! 🦸"),
]

# Stats counted against a TARGET member (e.g. `!smart @user`) rather than
# the invoker. `home` targets a fixed configured id (settings.julez_id).
TARGETED_STATS: set[str] = {"smart", "crash", "home"}


async def seed_defaults(repo: StatsRepository) -> None:
    for key, name, template in LEGACY_STATS:
        if await repo.get_stat(key) is not None:
            continue
        msg = await repo.create_message(f"{key}_msg", template)
        await repo.create_stat(key, name, message_id=msg.id, targeted=(key in TARGETED_STATS))


async def migrate_legacy_json(repo: StatsRepository, path: str) -> None:
    if not os.path.exists(path):
        return
    with open(path, "r") as f:
        data = json.load(f)

    user_stats = data.get("user_stats", {})
    for key, _, _ in LEGACY_STATS:
        # Idempotency guard: if this key already has counts, it was migrated.
        if await repo.get_total(key) != 0:
            continue
        if await repo.get_stat(key) is None:
            continue
        total = data.get(f"{key}_count", 0)
        attributed = 0
        for uid_str, cmds in user_stats.items():
            count = cmds.get(key, 0)
            for _ in range(count):
                await repo.record_use(int(uid_str), f"user_{uid_str}", key)
            attributed += count
        remainder = total - attributed
        for _ in range(max(0, remainder)):
            # Attribute the unattributed remainder to synthetic user 0
            # (the migrator), not to any real Discord user.
            await repo.record_use(0, "legacy_migrator", key)
