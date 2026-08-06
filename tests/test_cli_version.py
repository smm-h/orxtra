"""Regression test for the CLI version lookup.

The published distribution is named ``orxtra`` (see ``[project.name]`` in
pyproject.toml). Only that distribution ships in the wheel; the workspace
member distribution ``orxtra-cli`` is never published. The CLI therefore
must resolve its version from the ``orxtra`` distribution, not from
``orxtra-cli`` -- otherwise ``importlib.metadata.version("orxtra-cli")``
raises ``PackageNotFoundError`` at import time in the installed package,
crashing every ``orxtra`` invocation before it can do anything.

Locally ``orxtra-cli`` exists as an editable workspace member, so the crash
is invisible; the versions merely diverge. Asserting the app reports the
``orxtra`` distribution version reproduces the bug locally (divergent
versions) and guards against reintroducing a lookup of an unpublished name.
"""

from __future__ import annotations

import importlib.metadata

import orxtra.cli
from orxtra.cli._cli import app


def test_cli_version_resolves_to_published_distribution() -> None:
    assert app.version == importlib.metadata.version("orxtra")


def test_package_dunder_version_resolves_to_published_distribution() -> None:
    """``orxtra.cli.__version__`` must resolve from the published ``orxtra``
    distribution, not the unpublished workspace member ``orxtra-cli``.

    Looking up ``orxtra-cli`` silently degrades ``__version__`` to ``"0.0.0"``
    in the installed wheel (where only ``orxtra`` metadata exists), masking a
    renamed/missing distribution -- exactly the silent-degradation pattern the
    conventions forbid. Locally both distributions exist as editable workspace
    members with divergent versions, so this assertion reproduces the bug.
    """
    published = importlib.metadata.version("orxtra")
    assert orxtra.cli.__version__ == published
    assert orxtra.cli.__version__ != "0.0.0"
