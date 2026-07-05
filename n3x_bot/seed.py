import json
import os

from n3x_bot.storage.base import StatsRepository

LEGACY_STATS: list[tuple[str, str, str]] = [
    ("tit", "Tit", "Erkans boobies wurden schon {count} mal geshaket!"),
    ("wahab", "Wahab", "Wahab hat bereits {count} mal jemanden auf diesem Discord beleidigt :*"),
    ("cry", "Cry", "Es wurde bereits {count} mal geheult."),
    ("afk", "AFK", "Muneeb ist zum {count} mal AFK..."),
    ("oma", "Oma", "Patrick wurde zum {count} mal Perma gebannt."),
    ("jules", "Jules", "Der aller echteste Homelander hat euch schon {count} mal am leben gelassen!"),
    ("smart", "Smart", "Julez beweist zum {count} Mal, dass er ein Klugscheisser ist.."),
    ("crash", "Crash", "Dennis geht zum {count} mal komplett crashout... opfer"),
]


async def seed_defaults(repo: StatsRepository) -> None:
    for key, name, template in LEGACY_STATS:
        if await repo.get_stat(key) is not None:
            continue
        msg = await repo.create_message(f"{key}_msg", template)
        await repo.create_stat(key, name, message_id=msg.id)


async def migrate_legacy_json(repo: StatsRepository, path: str) -> None:
    if not os.path.exists(path):
        return
    with open(path, "r") as f:
        data = json.load(f)

    # per-user counts first (these contribute to global totals)
    user_contributed = {}
    for uid_str, cmds in data.get("user_stats", {}).items():
        discord_id = int(uid_str)
        for key, count in cmds.items():
            if await repo.get_stat(key) is None:
                continue
            for _ in range(count):
                await repo.record_use(discord_id, f"user_{discord_id}", key)
            user_contributed[key] = user_contributed.get(key, 0) + count

    # global totals: keys like "tit_count"
    # attribute the unattributed difference to user 0
    for key, _, _ in LEGACY_STATS:
        total = data.get(f"{key}_count", 0)
        user_count = user_contributed.get(key, 0)
        unattributed = total - user_count
        if unattributed > 0:
            for _ in range(unattributed):
                # seed total without attributing to a user
                await _bump_total(repo, key)


async def _bump_total(repo: StatsRepository, key: str) -> None:
    # Increment only the global total, using a synthetic archived migrator user
    await repo.record_use(0, "legacy_migrator", key)
