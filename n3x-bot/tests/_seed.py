"""Shared seed helper for migration / export-import tests.

Populates EVERY table of a connected ``StatsRepository`` with a small but
representative fixture: archived + active rows, a targeted stat, a stat
linked to a message, multi-user ``user_stats``, ``target_stats`` for two
targets, ``stat_last_post`` and ``gate_entries`` carrying full metadata.

This module uses ONLY methods that already exist on the repository, so it
never contributes to the RED state — it is pure test infrastructure.
"""

from n3x_bot.storage.base import StatsRepository


async def seed_everything(repo: StatsRepository) -> None:
    # messages: one live (linked below), one archived
    greet = await repo.create_message("greet", "hi {user} x{count}")
    old = await repo.create_message("old", "legacy {count}")
    await repo.archive_message(old.id)

    # stats: linked, targeted, and an archived one
    await repo.create_stat("tit", "Tit", message_id=greet.id)
    await repo.create_stat("smart", "Smart", targeted=True)
    await repo.create_stat("dead", "Dead")
    await repo.archive_stat("dead")

    # users + user_stats + stat_totals via record_use (two distinct users)
    await repo.record_use(1001, "Alice", "tit")
    await repo.record_use(1001, "Alice", "tit")
    await repo.record_use(1002, "Bob", "tit")

    # an archived user carrying no tracking rows
    await repo.upsert_user(1003, "Ghost")
    await repo.archive_user(1003)

    # target_stats for two distinct targets on the targeted stat
    await repo.record_target_use(2001, "smart")
    await repo.record_target_use(2001, "smart")
    await repo.record_target_use(2002, "smart")

    # stat_last_post
    await repo.set_last_post("tit", 55501, 66601)

    # gate_entries with full metadata across two gate types
    await repo.add_gate_entry("a", 46000, 3001, "gamer1")
    await repo.add_gate_entry("a", 48000, 3002, "gamer2")
    await repo.add_gate_entry("b", 90000, 3003, "gamer3")
