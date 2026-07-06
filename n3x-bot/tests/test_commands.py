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
