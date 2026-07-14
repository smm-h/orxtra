# Phase 3.3 replaces with real tests
import orxtra.notification


def test_version_exists() -> None:
    assert isinstance(orxtra.notification.__version__, str)
