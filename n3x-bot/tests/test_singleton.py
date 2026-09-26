"""Single-instance guard: `stale_pids` picks the OTHER n3x_bot processes of
the same instance (same working directory)."""
from n3x_bot.singleton import stale_pids, _is_our_process


_OURS = ["/opt/venv/bin/python3", "-u", "-m", "n3x_bot"]
_HERE = "/home/amp/.ampdata/instances/n3x_hera01/n3x-bot/n3x-bot"
_OTHER = "/home/amp/.ampdata/instances/n3x_alliance01/n3x-bot/n3x-bot"


def test_our_process_is_recognised():
    assert _is_our_process(_OURS)


def test_pytest_and_editors_are_not_ours():
    assert not _is_our_process(["/usr/bin/python3", "-m", "pytest"])
    assert not _is_our_process(["vim", "n3x_bot/bot.py"])  # has marker, no -m
    assert not _is_our_process(["python3", "-c", "print('n3x_bot')"])


def test_stale_pids_excludes_self_and_non_matching():
    entries = [
        (100, _OURS, _HERE),                        # a stale sibling -> kill
        (200, _OURS, _HERE),                        # this process -> keep
        (300, ["python3", "-m", "pytest"], _HERE),  # unrelated -> keep
        (400, ["bash"], _HERE),                     # unrelated -> keep
    ]
    assert stale_pids(entries, 200, _HERE) == [100]


def test_stale_pids_empty_when_only_self():
    assert stale_pids([(200, _OURS, _HERE)], 200, _HERE) == []


def test_stale_pids_multiple_orphans():
    entries = [(1, _OURS, _HERE), (2, _OURS, _HERE), (3, _OURS, _HERE)]
    assert stale_pids(entries, 2, _HERE) == [1, 3]


def test_another_instance_on_the_host_is_left_alone():
    entries = [(1, _OURS, _OTHER), (2, _OURS, _HERE), (3, _OURS, _HERE)]
    assert stale_pids(entries, 2, _HERE) == [3]


def test_unknown_cwd_is_never_killed():
    entries = [(1, _OURS, None), (2, _OURS, _HERE)]
    assert stale_pids(entries, 2, _HERE) == []
    assert stale_pids([(1, _OURS, _HERE)], 2, None) == []
