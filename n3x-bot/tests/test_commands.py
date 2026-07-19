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


async def test_build_output_uses_linked_message_and_per_user_count_with_mention():
    r = await _repo()
    out1 = await build_output(r, "tit", 42, "Erkan")
    out2 = await build_output(r, "tit", 42, "Erkan")
    # Mentions the invoker (<@id>) and counts THIS user's uses.
    assert out1 == "<@42> hat Erkans Boobies schon 1 mal geshaked! 🤲"
    assert out2 == "<@42> hat Erkans Boobies schon 2 mal geshaked! 🤲"
    await r.close()


async def test_build_output_counts_are_per_user_not_global():
    r = await _repo()
    a = await build_output(r, "cry", 1, "Ann")
    b = await build_output(r, "cry", 2, "Ben")   # different user
    # each user sees their OWN count of 1, not a shared global 2
    assert a == "<@1> hat schon 1 mal geheult. 😭"
    assert b == "<@2> hat schon 1 mal geheult. 😭"
    await r.close()


async def test_build_output_default_when_no_message():
    r = await _repo()
    await r.create_stat("newthing", "New Thing")  # no linked message
    out = await build_output(r, "newthing", 7, "Ali")
    assert out == "New Thing — <@7> — 1"
    await r.close()
