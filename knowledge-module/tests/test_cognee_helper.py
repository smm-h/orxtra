from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest


class TestRequireCognee:
    def test_require_cognee_when_installed(self) -> None:
        mock_cognee = MagicMock()
        with patch.dict(sys.modules, {"cognee": mock_cognee}):
            from orxt.knowledge_module._cognee_import import require_cognee  # noqa: PLC0415

            result = require_cognee()
        assert result is mock_cognee

    def test_require_cognee_when_missing(self) -> None:
        with patch.dict(sys.modules, {"cognee": None}):
            from orxt.knowledge_module._cognee_import import require_cognee  # noqa: PLC0415

            with pytest.raises(RuntimeError, match="cognee is required for the knowledge module"):
                require_cognee()
