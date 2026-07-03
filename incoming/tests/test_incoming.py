"""Tests for the incoming sub-project.

Scaffolding-only: verifies the package is importable and version is accessible.
"""

from __future__ import annotations


def test_import() -> None:
    import orxtra.incoming

    assert hasattr(orxtra.incoming, "__version__")


def test_version_string() -> None:
    from orxtra.incoming import __version__

    assert isinstance(__version__, str)
    assert len(__version__) > 0
