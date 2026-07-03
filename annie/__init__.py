"""Annie — a local-first browser UI to explore, inspect, and validate a video annotation dataset.

The package is organised as a strict layered architecture; see ``CONTRIBUTING.md``
for the import-direction rules. The version is derived from the git tag at build
time by ``hatch-vcs``.
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("annie")
except PackageNotFoundError:  # pragma: no cover - only when running from a source tree
    __version__ = "0.0.0+unknown"

__all__ = ["__version__"]
