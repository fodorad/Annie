"""Reveal a file in the OS file manager (service over the platform shell).

Annie is local-first — the browser and the filesystem are the same machine — so a
"Show at location" button can open the user's native file manager with the file
selected. The command is platform-dependent:

* macOS  → ``open -R <file>``           (Finder, file highlighted)
* Windows→ ``explorer /select,<file>``  (Explorer, file highlighted)
* other  → ``xdg-open <dir>``           (opens the containing folder)

The command builder (:func:`reveal_command`) is pure and unit-tested; the thin
:func:`reveal` wrapper spawns it.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

#: File Docker creates in every container; its presence is the standard signal
#: that Annie is running inside a container.
_DOCKER_SENTINEL = Path("/.dockerenv")


def is_docker(sentinel: Path = _DOCKER_SENTINEL) -> bool:
    """Return ``True`` when Annie is running inside a Docker container.

    Docker creates ``/.dockerenv`` in every container; its presence is the
    standard detection mechanism. The ``sentinel`` parameter exists solely to
    make this function testable without mocking.

    Args:
        sentinel: Path whose existence signals a Docker environment.

    Returns:
        ``True`` if the sentinel exists, ``False`` otherwise.
    """
    return sentinel.exists()


def reveal_command(path: str | Path, platform: str) -> list[str]:
    """Build the file-manager command that reveals ``path`` on ``platform``.

    Args:
        path: The file (or folder) to reveal.
        platform: A :data:`sys.platform` value (``"darwin"``, ``"win32"``, …).

    Returns:
        The argv list to spawn. On unknown platforms the containing directory is
        opened with ``xdg-open``.
    """
    target = Path(path)
    if platform == "darwin":
        return ["open", "-R", str(target)]
    if platform == "win32":
        return ["explorer", f"/select,{target}"]
    return ["xdg-open", str(target.parent)]


def reveal(path: str | Path) -> None:  # pragma: no cover - spawns an OS process
    """Open the OS file manager with ``path`` selected (fire-and-forget).

    Args:
        path: The file to reveal.
    """
    subprocess.Popen(reveal_command(path, sys.platform))  # noqa: S603 - argv list, no shell
