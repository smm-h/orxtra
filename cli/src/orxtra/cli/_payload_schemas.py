"""Declared payload schemas for orxtra's machine-mode commands.

strictcli's machine mode (``--json``) writes one document to stdout -- the
envelope -- and a command's machine output is its ``payload`` member. Every
such command declares that payload's JSON Schema at registration time, and
the framework validates the value against the declaration where it writes the
envelope.

The CLI is a thin front for ``dispatch()``: what a command prints is whatever
capability it called returned, and those result types are owned by the
services layer, not by this package. So the declarations here state the shape
this seam actually knows -- a row, a list of rows, or the pricing table's
nested map -- and no more. Declaring a per-capability field list here would
mean a second, hand-copied statement of types the services layer already
owns, and the framework would hard-fail a run the moment the two disagreed.
"""

from __future__ import annotations

from typing import Any

#: One record: a run report, an inbox item, a config snapshot.
ROW: dict[str, Any] = {"type": "object"}

#: A listing: runs, inbox items, events, tasks, notepad entries, transcript
#: turns.
ROWS: dict[str, Any] = {"type": "array", "items": {"type": "object"}}

#: The internal pricing table: model name -> rate name -> amount, every
#: amount a string because a price is a decimal and the envelope's numbers
#: are doubles.
PRICING: dict[str, Any] = {
    "type": "object",
    "additionalProperties": {
        "type": "object",
        "additionalProperties": {"type": "string"},
    },
}
