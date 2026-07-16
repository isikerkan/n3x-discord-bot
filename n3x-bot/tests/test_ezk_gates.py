"""Epsilon / Zeta / Kappa (e/z/k) gates: the parts NOT covered by the
drop-icon reaction rework.

The input-seeding + reaction-confirmed store behaviour for e/z/k (and the fact
that Kappa no longer posts a ``KappaConfirmView`` button panel) now lives in
``tests/test_gate_drop_reactions.py``. The old ``KappaConfirmView`` /
``handle_delta_confirmation`` / ``bot._pending_delta`` tests were removed from
this module when that flow was replaced.

What remains here: ``!stat`` must accept e/z/k as valid gate types (they simply
carry no reward), which is independent of the confirmation mechanism.

Discord I/O is faked (AsyncMock/MagicMock); the repo is a real, connected
JsonRepository.
"""

import os
import tempfile
from unittest.mock import AsyncMock, MagicMock

from n3x_bot.config import Settings
from n3x_bot.seed import seed_defaults
from n3x_bot.storage.json_repo import JsonRepository


BASE_SETTINGS_KWARGS = dict(
    discord_token="tok",
    target_role_id=1,
    welcome_channel_id=2,
    reminder_channel_id=999,
    julez_id=424242,
    _env_file=None,
    _env_prefix="NONEXISTENT_",
)


def _settings(**overrides) -> Settings:
    kwargs = dict(BASE_SETTINGS_KWARGS)
    kwargs.update(overrides)
    return Settings(**kwargs)


async def _flatfile_repo() -> JsonRepository:
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    os.remove(path)
    repo = JsonRepository(path)
    await repo.connect()
    await seed_defaults(repo)
    return repo


# ── !stat accepts e/z/k (via GATE_TYPES, not gate_rewards) ───────────────────

async def test_stat_command_accepts_kappa_and_lists_costs():
    # e/z/k are valid gate types (they simply have no reward), so "!stat k"
    # must not be rejected as an invalid gate type.
    from n3x_bot.bot import _handle_gate_stat
    repo = await _flatfile_repo()
    settings = _settings()
    await repo.add_gate_entry("k", 500, 7, "Erkan",
                              drops={"hercules": True, "lf4u": False})

    ctx = MagicMock()
    ctx.send = AsyncMock()
    await _handle_gate_stat(ctx, repo, settings, "k")

    sent = ""
    for call in ctx.send.await_args_list:
        if call.args:
            sent += str(call.args[0])
        embed = call.kwargs.get("embed")
        if embed is not None:
            sent += str(getattr(embed, "title", "")) + str(getattr(embed, "description", ""))
    assert "Ungültiger Gate-Typ" not in sent
    assert "500" in sent

    await repo.close()
