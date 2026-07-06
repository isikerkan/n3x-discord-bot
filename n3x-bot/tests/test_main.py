import json
import os
import tempfile

from n3x_bot.__main__ import _is_legacy_flatfile, _prepare
from n3x_bot.config import Settings

BASE_SETTINGS_KWARGS = dict(
    discord_token="tok",
    target_role_id=1,
    welcome_channel_id=2,
    reminder_channel_id=3,
    _env_file=None,
    _env_prefix="NONEXISTENT_",
)


def _settings(**overrides) -> Settings:
    kwargs = dict(BASE_SETTINGS_KWARGS)
    kwargs.update(overrides)
    return Settings(**kwargs)


LEGACY_PAYLOAD = {
    "tit_count": 6,
    "wahab_count": 163,
    "cry_count": 3,
    "afk_count": 3,
    "oma_count": 2,
    "jules_count": 2,
    "smart_count": 4,
    "last_messages": {
        "wahab": 1523090183917010965,
        "smart": 1523088462511734941,
    },
    "crash_count": 1,
    "user_stats": {"278494040461279232": {"crash": 1}},
}


def _write_legacy_file() -> str:
    fd, path = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w") as f:
        json.dump(LEGACY_PAYLOAD, f)
    return path


async def test_prepare_migrates_legacy_flatfile_counts():
    path = _write_legacy_file()
    settings = _settings(storage_backend="flatfile", data_file=path)
    try:
        repo = await _prepare(settings)
        assert await repo.get_total("wahab") == 163
        assert await repo.get_total("crash") == 1
        assert await repo.get_user_stats(278494040461279232) == {"crash": 1}
        await repo.close()
    finally:
        if os.path.exists(path):
            os.remove(path)
        if os.path.exists(path + ".legacy"):
            os.remove(path + ".legacy")


async def test_prepare_flatfile_migration_is_idempotent_across_restarts():
    path = _write_legacy_file()
    settings = _settings(storage_backend="flatfile", data_file=path)
    try:
        repo = await _prepare(settings)
        assert await repo.get_total("wahab") == 163
        await repo.close()

        repo2 = await _prepare(settings)
        assert await repo2.get_total("wahab") == 163
        assert await repo2.get_user_stats(278494040461279232) == {"crash": 1}
        await repo2.close()
    finally:
        if os.path.exists(path):
            os.remove(path)
        if os.path.exists(path + ".legacy"):
            os.remove(path + ".legacy")


def test_is_legacy_flatfile_false_for_new_format_file():
    fd, path = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w") as f:
        json.dump({
            "seq": {"user": 0, "message": 0, "stat": 0},
            "users": [], "messages": [], "stats": [],
            "user_stats": {}, "stat_totals": {}, "stat_last_post": {},
        }, f)
    try:
        assert _is_legacy_flatfile(path) is False
    finally:
        os.remove(path)


def test_is_legacy_flatfile_false_for_missing_file():
    assert _is_legacy_flatfile("/nonexistent/path/stats.json") is False


def test_is_legacy_flatfile_true_for_legacy_file():
    path = _write_legacy_file()
    try:
        assert _is_legacy_flatfile(path) is True
    finally:
        os.remove(path)
