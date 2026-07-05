import pytest

from n3x_bot.storage.json_repo import JsonRepository


async def _make_json(tmp_path_holder=[]):
    import tempfile, os
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    os.remove(path)  # start clean; connect() will create it
    r = JsonRepository(path)
    await r.connect()
    return r


# Backends are appended by Task 5 (json) and Task 6 (sql).
# Each entry: (id, async factory returning a *connected* StatsRepository).
BACKENDS: list = [("json", _make_json)]


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
