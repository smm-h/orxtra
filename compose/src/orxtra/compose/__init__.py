from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("orxtra-compose")
except PackageNotFoundError:
    __version__ = "0.0.0"

from orxtra.compose._engine import CompositionEngine
from orxtra.compose._fragment import Fragment, FragmentProvider
from orxtra.compose._includes import resolve_includes
from orxtra.compose._providers import FileFragmentProvider
from orxtra.compose._variables import resolve_variables

__all__ = [
    "__version__",
    "CompositionEngine",
    "FileFragmentProvider",
    "Fragment",
    "FragmentProvider",
    "resolve_includes",
    "resolve_variables",
]
