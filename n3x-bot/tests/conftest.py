"""Shared test configuration.

Speed: production code sleeps to pace real Discord work (e.g. the 5s settle
before nick enforcement in `on_member_join`, the 1s throttle per welcome card).
Those real delays dominated the suite wall-clock (four member-lifecycle tests
alone burned ~25s). Replace `asyncio.sleep` with an immediate no-op for every
test — no unit test asserts on real elapsed time; timer/loop logic is driven by
injected `now` values, not by sleeping.
"""
import asyncio

import pytest


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    async def _instant(*_args, **_kwargs):
        return None

    monkeypatch.setattr(asyncio, "sleep", _instant)
