"""Decorator-based tool definition infrastructure.

The ``@tool`` decorator converts a typed async function into a
``ToolTemplate`` -- an unbound tool definition that can be bound with
dependencies to produce a standard ``Tool``.
"""

from __future__ import annotations

import inspect
import sys
from typing import Any, Generic, TypeVar, get_type_hints

from pydantic import BaseModel, ValidationError

from orxtra.protocols._results import Renderer, ToolOutput
from orxtra.protocols._tool import Tool, ToolError

T = TypeVar("T")


class ToolTemplate(Generic[T]):
    """An unbound tool definition. Call ``.bind(**deps)`` to get a ``Tool``.

    Created by the ``@tool`` decorator. The template captures the function,
    its Pydantic params model, renderer, and metadata. Dependencies are
    supplied later via ``bind()``, producing a ready-to-use ``Tool``.
    """

    __slots__ = (
        "name",
        "description",
        "_fn",
        "_params_model",
        "_renderer",
        "_suspending",
        "_schema",
    )

    def __init__(
        self,
        name: str,
        description: str,
        fn: Any,  # noqa: ANN401
        params_model: type[BaseModel],
        renderer: Renderer[Any],
        *,
        suspending: bool = False,
    ) -> None:
        self.name = name
        self.description = description
        self._fn = fn
        self._params_model = params_model
        self._renderer = renderer
        self._suspending = suspending
        self._schema: dict[str, Any] = params_model.model_json_schema()

    def bind(self, **deps: Any) -> Tool:  # noqa: ANN401
        """Bind dependencies to produce a ready-to-use ``Tool``."""
        template = self

        async def execute(args: dict[str, Any]) -> ToolOutput[Any]:
            # Validate input via Pydantic
            try:
                validated = template._params_model.model_validate(args)
            except ValidationError as exc:
                msg = f"Invalid tool arguments: {exc}"
                raise ToolError(msg) from exc

            # Call the decorated function with validated params + bound deps
            result = await template._fn(validated, **deps)

            # If the function already returned a ToolOutput, use it directly.
            # This supports tools with complex context-dependent rendering
            # (e.g., line-numbered file content, previews) where a generic
            # renderer cannot reproduce the text.
            if isinstance(result, ToolOutput):
                return result

            # Render to text
            text = template._renderer.render(result)
            return ToolOutput(data=result, text=text)

        return Tool(
            name=template.name,
            description=template.description,
            parameters=template._schema,
            execute=execute,
            suspending=template._suspending,
        )


def tool(
    name: str,
    description: str,
    *,
    renderer: Renderer[Any],
    suspending: bool = False,
) -> Any:  # noqa: ANN401
    """Decorator that creates a ``ToolTemplate`` from a typed async function.

    The decorated function's **first parameter** must be typed as a Pydantic
    ``BaseModel`` subclass (the validated input). Remaining ``**kwargs`` are
    the bound dependencies supplied via ``ToolTemplate.bind()``.

    Example::

        class ReadParams(BaseModel):
            path: str

        @tool("read", "Read a file.", renderer=TextRenderer())
        async def read_file(params: ReadParams, *, fs: FileSystem) -> str:
            return fs.read(params.path)

        # Later:
        t = read_file.bind(fs=real_fs)  # returns a Tool
    """

    # Capture the caller's local namespace so get_type_hints can resolve
    # forward references from `from __future__ import annotations` for
    # locally-defined classes (e.g., in test functions).
    caller_frame = sys._getframe(1)  # noqa: SLF001
    caller_localns: dict[str, Any] = caller_frame.f_locals.copy()
    caller_globalns: dict[str, Any] = caller_frame.f_globals

    def decorator(fn: Any) -> ToolTemplate[Any]:  # noqa: ANN401
        # Extract the Pydantic model from the first parameter's type annotation
        sig = inspect.signature(fn)
        params = list(sig.parameters.keys())
        if not params:
            msg = (
                f"@tool-decorated function {fn.__name__!r} must have at least "
                f"one parameter (the Pydantic params model)"
            )
            raise TypeError(msg)

        # Merge function globals with caller's locals for forward-ref resolution.
        merged_ns = {**caller_globalns, **caller_localns}
        hints = get_type_hints(fn, globalns=merged_ns, localns=caller_localns)
        first_param = params[0]

        if first_param not in hints:
            msg = (
                f"@tool-decorated function {fn.__name__!r}: first parameter "
                f"{first_param!r} must have a type annotation"
            )
            raise TypeError(msg)

        params_model = hints[first_param]

        if not (isinstance(params_model, type) and issubclass(params_model, BaseModel)):
            msg = (
                f"@tool-decorated function {fn.__name__!r}: first parameter "
                f"{first_param!r} must be typed as a Pydantic BaseModel subclass, "
                f"got {params_model!r}"
            )
            raise TypeError(msg)

        return ToolTemplate(
            name=name,
            description=description,
            fn=fn,
            params_model=params_model,
            renderer=renderer,
            suspending=suspending,
        )

    return decorator
