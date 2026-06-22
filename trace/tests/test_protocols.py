"""Tests for storage protocols and PgBackend conformance."""

from __future__ import annotations

import inspect
from typing import TYPE_CHECKING, get_type_hints

import pytest
from orxtra.trace._pg_backend import PgBackend
from orxtra.trace._pg_event_bus import PgEventBus
from orxtra.trace._protocols import (
    EventBus,
    EventStorage,
    InboxStorage,
    NotepadStorage,
    OverseerStorage,
    RecoveryOperations,
    RunControlStorage,
    RunStorage,
    StorageBackend,
    StorageLock,
    StorageReader,
    TaskStorage,
)

if TYPE_CHECKING:
    from .conftest import MockPool


ALL_SUB_PROTOCOLS = [
    TaskStorage,
    EventStorage,
    RunStorage,
    RunControlStorage,
    OverseerStorage,
    InboxStorage,
    NotepadStorage,
    StorageReader,
    StorageLock,
    RecoveryOperations,
]


def _protocol_methods(protocol: type) -> set[str]:
    """Extract method names defined directly on a protocol (excluding inherited)."""
    methods: set[str] = set()
    # Get methods from annotations and actual definitions
    for name, value in inspect.getmembers(protocol):
        if name.startswith("_"):
            continue
        if callable(value) or isinstance(value, (classmethod, staticmethod)):
            # Check if this method is defined on the protocol itself,
            # not just inherited from Protocol
            for klass in protocol.__mro__:
                if klass is object:
                    continue
                if name in klass.__dict__:
                    if klass is protocol or (
                        hasattr(klass, "__protocol_attrs__")
                        and klass is not type(None)
                    ):
                        methods.add(name)
                    break
    return methods


def _get_protocol_method_names(protocol: type) -> set[str]:
    """Get all method names declared in a Protocol class (excluding dunder)."""
    names: set[str] = set()
    # Walk MRO to collect methods from all parent protocols
    for klass in protocol.__mro__:
        if klass is object:
            continue
        for name in klass.__dict__:
            if name.startswith("_"):
                continue
            obj = klass.__dict__[name]
            if callable(obj) or isinstance(obj, (classmethod, staticmethod)):
                names.add(name)
    return names


class TestPgBackendHasAllMethods:
    """Verify PgBackend has every method from every sub-protocol."""

    @pytest.mark.parametrize("protocol", ALL_SUB_PROTOCOLS, ids=lambda p: p.__name__)
    def test_sub_protocol_methods_present(self, protocol: type) -> None:
        protocol_methods = _get_protocol_method_names(protocol)
        backend_methods = {
            name for name in dir(PgBackend) if not name.startswith("_")
        }
        missing = protocol_methods - backend_methods
        assert not missing, (
            f"PgBackend is missing methods from {protocol.__name__}: {sorted(missing)}"
        )

    def test_combined_storage_backend_methods(self) -> None:
        """All StorageBackend methods are on PgBackend."""
        protocol_methods = _get_protocol_method_names(StorageBackend)
        backend_methods = {
            name for name in dir(PgBackend) if not name.startswith("_")
        }
        missing = protocol_methods - backend_methods
        assert not missing, (
            f"PgBackend is missing StorageBackend methods: {sorted(missing)}"
        )


class TestPgEventBusHasAllMethods:
    """Verify PgEventBus has every method from EventBus."""

    def test_event_bus_methods_present(self) -> None:
        protocol_methods = _get_protocol_method_names(EventBus)
        bus_methods = {
            name for name in dir(PgEventBus) if not name.startswith("_")
        }
        missing = protocol_methods - bus_methods
        assert not missing, (
            f"PgEventBus is missing EventBus methods: {sorted(missing)}"
        )


class TestProtocolMethodSignatures:
    """Verify protocol method signatures match PgBackend signatures."""

    @pytest.mark.parametrize("protocol", ALL_SUB_PROTOCOLS, ids=lambda p: p.__name__)
    def test_signature_parameters_match(self, protocol: type) -> None:
        protocol_methods = _get_protocol_method_names(protocol)
        mismatches: list[str] = []
        for method_name in sorted(protocol_methods):
            proto_method = getattr(protocol, method_name, None)
            backend_method = getattr(PgBackend, method_name, None)
            if proto_method is None or backend_method is None:
                continue
            proto_sig = inspect.signature(proto_method)
            backend_sig = inspect.signature(backend_method)
            # Compare parameter names (excluding 'self')
            proto_params = [
                p for p in proto_sig.parameters if p != "self"
            ]
            backend_params = [
                p for p in backend_sig.parameters if p != "self"
            ]
            if proto_params != backend_params:
                mismatches.append(
                    f"{method_name}: protocol={proto_params}, "
                    f"backend={backend_params}"
                )
        assert not mismatches, (
            f"Signature mismatches in {protocol.__name__}:\n"
            + "\n".join(mismatches)
        )


class TestRuntimeCheckable:
    """Verify runtime isinstance checks work for PgBackend."""

    def test_pg_backend_is_task_storage(self, mock_pool: MockPool) -> None:
        backend = PgBackend(mock_pool)  # type: ignore[arg-type]
        assert isinstance(backend, TaskStorage)

    def test_pg_backend_is_event_storage(self, mock_pool: MockPool) -> None:
        backend = PgBackend(mock_pool)  # type: ignore[arg-type]
        assert isinstance(backend, EventStorage)

    def test_pg_backend_is_run_storage(self, mock_pool: MockPool) -> None:
        backend = PgBackend(mock_pool)  # type: ignore[arg-type]
        assert isinstance(backend, RunStorage)

    def test_pg_backend_is_run_control_storage(self, mock_pool: MockPool) -> None:
        backend = PgBackend(mock_pool)  # type: ignore[arg-type]
        assert isinstance(backend, RunControlStorage)

    def test_pg_backend_is_overseer_storage(self, mock_pool: MockPool) -> None:
        backend = PgBackend(mock_pool)  # type: ignore[arg-type]
        assert isinstance(backend, OverseerStorage)

    def test_pg_backend_is_inbox_storage(self, mock_pool: MockPool) -> None:
        backend = PgBackend(mock_pool)  # type: ignore[arg-type]
        assert isinstance(backend, InboxStorage)

    def test_pg_backend_is_notepad_storage(self, mock_pool: MockPool) -> None:
        backend = PgBackend(mock_pool)  # type: ignore[arg-type]
        assert isinstance(backend, NotepadStorage)

    def test_pg_backend_is_storage_reader(self, mock_pool: MockPool) -> None:
        backend = PgBackend(mock_pool)  # type: ignore[arg-type]
        assert isinstance(backend, StorageReader)

    def test_pg_backend_is_storage_lock(self, mock_pool: MockPool) -> None:
        backend = PgBackend(mock_pool)  # type: ignore[arg-type]
        assert isinstance(backend, StorageLock)

    def test_pg_backend_is_recovery_operations(self, mock_pool: MockPool) -> None:
        backend = PgBackend(mock_pool)  # type: ignore[arg-type]
        assert isinstance(backend, RecoveryOperations)

    def test_pg_backend_is_storage_backend(self, mock_pool: MockPool) -> None:
        backend = PgBackend(mock_pool)  # type: ignore[arg-type]
        assert isinstance(backend, StorageBackend)

    def test_pg_event_bus_is_event_bus(self, mock_pool: MockPool) -> None:
        bus = PgEventBus(mock_pool)  # type: ignore[arg-type]
        assert isinstance(bus, EventBus)


class TestProtocolMethodCounts:
    """Verify expected method counts per protocol as documentation."""

    def test_task_storage_count(self) -> None:
        methods = _get_protocol_method_names(TaskStorage)
        assert len(methods) == 8, f"TaskStorage methods: {sorted(methods)}"

    def test_event_storage_count(self) -> None:
        methods = _get_protocol_method_names(EventStorage)
        assert len(methods) == 2, f"EventStorage methods: {sorted(methods)}"

    def test_run_storage_count(self) -> None:
        methods = _get_protocol_method_names(RunStorage)
        assert len(methods) == 3, f"RunStorage methods: {sorted(methods)}"

    def test_run_control_storage_count(self) -> None:
        methods = _get_protocol_method_names(RunControlStorage)
        assert len(methods) == 2, f"RunControlStorage methods: {sorted(methods)}"

    def test_overseer_storage_count(self) -> None:
        methods = _get_protocol_method_names(OverseerStorage)
        assert len(methods) == 6, f"OverseerStorage methods: {sorted(methods)}"

    def test_inbox_storage_count(self) -> None:
        methods = _get_protocol_method_names(InboxStorage)
        assert len(methods) == 5, f"InboxStorage methods: {sorted(methods)}"

    def test_notepad_storage_count(self) -> None:
        methods = _get_protocol_method_names(NotepadStorage)
        assert len(methods) == 1, f"NotepadStorage methods: {sorted(methods)}"

    def test_storage_reader_count(self) -> None:
        methods = _get_protocol_method_names(StorageReader)
        assert len(methods) == 23, f"StorageReader methods: {sorted(methods)}"

    def test_storage_lock_count(self) -> None:
        methods = _get_protocol_method_names(StorageLock)
        assert len(methods) == 4, f"StorageLock methods: {sorted(methods)}"

    def test_recovery_operations_count(self) -> None:
        methods = _get_protocol_method_names(RecoveryOperations)
        assert len(methods) == 3, f"RecoveryOperations methods: {sorted(methods)}"

    def test_event_bus_count(self) -> None:
        methods = _get_protocol_method_names(EventBus)
        assert len(methods) == 2, f"EventBus methods: {sorted(methods)}"

    def test_total_storage_backend_count(self) -> None:
        """StorageBackend combines all sub-protocols."""
        methods = _get_protocol_method_names(StorageBackend)
        # Sum of all sub-protocol methods (no overlap expected)
        expected = 8 + 2 + 3 + 2 + 6 + 5 + 1 + 23 + 4 + 3  # = 57
        assert len(methods) == expected, (
            f"StorageBackend has {len(methods)} methods, expected {expected}. "
            f"Methods: {sorted(methods)}"
        )
