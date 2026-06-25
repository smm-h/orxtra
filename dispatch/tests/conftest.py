from __future__ import annotations

import pytest

from orxtra.dispatch import InMemoryDispatchBackend


@pytest.fixture
def backend() -> InMemoryDispatchBackend:
    return InMemoryDispatchBackend()
