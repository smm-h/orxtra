"""Parity test: NOTIFY channel constants match the trigger DDL.

The generated trigger DDL in schema/_generated/post_tables.py uses
pg_notify('orxtra_events', ...) and pg_notify('orxtra_notifications', ...).
The channel constants in orxtra.trace and orxtra.notification must match
those channel names exactly.  If someone changes one without the other,
this test catches the drift.
"""

from __future__ import annotations

import re
from pathlib import Path

from orxtra.notification import NOTIFICATIONS_CHANNEL
from orxtra.trace import EVENTS_CHANNEL

# Path to the generated trigger DDL -- relative to the repo root.
_POST_TABLES = (
    Path(__file__).resolve().parents[2]  # trace/tests -> trace -> repo root
    / "schema"
    / "_generated"
    / "post_tables.py"
)

# All known PG NOTIFY channels and their expected constants.
_EXPECTED_CHANNELS: dict[str, str] = {
    "orxtra_events": EVENTS_CHANNEL,
    "orxtra_notifications": NOTIFICATIONS_CHANNEL,
}


def test_each_known_channel_appears_in_ddl() -> None:
    """Every known channel constant appears at least once in the DDL."""
    ddl_text = _POST_TABLES.read_text()
    ddl_channels = set(re.findall(r"pg_notify\('([^']+)'", ddl_text))

    assert len(ddl_channels) > 0, (
        f"No pg_notify calls found in {_POST_TABLES}; "
        "the trigger DDL may have changed structure"
    )

    for ddl_name, constant_value in _EXPECTED_CHANNELS.items():
        assert constant_value == ddl_name, (
            f"Channel constant {constant_value!r} does not match "
            f"expected DDL channel {ddl_name!r}"
        )
        assert ddl_name in ddl_channels, (
            f"Expected channel {ddl_name!r} not found in DDL. "
            f"Found: {ddl_channels}"
        )


def test_no_unknown_channels_in_ddl() -> None:
    """DDL does not introduce channels without a matching constant."""
    ddl_text = _POST_TABLES.read_text()
    ddl_channels = set(re.findall(r"pg_notify\('([^']+)'", ddl_text))
    expected = set(_EXPECTED_CHANNELS.keys())
    unexpected = ddl_channels - expected

    assert not unexpected, (
        f"DDL contains pg_notify channels without matching constants: "
        f"{unexpected}. Add constants and update _EXPECTED_CHANNELS."
    )
