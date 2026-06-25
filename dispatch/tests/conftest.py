from __future__ import annotations

import sys
from pathlib import Path

import pytest

from orxtra.dispatch import InMemoryDispatchBackend

# Make test modules importable for ScriptAction tests that use
# importlib.import_module("test_<name>:handler").
_tests_dir = str(Path(__file__).resolve().parent)
if _tests_dir not in sys.path:
    sys.path.insert(0, _tests_dir)


@pytest.fixture
def backend() -> InMemoryDispatchBackend:
    return InMemoryDispatchBackend()
