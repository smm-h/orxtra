"""Parity test: EVENTS_CHANNEL constant matches the trigger DDL.

The generated trigger DDL in schema/_generated/post_tables.py uses
pg_notify('orxtra_events', ...). The EVENTS_CHANNEL constant in
orxtra.trace must match that channel name exactly.  If someone
changes one without the other, this test catches the drift.
"""

from __future__ import annotations

import re
from pathlib import Path

from orxtra.trace import EVENTS_CHANNEL


# Path to the generated trigger DDL -- relative to the repo root.
_POST_TABLES = (
    Path(__file__).resolve().parents[2]  # trace/tests -> trace -> repo root
    / "schema"
    / "_generated"
    / "post_tables.py"
)


def test_events_channel_matches_trigger_ddl() -> None:
    """EVENTS_CHANNEL == the channel argument in pg_notify(...) in the DDL."""
    ddl_text = _POST_TABLES.read_text()

    # Extract all pg_notify channel names from the generated DDL.
    # Pattern: pg_notify('channel_name', ...)
    matches = re.findall(r"pg_notify\('([^']+)'", ddl_text)

    assert len(matches) > 0, (
        f"No pg_notify calls found in {_POST_TABLES}; "
        "the trigger DDL may have changed structure"
    )

    # Every pg_notify call should use the same channel.
    unique_channels = set(matches)
    assert len(unique_channels) == 1, (
        f"Multiple distinct pg_notify channels in DDL: {unique_channels}"
    )

    ddl_channel = unique_channels.pop()
    assert EVENTS_CHANNEL == ddl_channel, (
        f"EVENTS_CHANNEL={EVENTS_CHANNEL!r} does not match "
        f"the trigger DDL channel={ddl_channel!r}"
    )
