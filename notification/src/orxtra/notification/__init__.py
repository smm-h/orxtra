"""Notification delivery -- persisted notifications for principals.

Uses the dispatch subscription system. Deliberately minimal: a delivery
table, a PG NOTIFY trigger, and backends implementing the
NotificationPort protocol.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("orxtra-notification")
except PackageNotFoundError:
    __version__ = "0.0.0"

__all__ = [
    "__version__",
]
