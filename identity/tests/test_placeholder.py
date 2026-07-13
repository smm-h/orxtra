"""Placeholder test for the identity package skeleton.

Phase 1.4 replaces this file with real tests for the identity backends
and principal registry. Until then, it only asserts the package imports
and exposes a version, keeping CI green for the empty-but-valid package.
"""

from __future__ import annotations


def test_identity_imports() -> None:
    import orxtra.identity

    assert orxtra.identity.__version__
