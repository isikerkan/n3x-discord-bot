"""Single-instance guard.

The bot is deployed under AMP's ``UpdateAndStart``: every start git-pulls the
newest ``main`` and launches ``python3 -m n3x_bot``. A botched Stop/Restart can
leave the previous process alive and still connected to the Discord gateway —
so two (or more) bots handle every event and messages get duplicated (voice
log, gate posts, etc.).

On startup the freshly-launched process kills any OTHER live ``n3x_bot``
process before it connects. Newest-wins matches the deploy model (the new
process is always the newest code). Pure ``/proc`` scan — no psutil.
"""
import logging
import os
import signal

log = logging.getLogger(__name__)

# Marker args that identify one of *our* processes: launched as `python -m
# n3x_bot`. Both must be present so we never match an unrelated python process.
_MODULE_MARKER = "n3x_bot"
_MODULE_FLAG = "-m"


def _read_cmdline(pid: str) -> list[str]:
    """Return the NUL-split argv of ``/proc/<pid>/cmdline`` ([] on any error)."""
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            raw = f.read()
    except (OSError, ValueError):
        return []
    return [a.decode("utf-8", "replace") for a in raw.split(b"\x00") if a]


def _iter_proc_cmdlines():
    """Yield ``(pid:int, argv:list[str])`` for every numeric ``/proc`` entry."""
    try:
        entries = os.listdir("/proc")
    except OSError:
        return
    for name in entries:
        if not name.isdigit():
            continue
        argv = _read_cmdline(name)
        if argv:
            yield int(name), argv


def _is_our_process(argv: list[str]) -> bool:
    """True if argv launched our module (`python -m n3x_bot`), not pytest etc."""
    return _MODULE_FLAG in argv and _MODULE_MARKER in argv


def stale_pids(entries, self_pid: int) -> list[int]:
    """Pure: from ``(pid, argv)`` pairs, the OTHER n3x_bot pids to kill.

    Excludes ``self_pid`` so a process never kills itself. Testable without
    touching ``/proc``.
    """
    return [pid for pid, argv in entries
            if pid != self_pid and _is_our_process(argv)]


def kill_stale_instances() -> list[int]:
    """SIGTERM every other live ``n3x_bot`` process. Best-effort; returns the
    pids signalled. Runs before the gateway connect so the old process releases
    its Discord session."""
    self_pid = os.getpid()
    victims = stale_pids(_iter_proc_cmdlines(), self_pid)
    for pid in victims:
        try:
            os.kill(pid, signal.SIGTERM)
            log.warning("single-instance guard: terminated stale n3x_bot pid %d", pid)
        except OSError:
            pass  # already gone / not permitted — nothing to do
    return victims
