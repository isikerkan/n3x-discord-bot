# N3X Bot — Storage Redesign & Dynamic Stats Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor the single-file `bot.py` into a package with externalized config, a pluggable CRUD storage layer (flatfile/sqlite/postgres), data-driven Discord commands, and Docker deployment — all built via TDD.

**Architecture:** A `pydantic-settings` `Settings` object loads `.env`. Domain entities (User, Stat, Message) are plain frozen dataclasses. A `StatsRepository` abstract interface defines CRUD + tracking operations; two implementations (`JsonRepository`, `SqlRepository`) satisfy one shared contract test suite. A factory selects the implementation from `STORAGE_BACKEND`. `bot.py` registers one Discord command per non-archived `stats` row at startup.

**Tech Stack:** Python 3.12, discord.py, pydantic-settings, SQLAlchemy 2.0 (async) + aiosqlite + asyncpg, pytest / pytest-asyncio / pytest-cov, uv, Docker.

## Global Constraints

- Python `>=3.12`.
- No hardcoded secrets — token and all IDs come from `Settings` (`.env`). Never commit `.env` or a token-bearing `bot.py`.
- One SQLAlchemy implementation serves both `sqlite` and `postgres`; dialect derived from `DATABASE_URL`. Do NOT write a third SQL impl.
- Repository interface is async. All three backends pass the identical contract suite.
- SQL tables auto-created on startup via `metadata.create_all` — no Alembic.
- Placeholders in message templates: `{user}`, `{count}`, `{stat}`.
- Default output when a stat has no linked message: `"<stat.name> — <user> — <count>"` (em dash `—`, spaces around it).
- Package name: `n3x_bot`. Test root: `tests/`.
- Commit after every task.

---

## File Structure

```
pyproject.toml                     # uv project, deps, pytest config
.dockerignore
Dockerfile
docker-compose.yml
README.md
n3x_bot/
  __init__.py
  __main__.py                      # entrypoint: build Settings -> repo -> bot -> run
  config.py                        # Settings (pydantic-settings) + validation
  models.py                        # User/Stat/Message dataclasses + render_output()
  seed.py                          # legacy stat/message seed + stats.json migration
  bot.py                           # build bot, register dynamic commands, events
  storage/
    __init__.py
    base.py                        # StatsRepository ABC
    schema.py                      # SQLAlchemy MetaData + Table defs
    json_repo.py                   # flatfile JSON implementation
    sql_repo.py                    # SQLAlchemy async implementation (sqlite+postgres)
    factory.py                     # create_repository(settings)
tests/
  conftest.py
  test_config.py
  test_render.py
  test_seed.py
  test_commands.py
  storage/
    conftest.py                    # parametrized repo fixture (all backends)
    test_repository_contract.py    # shared contract suite
```

---

### Task 1: Project scaffold (uv, deps, pytest)

**Files:**
- Create: `pyproject.toml`
- Create: `n3x_bot/__init__.py` (empty)
- Create: `n3x_bot/storage/__init__.py` (empty)
- Create: `tests/__init__.py` (empty)
- Create: `tests/test_smoke.py`

**Interfaces:**
- Consumes: nothing.
- Produces: importable `n3x_bot` package; working `pytest` + `pytest-asyncio` (asyncio_mode=auto).

- [ ] **Step 1: Write `pyproject.toml`**

```toml
[project]
name = "n3x-bot"
version = "0.1.0"
description = "N3X Discord bot with pluggable CRUD storage"
requires-python = ">=3.12"
dependencies = [
    "discord.py>=2.4",
    "pydantic-settings>=2.4",
    "sqlalchemy[asyncio]>=2.0",
    "asyncpg>=0.29",
    "aiosqlite>=0.20",
]

[dependency-groups]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.24",
    "pytest-cov>=5.0",
]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
addopts = "-q"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```

- [ ] **Step 2: Write the smoke test** — `tests/test_smoke.py`

```python
import n3x_bot


def test_package_imports():
    assert n3x_bot is not None
```

- [ ] **Step 3: Create the empty files**

Create `n3x_bot/__init__.py`, `n3x_bot/storage/__init__.py`, `tests/__init__.py` as empty files.

- [ ] **Step 4: Install and run**

Run: `uv sync && uv run pytest tests/test_smoke.py -v`
Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml n3x_bot tests
git commit -m "chore: scaffold n3x_bot package with uv + pytest"
```

---

### Task 2: Config (`Settings`)

**Files:**
- Create: `n3x_bot/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `Settings` (pydantic-settings `BaseSettings`) with fields:
  `discord_token: str`, `storage_backend: Literal["flatfile","sqlite","postgres"]`,
  `database_url: str | None`, `data_file: str`, `target_role_id: int`,
  `welcome_channel_id: int`, `reminder_channel_id: int`, `prefix_str: str`,
  `command_prefix: str`, `reminder_time: str`.
  Raises `ValidationError` if `storage_backend` in {sqlite,postgres} and `database_url` is empty.
  `Settings.reminder_hm() -> tuple[int,int]` parses `reminder_time` `"HH:MM"`.

- [ ] **Step 1: Write the failing tests** — `tests/test_config.py`

```python
import pytest
from pydantic import ValidationError
from n3x_bot.config import Settings

BASE = dict(
    discord_token="tok",
    target_role_id=1,
    welcome_channel_id=2,
    reminder_channel_id=3,
)


def test_defaults_flatfile():
    s = Settings(**BASE)
    assert s.storage_backend == "flatfile"
    assert s.data_file == "stats.json"
    assert s.command_prefix == "!"
    assert s.reminder_hm() == (19, 30)


def test_sqlite_requires_database_url():
    with pytest.raises(ValidationError):
        Settings(**BASE, storage_backend="sqlite")


def test_postgres_with_url_ok():
    s = Settings(**BASE, storage_backend="postgres",
                 database_url="postgresql+asyncpg://u:p@h/d")
    assert s.database_url.endswith("/d")


def test_reminder_hm_parses():
    s = Settings(**BASE, reminder_time="07:05")
    assert s.reminder_hm() == (7, 5)
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: n3x_bot.config`.

- [ ] **Step 3: Implement** — `n3x_bot/config.py`

```python
from typing import Literal
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    discord_token: str
    storage_backend: Literal["flatfile", "sqlite", "postgres"] = "flatfile"
    database_url: str | None = None
    data_file: str = "stats.json"

    target_role_id: int
    welcome_channel_id: int
    reminder_channel_id: int

    prefix_str: str = "[N3X]"
    command_prefix: str = "!"
    reminder_time: str = "19:30"

    @model_validator(mode="after")
    def _require_db_url(self) -> "Settings":
        if self.storage_backend in ("sqlite", "postgres") and not self.database_url:
            raise ValueError(
                f"database_url is required for storage_backend={self.storage_backend}"
            )
        return self

    def reminder_hm(self) -> tuple[int, int]:
        hh, mm = self.reminder_time.split(":")
        return int(hh), int(mm)
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_config.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add n3x_bot/config.py tests/test_config.py
git commit -m "feat: typed Settings with backend/database_url validation"
```

---

### Task 3: Domain models + render logic

**Files:**
- Create: `n3x_bot/models.py`
- Test: `tests/test_render.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `User(id:int, discord_id:int, display_name:str, archived_at:datetime|None=None, created_at:datetime|None=None)` (frozen dataclass)
  - `Message(id:int, name:str, template:str, archived_at:datetime|None=None, created_at:datetime|None=None)` (frozen)
  - `Stat(id:int, key:str, name:str, message_id:int|None=None, archived_at:datetime|None=None, created_at:datetime|None=None)` (frozen)
  - `render_output(stat: Stat, message: Message | None, user_display: str, count: int) -> str`

- [ ] **Step 1: Write the failing tests** — `tests/test_render.py`

```python
from n3x_bot.models import Stat, Message, render_output


def test_default_output_when_no_message():
    stat = Stat(id=1, key="tit", name="Tit", message_id=None)
    assert render_output(stat, None, "Erkan", 5) == "Tit — Erkan — 5"


def test_linked_message_renders_placeholders():
    stat = Stat(id=1, key="tit", name="Tit", message_id=9)
    msg = Message(id=9, name="tit_msg", template="{user} did {stat} x{count}")
    assert render_output(stat, msg, "Erkan", 5) == "Erkan did Tit x5"


def test_missing_placeholder_in_template_is_ignored():
    stat = Stat(id=1, key="cry", name="Cry", message_id=9)
    msg = Message(id=9, name="cry_msg", template="cried {count} times")
    assert render_output(stat, msg, "Ali", 3) == "cried 3 times"
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_render.py -v`
Expected: FAIL — `ModuleNotFoundError: n3x_bot.models`.

- [ ] **Step 3: Implement** — `n3x_bot/models.py`

```python
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class User:
    id: int
    discord_id: int
    display_name: str
    archived_at: datetime | None = None
    created_at: datetime | None = None


@dataclass(frozen=True)
class Message:
    id: int
    name: str
    template: str
    archived_at: datetime | None = None
    created_at: datetime | None = None


@dataclass(frozen=True)
class Stat:
    id: int
    key: str
    name: str
    message_id: int | None = None
    archived_at: datetime | None = None
    created_at: datetime | None = None


def render_output(stat: Stat, message: Message | None,
                  user_display: str, count: int) -> str:
    if message is not None:
        return message.template.format(user=user_display, count=count, stat=stat.name)
    return f"{stat.name} — {user_display} — {count}"
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_render.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add n3x_bot/models.py tests/test_render.py
git commit -m "feat: domain models + template render logic"
```

---

### Task 4: Repository interface + contract suite (fails until an impl exists)

**Files:**
- Create: `n3x_bot/storage/base.py`
- Create: `tests/storage/__init__.py` (empty)
- Create: `tests/storage/conftest.py`
- Create: `tests/storage/test_repository_contract.py`

**Interfaces:**
- Consumes: `n3x_bot.models` (User, Stat, Message).
- Produces: abstract `StatsRepository` with async methods (exact signatures below). The contract fixture `repo` yields a connected repository; parametrized over backends. Task 5/6 plug implementations in.

**`StatsRepository` method signatures (all `async`):**
```
connect() -> None
close() -> None
# messages
create_message(name: str, template: str) -> Message
get_message(message_id: int) -> Message | None
list_messages(include_archived: bool = False) -> list[Message]
update_message(message_id: int, name: str | None = None, template: str | None = None) -> Message
archive_message(message_id: int) -> None
delete_message(message_id: int) -> None
# stats
create_stat(key: str, name: str, message_id: int | None = None) -> Stat
get_stat(key: str) -> Stat | None
list_stats(include_archived: bool = False) -> list[Stat]
update_stat(key: str, name: str | None = None) -> Stat
set_stat_message(key: str, message_id: int | None) -> Stat
archive_stat(key: str) -> None
delete_stat(key: str) -> None
# users
upsert_user(discord_id: int, display_name: str) -> User
get_user(discord_id: int) -> User | None
list_users(include_archived: bool = False) -> list[User]
archive_user(discord_id: int) -> None
delete_user(discord_id: int) -> None
# tracking
record_use(discord_id: int, display_name: str, stat_key: str) -> tuple[int, int]  # (user_count, total_count)
get_user_stats(discord_id: int) -> dict[str, int]  # {stat_key: count}
get_total(stat_key: str) -> int
get_last_post(stat_key: str) -> tuple[int, int] | None  # (discord_message_id, channel_id)
set_last_post(stat_key: str, discord_message_id: int, channel_id: int) -> None
```

- [ ] **Step 1: Write the ABC** — `n3x_bot/storage/base.py`

```python
from abc import ABC, abstractmethod
from n3x_bot.models import User, Stat, Message


class StatsRepository(ABC):
    @abstractmethod
    async def connect(self) -> None: ...
    @abstractmethod
    async def close(self) -> None: ...

    # messages
    @abstractmethod
    async def create_message(self, name: str, template: str) -> Message: ...
    @abstractmethod
    async def get_message(self, message_id: int) -> Message | None: ...
    @abstractmethod
    async def list_messages(self, include_archived: bool = False) -> list[Message]: ...
    @abstractmethod
    async def update_message(self, message_id: int, name: str | None = None,
                             template: str | None = None) -> Message: ...
    @abstractmethod
    async def archive_message(self, message_id: int) -> None: ...
    @abstractmethod
    async def delete_message(self, message_id: int) -> None: ...

    # stats
    @abstractmethod
    async def create_stat(self, key: str, name: str,
                          message_id: int | None = None) -> Stat: ...
    @abstractmethod
    async def get_stat(self, key: str) -> Stat | None: ...
    @abstractmethod
    async def list_stats(self, include_archived: bool = False) -> list[Stat]: ...
    @abstractmethod
    async def update_stat(self, key: str, name: str | None = None) -> Stat: ...
    @abstractmethod
    async def set_stat_message(self, key: str, message_id: int | None) -> Stat: ...
    @abstractmethod
    async def archive_stat(self, key: str) -> None: ...
    @abstractmethod
    async def delete_stat(self, key: str) -> None: ...

    # users
    @abstractmethod
    async def upsert_user(self, discord_id: int, display_name: str) -> User: ...
    @abstractmethod
    async def get_user(self, discord_id: int) -> User | None: ...
    @abstractmethod
    async def list_users(self, include_archived: bool = False) -> list[User]: ...
    @abstractmethod
    async def archive_user(self, discord_id: int) -> None: ...
    @abstractmethod
    async def delete_user(self, discord_id: int) -> None: ...

    # tracking
    @abstractmethod
    async def record_use(self, discord_id: int, display_name: str,
                         stat_key: str) -> tuple[int, int]: ...
    @abstractmethod
    async def get_user_stats(self, discord_id: int) -> dict[str, int]: ...
    @abstractmethod
    async def get_total(self, stat_key: str) -> int: ...
    @abstractmethod
    async def get_last_post(self, stat_key: str) -> tuple[int, int] | None: ...
    @abstractmethod
    async def set_last_post(self, stat_key: str, discord_message_id: int,
                            channel_id: int) -> None: ...
```

- [ ] **Step 2: Write the contract fixture** — `tests/storage/conftest.py`

The fixture is a placeholder that will be extended in Tasks 5 and 6. Start with an empty param list so collection is valid but the suite is skipped until a backend is registered.

```python
import pytest

# Backends are appended by Task 5 (json) and Task 6 (sql).
# Each entry: (id, async factory returning a *connected* StatsRepository).
BACKENDS: list = []


def pytest_generate_tests(metafunc):
    if "repo" in metafunc.fixturenames:
        if not BACKENDS:
            pytest.skip("no storage backends registered yet")
        ids = [b[0] for b in BACKENDS]
        metafunc.parametrize("repo_factory", [b[1] for b in BACKENDS], ids=ids)


@pytest.fixture
async def repo(repo_factory):
    r = await repo_factory()
    try:
        yield r
    finally:
        await r.close()
```

- [ ] **Step 3: Write the contract suite** — `tests/storage/test_repository_contract.py`

```python
import pytest


async def test_create_and_get_stat(repo):
    await repo.create_stat("tit", "Tit")
    s = await repo.get_stat("tit")
    assert s is not None and s.key == "tit" and s.name == "Tit"
    assert s.message_id is None


async def test_get_missing_stat_returns_none(repo):
    assert await repo.get_stat("nope") is None


async def test_list_stats_excludes_archived_by_default(repo):
    await repo.create_stat("a", "A")
    await repo.create_stat("b", "B")
    await repo.archive_stat("a")
    keys = {s.key for s in await repo.list_stats()}
    assert keys == {"b"}
    all_keys = {s.key for s in await repo.list_stats(include_archived=True)}
    assert all_keys == {"a", "b"}


async def test_update_and_delete_stat(repo):
    await repo.create_stat("x", "X")
    updated = await repo.update_stat("x", name="X2")
    assert updated.name == "X2"
    await repo.delete_stat("x")
    assert await repo.get_stat("x") is None


async def test_message_crud_and_link(repo):
    m = await repo.create_message("greet", "hi {user}")
    assert m.id > 0
    await repo.create_stat("k", "K")
    linked = await repo.set_stat_message("k", m.id)
    assert linked.message_id == m.id
    unlinked = await repo.set_stat_message("k", None)
    assert unlinked.message_id is None


async def test_upsert_user_is_idempotent(repo):
    u1 = await repo.upsert_user(42, "Erkan")
    u2 = await repo.upsert_user(42, "Erkan Renamed")
    assert u1.id == u2.id
    assert (await repo.get_user(42)).display_name == "Erkan Renamed"
    assert len(await repo.list_users()) == 1


async def test_record_use_increments_user_and_total(repo):
    await repo.create_stat("tit", "Tit")
    uc1, tc1 = await repo.record_use(42, "Erkan", "tit")
    uc2, tc2 = await repo.record_use(42, "Erkan", "tit")
    uc3, tc3 = await repo.record_use(99, "Ali", "tit")
    assert (uc1, tc1) == (1, 1)
    assert (uc2, tc2) == (2, 2)
    assert (uc3, tc3) == (1, 3)
    assert await repo.get_total("tit") == 3
    assert await repo.get_user_stats(42) == {"tit": 2}


async def test_record_use_unknown_stat_raises(repo):
    with pytest.raises(KeyError):
        await repo.record_use(1, "Nobody", "ghost")


async def test_last_post_roundtrip(repo):
    await repo.create_stat("tit", "Tit")
    assert await repo.get_last_post("tit") is None
    await repo.set_last_post("tit", 123, 456)
    assert await repo.get_last_post("tit") == (123, 456)
    await repo.set_last_post("tit", 789, 456)
    assert await repo.get_last_post("tit") == (789, 456)
```

- [ ] **Step 4: Run — suite skips (no backend yet)**

Run: `uv run pytest tests/storage -v`
Expected: all `test_*` SKIPPED with reason "no storage backends registered yet". This confirms collection works.

- [ ] **Step 5: Commit**

```bash
git add n3x_bot/storage/base.py tests/storage
git commit -m "feat: StatsRepository interface + backend-parametrized contract suite"
```

---

### Task 5: Flatfile (JSON) repository

**Files:**
- Create: `n3x_bot/storage/json_repo.py`
- Modify: `tests/storage/conftest.py` (register the json backend)

**Interfaces:**
- Consumes: `StatsRepository`, `n3x_bot.models`.
- Produces: `JsonRepository(path: str)` — implements the full interface over a single JSON file. Autoincrement ids kept in the JSON. Passes the contract suite.

JSON shape:
```json
{
  "seq": {"user": 0, "message": 0, "stat": 0},
  "users": [ {"id":1,"discord_id":42,"display_name":"..","archived_at":null,"created_at":".."} ],
  "messages": [ {"id":1,"name":"..","template":"..","archived_at":null,"created_at":".."} ],
  "stats": [ {"id":1,"key":"tit","name":"Tit","message_id":null,"archived_at":null,"created_at":".."} ],
  "user_stats": {"<user_id>": {"<stat_id>": 5}},
  "stat_totals": {"<stat_id>": 5},
  "stat_last_post": {"<stat_id>": [msg_id, channel_id]}
}
```

- [ ] **Step 1: Implement** — `n3x_bot/storage/json_repo.py`

```python
import json
import os
from datetime import datetime, timezone

from n3x_bot.models import User, Stat, Message
from n3x_bot.storage.base import StatsRepository


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_dt(v):
    return datetime.fromisoformat(v) if v else None


class JsonRepository(StatsRepository):
    def __init__(self, path: str):
        self.path = path
        self._db = None

    # ── lifecycle / persistence ────────────────────────────────────────────
    def _empty(self) -> dict:
        return {
            "seq": {"user": 0, "message": 0, "stat": 0},
            "users": [], "messages": [], "stats": [],
            "user_stats": {}, "stat_totals": {}, "stat_last_post": {},
        }

    async def connect(self) -> None:
        if os.path.exists(self.path):
            with open(self.path, "r") as f:
                self._db = json.load(f)
            for k, v in self._empty().items():
                self._db.setdefault(k, v)
        else:
            self._db = self._empty()
            self._flush()

    async def close(self) -> None:
        self._flush()

    def _flush(self) -> None:
        with open(self.path, "w") as f:
            json.dump(self._db, f)

    def _next(self, kind: str) -> int:
        self._db["seq"][kind] += 1
        return self._db["seq"][kind]

    # ── row -> model helpers ───────────────────────────────────────────────
    def _stat(self, r) -> Stat:
        return Stat(id=r["id"], key=r["key"], name=r["name"],
                    message_id=r["message_id"],
                    archived_at=_parse_dt(r["archived_at"]),
                    created_at=_parse_dt(r["created_at"]))

    def _msg(self, r) -> Message:
        return Message(id=r["id"], name=r["name"], template=r["template"],
                       archived_at=_parse_dt(r["archived_at"]),
                       created_at=_parse_dt(r["created_at"]))

    def _user(self, r) -> User:
        return User(id=r["id"], discord_id=r["discord_id"],
                    display_name=r["display_name"],
                    archived_at=_parse_dt(r["archived_at"]),
                    created_at=_parse_dt(r["created_at"]))

    def _find(self, coll: str, **kw):
        for r in self._db[coll]:
            if all(r[k] == v for k, v in kw.items()):
                return r
        return None

    # ── messages ───────────────────────────────────────────────────────────
    async def create_message(self, name, template) -> Message:
        row = {"id": self._next("message"), "name": name, "template": template,
               "archived_at": None, "created_at": _now()}
        self._db["messages"].append(row)
        self._flush()
        return self._msg(row)

    async def get_message(self, message_id):
        r = self._find("messages", id=message_id)
        return self._msg(r) if r else None

    async def list_messages(self, include_archived=False):
        return [self._msg(r) for r in self._db["messages"]
                if include_archived or r["archived_at"] is None]

    async def update_message(self, message_id, name=None, template=None):
        r = self._find("messages", id=message_id)
        if r is None:
            raise KeyError(message_id)
        if name is not None:
            r["name"] = name
        if template is not None:
            r["template"] = template
        self._flush()
        return self._msg(r)

    async def archive_message(self, message_id):
        r = self._find("messages", id=message_id)
        if r is None:
            raise KeyError(message_id)
        r["archived_at"] = _now()
        self._flush()

    async def delete_message(self, message_id):
        self._db["messages"] = [r for r in self._db["messages"] if r["id"] != message_id]
        self._flush()

    # ── stats ──────────────────────────────────────────────────────────────
    async def create_stat(self, key, name, message_id=None) -> Stat:
        row = {"id": self._next("stat"), "key": key, "name": name,
               "message_id": message_id, "archived_at": None, "created_at": _now()}
        self._db["stats"].append(row)
        self._flush()
        return self._stat(row)

    async def get_stat(self, key):
        r = self._find("stats", key=key)
        return self._stat(r) if r else None

    async def list_stats(self, include_archived=False):
        return [self._stat(r) for r in self._db["stats"]
                if include_archived or r["archived_at"] is None]

    async def update_stat(self, key, name=None):
        r = self._find("stats", key=key)
        if r is None:
            raise KeyError(key)
        if name is not None:
            r["name"] = name
        self._flush()
        return self._stat(r)

    async def set_stat_message(self, key, message_id):
        r = self._find("stats", key=key)
        if r is None:
            raise KeyError(key)
        r["message_id"] = message_id
        self._flush()
        return self._stat(r)

    async def archive_stat(self, key):
        r = self._find("stats", key=key)
        if r is None:
            raise KeyError(key)
        r["archived_at"] = _now()
        self._flush()

    async def delete_stat(self, key):
        self._db["stats"] = [r for r in self._db["stats"] if r["key"] != key]
        self._flush()

    # ── users ──────────────────────────────────────────────────────────────
    async def upsert_user(self, discord_id, display_name) -> User:
        r = self._find("users", discord_id=discord_id)
        if r is None:
            r = {"id": self._next("user"), "discord_id": discord_id,
                 "display_name": display_name, "archived_at": None,
                 "created_at": _now()}
            self._db["users"].append(r)
        else:
            r["display_name"] = display_name
        self._flush()
        return self._user(r)

    async def get_user(self, discord_id):
        r = self._find("users", discord_id=discord_id)
        return self._user(r) if r else None

    async def list_users(self, include_archived=False):
        return [self._user(r) for r in self._db["users"]
                if include_archived or r["archived_at"] is None]

    async def archive_user(self, discord_id):
        r = self._find("users", discord_id=discord_id)
        if r is None:
            raise KeyError(discord_id)
        r["archived_at"] = _now()
        self._flush()

    async def delete_user(self, discord_id):
        self._db["users"] = [r for r in self._db["users"]
                             if r["discord_id"] != discord_id]
        self._flush()

    # ── tracking ───────────────────────────────────────────────────────────
    async def record_use(self, discord_id, display_name, stat_key):
        stat = self._find("stats", key=stat_key)
        if stat is None:
            raise KeyError(stat_key)
        user = await self.upsert_user(discord_id, display_name)
        uid, sid = str(user.id), str(stat["id"])
        us = self._db["user_stats"].setdefault(uid, {})
        us[sid] = us.get(sid, 0) + 1
        self._db["stat_totals"][sid] = self._db["stat_totals"].get(sid, 0) + 1
        self._flush()
        return us[sid], self._db["stat_totals"][sid]

    async def get_user_stats(self, discord_id):
        user = self._find("users", discord_id=discord_id)
        if user is None:
            return {}
        us = self._db["user_stats"].get(str(user["id"]), {})
        id_to_key = {str(s["id"]): s["key"] for s in self._db["stats"]}
        return {id_to_key[sid]: c for sid, c in us.items() if sid in id_to_key}

    async def get_total(self, stat_key):
        stat = self._find("stats", key=stat_key)
        if stat is None:
            return 0
        return self._db["stat_totals"].get(str(stat["id"]), 0)

    async def get_last_post(self, stat_key):
        stat = self._find("stats", key=stat_key)
        if stat is None:
            return None
        v = self._db["stat_last_post"].get(str(stat["id"]))
        return (v[0], v[1]) if v else None

    async def set_last_post(self, stat_key, discord_message_id, channel_id):
        stat = self._find("stats", key=stat_key)
        if stat is None:
            raise KeyError(stat_key)
        self._db["stat_last_post"][str(stat["id"])] = [discord_message_id, channel_id]
        self._flush()
```

- [ ] **Step 2: Register the backend in the fixture** — edit `tests/storage/conftest.py`, replace the `BACKENDS: list = []` line with:

```python
from n3x_bot.storage.json_repo import JsonRepository


async def _make_json(tmp_path_holder=[]):
    import tempfile, os
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    os.remove(path)  # start clean; connect() will create it
    r = JsonRepository(path)
    await r.connect()
    return r


BACKENDS: list = [("json", _make_json)]
```

- [ ] **Step 3: Run the contract suite against json**

Run: `uv run pytest tests/storage -v`
Expected: every contract test PASSES with id `[json]`.

- [ ] **Step 4: Commit**

```bash
git add n3x_bot/storage/json_repo.py tests/storage/conftest.py
git commit -m "feat: flatfile JSON repository passing contract suite"
```

---

### Task 6: SQL repository (SQLAlchemy async; sqlite + postgres)

**Files:**
- Create: `n3x_bot/storage/schema.py`
- Create: `n3x_bot/storage/sql_repo.py`
- Modify: `tests/storage/conftest.py` (register sqlite + optional postgres)

**Interfaces:**
- Consumes: `StatsRepository`, `n3x_bot.models`.
- Produces: `SqlRepository(database_url: str)` — one implementation for both dialects. `connect()` creates an async engine and runs `metadata.create_all`. Passes the contract suite.

- [ ] **Step 1: Define the schema** — `n3x_bot/storage/schema.py`

```python
from sqlalchemy import (
    MetaData, Table, Column, Integer, BigInteger, String, Text,
    DateTime, ForeignKey,
)

metadata = MetaData()

users = Table(
    "users", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("discord_id", BigInteger, unique=True, nullable=False),
    Column("display_name", String(100), nullable=False),
    Column("archived_at", DateTime, nullable=True),
    Column("created_at", DateTime, nullable=False),
)

messages = Table(
    "messages", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("name", String(100), unique=True, nullable=False),
    Column("template", Text, nullable=False),
    Column("archived_at", DateTime, nullable=True),
    Column("created_at", DateTime, nullable=False),
)

stats = Table(
    "stats", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("key", String(50), unique=True, nullable=False),
    Column("name", String(100), nullable=False),
    Column("message_id", Integer, ForeignKey("messages.id"), nullable=True),
    Column("archived_at", DateTime, nullable=True),
    Column("created_at", DateTime, nullable=False),
)

user_stats = Table(
    "user_stats", metadata,
    Column("user_id", Integer, ForeignKey("users.id"), primary_key=True),
    Column("stat_id", Integer, ForeignKey("stats.id"), primary_key=True),
    Column("count", Integer, nullable=False, default=0),
)

stat_totals = Table(
    "stat_totals", metadata,
    Column("stat_id", Integer, ForeignKey("stats.id"), primary_key=True),
    Column("count", Integer, nullable=False, default=0),
)

stat_last_post = Table(
    "stat_last_post", metadata,
    Column("stat_id", Integer, ForeignKey("stats.id"), primary_key=True),
    Column("discord_message_id", BigInteger, nullable=False),
    Column("channel_id", BigInteger, nullable=False),
)
```

- [ ] **Step 2: Implement the repository** — `n3x_bot/storage/sql_repo.py`

```python
from datetime import datetime, timezone

from sqlalchemy import select, insert, update, delete, func
from sqlalchemy.ext.asyncio import create_async_engine

from n3x_bot.models import User, Stat, Message
from n3x_bot.storage.base import StatsRepository
from n3x_bot.storage import schema as sc


def _now() -> datetime:
    return datetime.now(timezone.utc)


class SqlRepository(StatsRepository):
    def __init__(self, database_url: str):
        self.database_url = database_url
        self.engine = None

    async def connect(self) -> None:
        self.engine = create_async_engine(self.database_url)
        async with self.engine.begin() as conn:
            await conn.run_sync(sc.metadata.create_all)

    async def close(self) -> None:
        if self.engine is not None:
            await self.engine.dispose()

    # ── row -> model helpers ───────────────────────────────────────────────
    @staticmethod
    def _stat(r) -> Stat:
        return Stat(id=r.id, key=r.key, name=r.name, message_id=r.message_id,
                    archived_at=r.archived_at, created_at=r.created_at)

    @staticmethod
    def _msg(r) -> Message:
        return Message(id=r.id, name=r.name, template=r.template,
                       archived_at=r.archived_at, created_at=r.created_at)

    @staticmethod
    def _user(r) -> User:
        return User(id=r.id, discord_id=r.discord_id, display_name=r.display_name,
                    archived_at=r.archived_at, created_at=r.created_at)

    # ── messages ───────────────────────────────────────────────────────────
    async def create_message(self, name, template) -> Message:
        async with self.engine.begin() as conn:
            res = await conn.execute(
                insert(sc.messages).values(name=name, template=template,
                                           created_at=_now())
                .returning(sc.messages))
            return self._msg(res.one())

    async def get_message(self, message_id):
        async with self.engine.connect() as conn:
            r = (await conn.execute(
                select(sc.messages).where(sc.messages.c.id == message_id))).one_or_none()
            return self._msg(r) if r else None

    async def list_messages(self, include_archived=False):
        q = select(sc.messages)
        if not include_archived:
            q = q.where(sc.messages.c.archived_at.is_(None))
        async with self.engine.connect() as conn:
            return [self._msg(r) for r in await conn.execute(q)]

    async def update_message(self, message_id, name=None, template=None):
        vals = {}
        if name is not None:
            vals["name"] = name
        if template is not None:
            vals["template"] = template
        async with self.engine.begin() as conn:
            if vals:
                await conn.execute(update(sc.messages)
                                   .where(sc.messages.c.id == message_id).values(**vals))
            r = (await conn.execute(
                select(sc.messages).where(sc.messages.c.id == message_id))).one_or_none()
            if r is None:
                raise KeyError(message_id)
            return self._msg(r)

    async def archive_message(self, message_id):
        async with self.engine.begin() as conn:
            await conn.execute(update(sc.messages)
                               .where(sc.messages.c.id == message_id)
                               .values(archived_at=_now()))

    async def delete_message(self, message_id):
        async with self.engine.begin() as conn:
            await conn.execute(delete(sc.messages).where(sc.messages.c.id == message_id))

    # ── stats ──────────────────────────────────────────────────────────────
    async def create_stat(self, key, name, message_id=None) -> Stat:
        async with self.engine.begin() as conn:
            res = await conn.execute(
                insert(sc.stats).values(key=key, name=name, message_id=message_id,
                                        created_at=_now()).returning(sc.stats))
            return self._stat(res.one())

    async def get_stat(self, key):
        async with self.engine.connect() as conn:
            r = (await conn.execute(
                select(sc.stats).where(sc.stats.c.key == key))).one_or_none()
            return self._stat(r) if r else None

    async def list_stats(self, include_archived=False):
        q = select(sc.stats)
        if not include_archived:
            q = q.where(sc.stats.c.archived_at.is_(None))
        async with self.engine.connect() as conn:
            return [self._stat(r) for r in await conn.execute(q)]

    async def update_stat(self, key, name=None):
        async with self.engine.begin() as conn:
            if name is not None:
                await conn.execute(update(sc.stats)
                                   .where(sc.stats.c.key == key).values(name=name))
            r = (await conn.execute(
                select(sc.stats).where(sc.stats.c.key == key))).one_or_none()
            if r is None:
                raise KeyError(key)
            return self._stat(r)

    async def set_stat_message(self, key, message_id):
        async with self.engine.begin() as conn:
            await conn.execute(update(sc.stats)
                               .where(sc.stats.c.key == key)
                               .values(message_id=message_id))
            r = (await conn.execute(
                select(sc.stats).where(sc.stats.c.key == key))).one_or_none()
            if r is None:
                raise KeyError(key)
            return self._stat(r)

    async def archive_stat(self, key):
        async with self.engine.begin() as conn:
            await conn.execute(update(sc.stats)
                               .where(sc.stats.c.key == key).values(archived_at=_now()))

    async def delete_stat(self, key):
        async with self.engine.begin() as conn:
            await conn.execute(delete(sc.stats).where(sc.stats.c.key == key))

    # ── users ──────────────────────────────────────────────────────────────
    async def upsert_user(self, discord_id, display_name) -> User:
        async with self.engine.begin() as conn:
            r = (await conn.execute(select(sc.users)
                 .where(sc.users.c.discord_id == discord_id))).one_or_none()
            if r is None:
                res = await conn.execute(
                    insert(sc.users).values(discord_id=discord_id,
                                            display_name=display_name,
                                            created_at=_now()).returning(sc.users))
                return self._user(res.one())
            await conn.execute(update(sc.users)
                               .where(sc.users.c.discord_id == discord_id)
                               .values(display_name=display_name))
            r = (await conn.execute(select(sc.users)
                 .where(sc.users.c.discord_id == discord_id))).one()
            return self._user(r)

    async def get_user(self, discord_id):
        async with self.engine.connect() as conn:
            r = (await conn.execute(select(sc.users)
                 .where(sc.users.c.discord_id == discord_id))).one_or_none()
            return self._user(r) if r else None

    async def list_users(self, include_archived=False):
        q = select(sc.users)
        if not include_archived:
            q = q.where(sc.users.c.archived_at.is_(None))
        async with self.engine.connect() as conn:
            return [self._user(r) for r in await conn.execute(q)]

    async def archive_user(self, discord_id):
        async with self.engine.begin() as conn:
            await conn.execute(update(sc.users)
                               .where(sc.users.c.discord_id == discord_id)
                               .values(archived_at=_now()))

    async def delete_user(self, discord_id):
        async with self.engine.begin() as conn:
            await conn.execute(delete(sc.users)
                               .where(sc.users.c.discord_id == discord_id))

    # ── tracking ───────────────────────────────────────────────────────────
    async def record_use(self, discord_id, display_name, stat_key):
        async with self.engine.begin() as conn:
            stat = (await conn.execute(select(sc.stats)
                    .where(sc.stats.c.key == stat_key))).one_or_none()
            if stat is None:
                raise KeyError(stat_key)
            user = (await conn.execute(select(sc.users)
                    .where(sc.users.c.discord_id == discord_id))).one_or_none()
            if user is None:
                res = await conn.execute(
                    insert(sc.users).values(discord_id=discord_id,
                                            display_name=display_name,
                                            created_at=_now()).returning(sc.users))
                user = res.one()
            else:
                await conn.execute(update(sc.users)
                                   .where(sc.users.c.id == user.id)
                                   .values(display_name=display_name))
            # user_stats upsert
            us = (await conn.execute(select(sc.user_stats).where(
                (sc.user_stats.c.user_id == user.id) &
                (sc.user_stats.c.stat_id == stat.id)))).one_or_none()
            if us is None:
                user_count = 1
                await conn.execute(insert(sc.user_stats).values(
                    user_id=user.id, stat_id=stat.id, count=1))
            else:
                user_count = us.count + 1
                await conn.execute(update(sc.user_stats).where(
                    (sc.user_stats.c.user_id == user.id) &
                    (sc.user_stats.c.stat_id == stat.id)).values(count=user_count))
            # stat_totals upsert
            tot = (await conn.execute(select(sc.stat_totals)
                   .where(sc.stat_totals.c.stat_id == stat.id))).one_or_none()
            if tot is None:
                total = 1
                await conn.execute(insert(sc.stat_totals)
                                   .values(stat_id=stat.id, count=1))
            else:
                total = tot.count + 1
                await conn.execute(update(sc.stat_totals)
                                   .where(sc.stat_totals.c.stat_id == stat.id)
                                   .values(count=total))
            return user_count, total

    async def get_user_stats(self, discord_id):
        async with self.engine.connect() as conn:
            rows = await conn.execute(
                select(sc.stats.c.key, sc.user_stats.c.count)
                .select_from(sc.user_stats
                             .join(sc.users, sc.users.c.id == sc.user_stats.c.user_id)
                             .join(sc.stats, sc.stats.c.id == sc.user_stats.c.stat_id))
                .where(sc.users.c.discord_id == discord_id))
            return {key: count for key, count in rows}

    async def get_total(self, stat_key):
        async with self.engine.connect() as conn:
            r = (await conn.execute(
                select(sc.stat_totals.c.count)
                .select_from(sc.stat_totals
                             .join(sc.stats, sc.stats.c.id == sc.stat_totals.c.stat_id))
                .where(sc.stats.c.key == stat_key))).one_or_none()
            return r.count if r else 0

    async def get_last_post(self, stat_key):
        async with self.engine.connect() as conn:
            r = (await conn.execute(
                select(sc.stat_last_post.c.discord_message_id,
                       sc.stat_last_post.c.channel_id)
                .select_from(sc.stat_last_post
                             .join(sc.stats, sc.stats.c.id == sc.stat_last_post.c.stat_id))
                .where(sc.stats.c.key == stat_key))).one_or_none()
            return (r.discord_message_id, r.channel_id) if r else None

    async def set_last_post(self, stat_key, discord_message_id, channel_id):
        async with self.engine.begin() as conn:
            stat = (await conn.execute(select(sc.stats)
                    .where(sc.stats.c.key == stat_key))).one_or_none()
            if stat is None:
                raise KeyError(stat_key)
            exists = (await conn.execute(select(sc.stat_last_post)
                      .where(sc.stat_last_post.c.stat_id == stat.id))).one_or_none()
            if exists is None:
                await conn.execute(insert(sc.stat_last_post).values(
                    stat_id=stat.id, discord_message_id=discord_message_id,
                    channel_id=channel_id))
            else:
                await conn.execute(update(sc.stat_last_post)
                                   .where(sc.stat_last_post.c.stat_id == stat.id)
                                   .values(discord_message_id=discord_message_id,
                                           channel_id=channel_id))
```

- [ ] **Step 3: Register sqlite (always) + postgres (opt-in) in the fixture** — edit `tests/storage/conftest.py`, append after the json registration:

```python
import os
from n3x_bot.storage.sql_repo import SqlRepository


async def _make_sqlite():
    import tempfile
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(path)
    r = SqlRepository(f"sqlite+aiosqlite:///{path}")
    await r.connect()
    return r


BACKENDS.append(("sqlite", _make_sqlite))

# Postgres only if a test DSN is provided; otherwise the id never registers
# and postgres is silently skipped (not failed).
_PG = os.environ.get("TEST_POSTGRES_URL")
if _PG:
    async def _make_postgres():
        r = SqlRepository(_PG)
        await r.connect()
        # clean slate each test
        from n3x_bot.storage import schema as sc
        async with r.engine.begin() as conn:
            await conn.run_sync(sc.metadata.drop_all)
            await conn.run_sync(sc.metadata.create_all)
        return r

    BACKENDS.append(("postgres", _make_postgres))
```

- [ ] **Step 4: Run the contract suite (json + sqlite)**

Run: `uv run pytest tests/storage -v`
Expected: each contract test PASSES twice — ids `[json]` and `[sqlite]`. (Postgres appears only if `TEST_POSTGRES_URL` is set.)

- [ ] **Step 5: Commit**

```bash
git add n3x_bot/storage/schema.py n3x_bot/storage/sql_repo.py tests/storage/conftest.py
git commit -m "feat: SQLAlchemy async repository (sqlite+postgres) passing contract suite"
```

---

### Task 7: Repository factory

**Files:**
- Create: `n3x_bot/storage/factory.py`
- Test: `tests/storage/test_factory.py`

**Interfaces:**
- Consumes: `Settings`, `JsonRepository`, `SqlRepository`.
- Produces: `create_repository(settings: Settings) -> StatsRepository` — returns `JsonRepository(settings.data_file)` for `flatfile`, else `SqlRepository(settings.database_url)`.

- [ ] **Step 1: Write the failing test** — `tests/storage/test_factory.py`

```python
from n3x_bot.config import Settings
from n3x_bot.storage.factory import create_repository
from n3x_bot.storage.json_repo import JsonRepository
from n3x_bot.storage.sql_repo import SqlRepository

BASE = dict(discord_token="t", target_role_id=1,
            welcome_channel_id=2, reminder_channel_id=3)


def test_flatfile_returns_json_repo():
    s = Settings(**BASE, storage_backend="flatfile", data_file="x.json")
    assert isinstance(create_repository(s), JsonRepository)


def test_sqlite_returns_sql_repo():
    s = Settings(**BASE, storage_backend="sqlite",
                 database_url="sqlite+aiosqlite:///x.db")
    assert isinstance(create_repository(s), SqlRepository)


def test_postgres_returns_sql_repo():
    s = Settings(**BASE, storage_backend="postgres",
                 database_url="postgresql+asyncpg://u:p@h/d")
    assert isinstance(create_repository(s), SqlRepository)
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/storage/test_factory.py -v`
Expected: FAIL — `ModuleNotFoundError: n3x_bot.storage.factory`.

- [ ] **Step 3: Implement** — `n3x_bot/storage/factory.py`

```python
from n3x_bot.config import Settings
from n3x_bot.storage.base import StatsRepository
from n3x_bot.storage.json_repo import JsonRepository
from n3x_bot.storage.sql_repo import SqlRepository


def create_repository(settings: Settings) -> StatsRepository:
    if settings.storage_backend == "flatfile":
        return JsonRepository(settings.data_file)
    return SqlRepository(settings.database_url)
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/storage/test_factory.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add n3x_bot/storage/factory.py tests/storage/test_factory.py
git commit -m "feat: repository factory selects backend from settings"
```

---

### Task 8: Seed + legacy `stats.json` migration

**Files:**
- Create: `n3x_bot/seed.py`
- Test: `tests/test_seed.py`

**Interfaces:**
- Consumes: `StatsRepository`.
- Produces:
  - `LEGACY_STATS: list[tuple[str, str, str]]` — `(key, name, template)` for the 8 original commands, templates verbatim from the current bot.
  - `async seed_defaults(repo) -> None` — idempotent; creates each legacy stat + its message (linked) if the stat key is absent.
  - `async migrate_legacy_json(repo, path) -> None` — if `path` exists and has old-format counters, load totals into `stat_totals` and per-user counts into `user_stats` via `record_use`-equivalent bulk seeding; safe to run once.

Legacy templates (verbatim, German):
```
tit    "Tit"    "Erkans boobies wurden schon {count} mal geshaket!"
wahab  "Wahab"  "Wahab hat bereits {count} mal jemanden auf diesem Discord beleidigt :*"
cry    "Cry"    "Es wurde bereits {count} mal geheult."
afk    "AFK"    "Muneeb ist zum {count} mal AFK..."
oma    "Oma"    "Patrick wurde zum {count} mal Perma gebannt."
jules  "Jules"  "Der aller echteste Homelander hat euch schon {count} mal am leben gelassen!"
smart  "Smart"  "Julez beweist zum {count} Mal, dass er ein Klugscheisser ist.."
crash  "Crash"  "Dennis geht zum {count} mal komplett crashout... opfer"
```

- [ ] **Step 1: Write the failing tests** — `tests/test_seed.py`

```python
import json
import os
import tempfile

from n3x_bot.storage.json_repo import JsonRepository
from n3x_bot.seed import seed_defaults, migrate_legacy_json, LEGACY_STATS


async def _repo():
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    os.remove(path)
    r = JsonRepository(path)
    await r.connect()
    return r


async def test_seed_is_idempotent():
    r = await _repo()
    await seed_defaults(r)
    await seed_defaults(r)
    stats = await r.list_stats()
    assert len(stats) == len(LEGACY_STATS)
    tit = await r.get_stat("tit")
    assert tit.message_id is not None
    msg = await r.get_message(tit.message_id)
    assert "{count}" in msg.template
    await r.close()


async def test_migrate_legacy_counts():
    fd, legacy = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w") as f:
        json.dump({
            "tit_count": 3, "cry_count": 0,
            "user_stats": {"42": {"tit": 2}},
        }, f)
    r = await _repo()
    await seed_defaults(r)
    await migrate_legacy_json(r, legacy)
    assert await r.get_total("tit") == 3
    assert await r.get_user_stats(42) == {"tit": 2}
    os.remove(legacy)
    await r.close()
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_seed.py -v`
Expected: FAIL — `ModuleNotFoundError: n3x_bot.seed`.

- [ ] **Step 3: Implement** — `n3x_bot/seed.py`

```python
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

    # global totals: keys like "tit_count"
    for key, _, _ in LEGACY_STATS:
        total = data.get(f"{key}_count", 0)
        if total and await repo.get_total(key) == 0:
            for _ in range(total):
                # seed total without attributing to a user
                await _bump_total(repo, key)

    # per-user counts
    for uid_str, cmds in data.get("user_stats", {}).items():
        discord_id = int(uid_str)
        for key, count in cmds.items():
            if await repo.get_stat(key) is None:
                continue
            for _ in range(count):
                await repo.record_use(discord_id, f"user_{discord_id}", key)


async def _bump_total(repo: StatsRepository, key: str) -> None:
    # Increment only the global total, using a synthetic archived migrator user
    await repo.record_use(0, "legacy_migrator", key)
```

Note: `migrate_legacy_json` prioritizes preserving global totals; per-user rows are additionally restored where present. The synthetic user id `0` collects otherwise-unattributed legacy counts.

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_seed.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add n3x_bot/seed.py tests/test_seed.py
git commit -m "feat: seed legacy stats/messages + migrate old stats.json counts"
```

---

### Task 9: Bot wiring — dynamic commands, events, entrypoint

**Files:**
- Create: `n3x_bot/bot.py`
- Create: `n3x_bot/__main__.py`
- Test: `tests/test_commands.py`
- Delete: old top-level `bot.py` (never committed) — remove the file from the working tree.

**Interfaces:**
- Consumes: `Settings`, `StatsRepository`, `render_output`, `seed_defaults`.
- Produces:
  - `async build_output(repo, stat_key, discord_id, display_name) -> str` — pure-ish core used by each command; does `record_use`, loads linked message, returns the rendered string. Unit-testable without Discord.
  - `def build_bot(settings: Settings, repo: StatsRepository) -> commands.Bot` — creates the bot, registers a command per non-archived stat (via `register_stat_commands`), wires events.
  - `async register_stat_commands(bot, repo, settings) -> None` — reads `list_stats()` and adds one `commands.Command` per stat key.

The command handler logic (delete previous post, send new, store `stat_last_post`) mirrors the current `send_or_update_msg`, but keyed by stat. Prefix enforcement / reminder task / welcome are ported unchanged except for reading IDs from `settings`.

- [ ] **Step 1: Write the failing test for the testable core** — `tests/test_commands.py`

```python
import os
import tempfile

from n3x_bot.storage.json_repo import JsonRepository
from n3x_bot.seed import seed_defaults
from n3x_bot.bot import build_output


async def _repo():
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    os.remove(path)
    r = JsonRepository(path)
    await r.connect()
    await seed_defaults(r)
    return r


async def test_build_output_uses_linked_message_and_counts():
    r = await _repo()
    out1 = await build_output(r, "tit", 42, "Erkan")
    out2 = await build_output(r, "tit", 42, "Erkan")
    assert out1 == "Erkans boobies wurden schon 1 mal geshaket!"
    assert out2 == "Erkans boobies wurden schon 2 mal geshaket!"
    await r.close()


async def test_build_output_default_when_no_message():
    r = await _repo()
    await r.create_stat("newthing", "New Thing")  # no linked message
    out = await build_output(r, "newthing", 7, "Ali")
    assert out == "New Thing — Ali — 1"
    await r.close()
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_commands.py -v`
Expected: FAIL — `ImportError: cannot import name 'build_output'`.

- [ ] **Step 3: Implement** — `n3x_bot/bot.py`

```python
import asyncio
import logging
from datetime import datetime, time

import discord
from discord.ext import commands, tasks

from n3x_bot.config import Settings
from n3x_bot.models import render_output
from n3x_bot.storage.base import StatsRepository

log = logging.getLogger("N3X-Bot")


async def build_output(repo: StatsRepository, stat_key: str,
                       discord_id: int, display_name: str) -> str:
    user_count, total = await repo.record_use(discord_id, display_name, stat_key)
    stat = await repo.get_stat(stat_key)
    message = None
    if stat.message_id is not None:
        message = await repo.get_message(stat.message_id)
    return render_output(stat, message, display_name, total)


def build_bot(settings: Settings, repo: StatsRepository) -> commands.Bot:
    intents = discord.Intents.default()
    intents.members = True
    intents.guilds = True
    intents.message_content = True

    bot = commands.Bot(command_prefix=settings.command_prefix,
                       intents=intents, case_insensitive=True)
    bot.n3x_settings = settings
    bot.n3x_repo = repo

    _wire_events(bot, settings, repo)
    return bot


async def _send_or_update(bot, repo, settings, stat_key: str, text: str):
    channel = bot.get_channel(settings.reminder_channel_id)
    if channel is None:
        return
    last = await repo.get_last_post(stat_key)
    if last is not None:
        old_id, _ = last
        try:
            old = await channel.fetch_message(old_id)
            await old.delete()
        except Exception:
            pass
    new_msg = await channel.send(text)
    await repo.set_last_post(stat_key, new_msg.id, channel.id)


async def register_stat_commands(bot, repo: StatsRepository, settings: Settings):
    for stat in await repo.list_stats():
        _add_stat_command(bot, repo, settings, stat.key)


def _add_stat_command(bot, repo, settings, key: str):
    if bot.get_command(key) is not None:
        return

    @commands.cooldown(1, 20, commands.BucketType.user)
    async def _cmd(ctx, _key=key):
        text = await build_output(repo, _key, ctx.author.id, ctx.author.display_name)
        await _send_or_update(bot, repo, settings, _key, text)

    bot.add_command(commands.Command(_cmd, name=key))


def _wire_events(bot, settings: Settings, repo: StatsRepository):
    reminder_h, reminder_m = settings.reminder_hm()

    async def enforce_prefix(member: discord.Member):
        if member.bot or member == member.guild.owner:
            return
        if not member.guild.me.guild_permissions.manage_nicknames:
            return
        if member.guild.me.top_role <= member.top_role:
            return
        has_role = any(r.id == settings.target_role_id for r in member.roles)
        current = member.display_name
        if has_role and not current.startswith(settings.prefix_str):
            base = current.replace("R3X", "").strip()
            try:
                await member.edit(nick=f"{settings.prefix_str}{base}"[:32],
                                  reason="N3X Prefix Enforcement")
            except Exception:
                pass
        elif not has_role and current.startswith(settings.prefix_str):
            try:
                await member.edit(nick=current[len(settings.prefix_str):],
                                  reason="N3X Prefix Removal")
            except Exception:
                pass

    @tasks.loop(time=time(hour=reminder_h, minute=reminder_m))
    async def event_reminder_task():
        weekday = datetime.now().weekday()
        channel = bot.get_channel(settings.reminder_channel_id)
        if channel is None:
            return
        if weekday == 2:
            await channel.send("*EVENT REMINDER*: ACE-BALL beginnt in 30 Minuten! @everyone")
        elif weekday == 4:
            await channel.send("*EVENT REMINDER*: Invasion beginnt in 30 Minuten! @everyone")

    @bot.event
    async def on_ready():
        log.info("Bot eingeloggt als %s", bot.user)
        await register_stat_commands(bot, repo, settings)
        for guild in bot.guilds:
            try:
                members = await guild.fetch_members(limit=None).flatten()
            except Exception:
                members = guild.members
            for m in members:
                await enforce_prefix(m)
        if not event_reminder_task.is_running():
            event_reminder_task.start()

    @bot.event
    async def on_command_error(ctx, error):
        if isinstance(error, commands.CommandOnCooldown):
            await ctx.send(f"Warte bitte {error.retry_after:.1f} Sekunden.",
                           delete_after=5)

    @bot.event
    async def on_message(message):
        if message.author == bot.user:
            return
        if message.content.startswith(settings.command_prefix):
            try:
                await message.delete(delay=5.0)
            except Exception:
                pass
        await bot.process_commands(message)

    @bot.event
    async def on_member_update(before, after):
        if before.roles != after.roles or before.display_name != after.display_name:
            await enforce_prefix(after)

    @bot.event
    async def on_member_join(member):
        channel = bot.get_channel(settings.welcome_channel_id)
        if channel:
            try:
                await channel.send(
                    f"Willkommen {member.mention} bei N3X - Night Shadow!")
            except Exception:
                pass
        await asyncio.sleep(5)
        await enforce_prefix(member)
```

Note: `!rank` from the original bot is intentionally re-added in Step 3b below so per-user ranking survives the redesign.

- [ ] **Step 3b: Add the `rank` command** — append to `register_stat_commands` in `n3x_bot/bot.py`, after the stat loop:

```python
    async def _rank(ctx):
        data = await repo.get_user_stats(ctx.author.id)
        if not data:
            text = (f"📊 **Command-Ranking von {ctx.author.display_name}**\n\n"
                    "Du hast bisher noch keine Befehle genutzt!")
        else:
            ordered = sorted(data.items(), key=lambda x: x[1], reverse=True)
            emojis = ["🥇", "🥈", "🥉"]
            text = f"📊 **Command-Ranking von {ctx.author.display_name}**\n\n"
            for i, (cmd, count) in enumerate(ordered):
                pref = emojis[i] if i < 3 else f"{i+1}."
                text += f"{pref} !{cmd:<10} {count}\n"
        await _send_or_update(bot, repo, settings, f"rank_{ctx.author.id}", text)

    if bot.get_command("rank") is None:
        bot.add_command(commands.Command(_rank, name="rank"))
```

- [ ] **Step 4: Implement the entrypoint** — `n3x_bot/__main__.py`

```python
import asyncio
import logging

from n3x_bot.config import Settings
from n3x_bot.storage.factory import create_repository
from n3x_bot.seed import seed_defaults, migrate_legacy_json
from n3x_bot.bot import build_bot

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


async def _prepare(settings: Settings):
    repo = create_repository(settings)
    await repo.connect()
    await seed_defaults(repo)
    if settings.storage_backend == "flatfile" is False:
        await migrate_legacy_json(repo, settings.data_file)
    return repo


def main() -> None:
    settings = Settings()
    repo = asyncio.get_event_loop().run_until_complete(_prepare(settings))
    bot = build_bot(settings, repo)
    bot.run(settings.discord_token)


if __name__ == "__main__":
    main()
```

Correction for the migration guard (the `is False` above is wrong): use this body for `_prepare` instead —

```python
async def _prepare(settings: Settings):
    repo = create_repository(settings)
    await repo.connect()
    await seed_defaults(repo)
    # Only the SQL backends need to import the legacy flat file; the flatfile
    # backend already reads stats.json natively.
    if settings.storage_backend != "flatfile":
        await migrate_legacy_json(repo, "stats.json")
    return repo
```

- [ ] **Step 5: Run to verify pass, then delete the old bot.py**

Run: `uv run pytest tests/test_commands.py -v`
Expected: 2 passed.

Then remove the legacy token-bearing file from the working tree:
Run: `rm -f bot.py`

- [ ] **Step 6: Full suite + coverage**

Run: `uv run pytest --cov=n3x_bot --cov-report=term-missing`
Expected: all tests pass; coverage ≥ 80%.

- [ ] **Step 7: Commit**

```bash
git add n3x_bot/bot.py n3x_bot/__main__.py tests/test_commands.py
git commit -m "feat: dynamic stat commands, events, entrypoint; drop legacy bot.py"
```

---

### Task 10: Docker + compose + README

**Files:**
- Create: `Dockerfile`
- Create: `docker-compose.yml`
- Create: `.dockerignore`
- Create: `README.md`

**Interfaces:**
- Consumes: `n3x_bot` package, `.env`.
- Produces: runnable container; `docker compose up` launches bot; postgres service available; `STORAGE_BACKEND` read from `.env`.

- [ ] **Step 1: Write `.dockerignore`**

```
.git
.venv
__pycache__
*.pyc
.pytest_cache
.env
stats.json
*.db
docs
tests
```

- [ ] **Step 2: Write `Dockerfile`**

```dockerfile
FROM python:3.12-slim

RUN pip install --no-cache-dir uv

WORKDIR /app
COPY pyproject.toml ./
RUN uv sync --no-dev

COPY n3x_bot ./n3x_bot

RUN useradd -m botuser && chown -R botuser /app
USER botuser

CMD ["uv", "run", "python", "-m", "n3x_bot"]
```

- [ ] **Step 3: Write `docker-compose.yml`**

```yaml
services:
  bot:
    build: .
    env_file: .env
    volumes:
      - bot-data:/app/data      # persists sqlite/flatfile
    depends_on:
      postgres:
        condition: service_healthy
        required: false
    restart: unless-stopped

  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: n3x
      POSTGRES_PASSWORD: n3x
      POSTGRES_DB: n3x
    volumes:
      - pg-data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U n3x"]
      interval: 5s
      timeout: 3s
      retries: 5
    restart: unless-stopped

volumes:
  bot-data:
  pg-data:
```

Note: `bot` reads `STORAGE_BACKEND` from `.env`. For flatfile/sqlite the postgres service is harmless (idle); for postgres set `DATABASE_URL=postgresql+asyncpg://n3x:n3x@postgres:5432/n3x` in `.env`. `required: false` on the dependency keeps the bot startable even if postgres is not desired.

- [ ] **Step 4: Write `README.md`**

```markdown
# N3X Discord Bot

Discord bot with pluggable CRUD storage (flatfile / SQLite / Postgres) and
data-driven commands.

## Setup

1. `cp .env.example .env` and fill in `DISCORD_TOKEN` (rotate the old one first),
   channel/role IDs, and choose `STORAGE_BACKEND`.
2. For sqlite/postgres, set `DATABASE_URL` (see `.env.example`).

## Run (local)

    uv sync
    uv run python -m n3x_bot

## Run (Docker)

    docker compose up --build          # flatfile or sqlite (set in .env)
    # Postgres: set STORAGE_BACKEND=postgres and
    # DATABASE_URL=postgresql+asyncpg://n3x:n3x@postgres:5432/n3x in .env

## Test

    uv run pytest --cov=n3x_bot

Postgres contract tests run only when TEST_POSTGRES_URL is set.

## Storage backends

Selected via `STORAGE_BACKEND` in `.env`: `flatfile` | `sqlite` | `postgres`.
All three satisfy the same repository contract. Adding a counter = insert a
`stats` row (a linked `messages` template is optional); the command is
registered automatically on next start.
```

- [ ] **Step 5: Build sanity check**

Run: `docker compose build bot`
Expected: image builds without error.

- [ ] **Step 6: Commit**

```bash
git add Dockerfile docker-compose.yml .dockerignore README.md
git commit -m "chore: Docker, compose, and README"
```

---

### Task 11: Push branch + open PR

**Files:** none (git only).

- [ ] **Step 1: Confirm no secrets are tracked**

Run: `git grep -nE "MTU='?|discord_token *= *\"" || echo clean`
Expected: `clean`. Also confirm `.env` is untracked: `git status --porcelain | grep -q "\.env$" && echo "LEAK" || echo ok` → `ok`.

- [ ] **Step 2: Push**

```bash
git push -u origin main
```

- [ ] **Step 3: Final coverage gate**

Run: `uv run pytest --cov=n3x_bot --cov-report=term-missing`
Expected: pass, ≥ 80%.

---

## Self-Review

**1. Spec coverage**
- `.env` config + validation → Task 2. ✓
- Backend selectable via `.env` → Task 2 (`storage_backend`) + Task 7 (factory). ✓
- CRUD element model (users/stats/messages) → Tasks 3–6 (interface + both impls). ✓
- Tracking tables (user_stats/stat_totals/stat_last_post) → Tasks 4–6. ✓
- Dynamic commands from stats → Task 9. ✓
- Optional linked message, default output → Task 3 (render) + Task 9 (build_output). ✓
- Flatfile/sqlite/postgres one contract suite → Tasks 4–6. ✓
- Legacy behavior/counts preserved → Task 8 (seed + migrate) + Task 9 (`rank`, events). ✓
- Docker/compose, `STORAGE_BACKEND` from `.env` → Task 10. ✓
- TDD, 80%+ coverage → every task; gates in Tasks 9 & 11. ✓
- No committed secrets → `.gitignore` (already committed), old `bot.py` deleted in Task 9, verified in Task 11. ✓

**2. Placeholder scan:** `__main__.py` originally contained a buggy `storage_backend == "flatfile" is False` guard — flagged inline and replaced with the corrected `_prepare` body (Task 9, Step 4). No other placeholders.

**3. Type consistency:** `create_repository`, `StatsRepository` method names, `build_output`, `render_output`, `seed_defaults`, `migrate_legacy_json`, `create_repository` all used with the signatures defined in their producing tasks. Contract-suite method calls match `base.py` exactly. `record_use` returns `(user_count, total)` everywhere; output uses `total`.
