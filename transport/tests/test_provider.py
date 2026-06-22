from __future__ import annotations

import dataclasses

import pytest
from orxtra.transport._provider import RetryPolicy


class TestRetryPolicy:
    def test_construction(self) -> None:
        policy = RetryPolicy(
            max_retries=3,
            backoff_base_seconds=1.0,
            backoff_max_seconds=30.0,
            jitter=True,
        )
        assert policy.max_retries == 3
        assert policy.backoff_base_seconds == 1.0
        assert policy.backoff_max_seconds == 30.0
        assert policy.jitter is True

    def test_frozen(self) -> None:
        policy = RetryPolicy(
            max_retries=3,
            backoff_base_seconds=1.0,
            backoff_max_seconds=30.0,
            jitter=True,
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            policy.max_retries = 5  # type: ignore[misc]

    def test_no_jitter(self) -> None:
        policy = RetryPolicy(
            max_retries=0,
            backoff_base_seconds=0.5,
            backoff_max_seconds=10.0,
            jitter=False,
        )
        assert policy.jitter is False
        assert policy.max_retries == 0
