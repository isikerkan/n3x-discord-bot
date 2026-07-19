"""Single-instance guard: `stale_pids` picks the OTHER n3x_bot processes."""
from n3x_bot.singleton import stale_pids, _is_our_process


_OURS = ["/opt/venv/bin/python3", "-u", "-m", "n3x_bot"]


def test_our_process_is_recognised():
    assert _is_our_process(_OURS)


def test_pytest_and_editors_are_not_ours():
    assert not _is_our_process(["/usr/bin/python3", "-m", "pytest"])
    assert not _is_our_process(["vim", "n3x_bot/bot.py"])  # has marker, no -m
    assert not _is_our_process(["python3", "-c", "print('n3x_bot')"])


def test_stale_pids_excludes_self_and_non_matching():
    entries = [
        (100, _OURS),                       # a stale sibling -> kill
        (200, _OURS),                       # this process -> keep
        (300, ["python3", "-m", "pytest"]),  # unrelated -> keep
        (400, ["bash"]),                    # unrelated -> keep
    ]
    assert stale_pids(entries, self_pid=200) == [100]


def test_stale_pids_empty_when_only_self():
    assert stale_pids([(200, _OURS)], self_pid=200) == []


def test_stale_pids_multiple_orphans():
    entries = [(1, _OURS), (2, _OURS), (3, _OURS)]
    assert stale_pids(entries, self_pid=2) == [1, 3]
