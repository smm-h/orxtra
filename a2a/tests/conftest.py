from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path

# Fix a2a namespace package shadow. pytest --import-mode=importlib
# registers the workspace a2a/ directory as a namespace package,
# shadowing the a2a SDK. Replace with the real SDK from site-packages.
_a2a_mod = sys.modules.get("a2a")
if _a2a_mod is not None and getattr(_a2a_mod, "__file__", None) is None:
    for _p in sys.path:
        _init = Path(_p) / "a2a" / "__init__.py"
        if _init.is_file():
            _spec = importlib.util.spec_from_file_location(
                "a2a",
                _init,
                submodule_search_locations=[str(Path(_p) / "a2a")],
            )
            if _spec and _spec.loader:
                _real_a2a = importlib.util.module_from_spec(_spec)
                sys.modules["a2a"] = _real_a2a
                _spec.loader.exec_module(_real_a2a)
                break
