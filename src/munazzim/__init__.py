"""Munazzim daily planner core package."""

from importlib.metadata import version, PackageNotFoundError

try:
    __version__ = version("munazzim")
except PackageNotFoundError:  # pragma: no cover
    __version__ = "0.0.0"

__all__ = ["__version__"]
