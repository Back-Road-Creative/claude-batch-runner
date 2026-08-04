"""`__version__` agrees with pyproject.toml. Never spawns a real `claude` process."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

import claude_batch_runner

REPO_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = REPO_ROOT / "pyproject.toml"


def _declared_version() -> str:
    with PYPROJECT.open("rb") as fh:
        return tomllib.load(fh)["project"]["version"]


def test_version_matches_pyproject():
    """0.1.1 bumped pyproject and left `__version__` at 0.1.0 for a whole release.

    `__version__` is now derived from installed metadata rather than a second
    literal, so the only way this can disagree is a stale editable install --
    which is itself worth failing on, because every other check in the repo
    (release.yml's tag cross-check included) trusts pyproject.
    """
    assert claude_batch_runner.__version__ == _declared_version()


def test_version_is_not_the_uninstalled_fallback():
    """The PackageNotFoundError branch must not be what the suite exercises."""
    if claude_batch_runner.__version__ == "0.0.0.dev0":
        pytest.fail("package is not installed; run `pip install -e .[dev]` before pytest")
