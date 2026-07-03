from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from orxtra.protocols import TrustTier

if TYPE_CHECKING:
    import asyncpg


@dataclass(frozen=True)
class ConsumerRecord:
    id: UUID
    name: str
    trust_tier: TrustTier
    scope_grants: list[str]
    disabled_at: datetime | None
    created_at: datetime


@dataclass(frozen=True)
class CredentialRecord:
    id: UUID
    consumer_id: UUID
    credential_type: str
    credential_hash: str
    algorithm: str
    metadata: dict[str, object]
    created_at: datetime


class AuthBackend:
    """asyncpg-backed auth storage."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def create_consumer(
        self,
        pool: asyncpg.Pool,
        name: str,
        trust_tier: TrustTier,
        scope_grants: list[str],
    ) -> UUID:
        async with pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow(
                "INSERT INTO consumers (name, trust_tier, scope_grants)"
                " VALUES ($1, $2, $3)"
                " RETURNING id",
                name,
                trust_tier.value,
                json.dumps(scope_grants),
            )
        assert row is not None  # noqa: S101
        return row["id"]  # type: ignore[no-any-return]

    async def get_consumer(
        self,
        pool: asyncpg.Pool,
        consumer_id: UUID,
    ) -> ConsumerRecord | None:
        async with pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow(
                "SELECT id, name, trust_tier, scope_grants,"
                " disabled_at, created_at"
                " FROM consumers WHERE id = $1",
                consumer_id,
            )
        if row is None:
            return None
        return _row_to_consumer(row)

    async def disable_consumer(
        self,
        pool: asyncpg.Pool,
        consumer_id: UUID,
    ) -> None:
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "UPDATE consumers SET disabled_at = now()"
                " WHERE id = $1",
                consumer_id,
            )

    async def create_credential(
        self,
        pool: asyncpg.Pool,
        consumer_id: UUID,
        credential_type: str,
        raw_value: str,
    ) -> UUID:
        credential_hash = hashlib.sha256(raw_value.encode()).hexdigest()
        async with pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow(
                "INSERT INTO credentials"
                " (consumer_id, credential_type, credential_hash)"
                " VALUES ($1, $2, $3)"
                " RETURNING id",
                consumer_id,
                credential_type,
                credential_hash,
            )
        assert row is not None  # noqa: S101
        return row["id"]  # type: ignore[no-any-return]

    async def get_credential_by_hash(
        self,
        pool: asyncpg.Pool,
        credential_hash: str,
    ) -> CredentialRecord | None:
        async with pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow(
                "SELECT id, consumer_id, credential_type,"
                " credential_hash, algorithm, metadata, created_at"
                " FROM credentials WHERE credential_hash = $1",
                credential_hash,
            )
        if row is None:
            return None
        return _row_to_credential(row)


def _row_to_consumer(row: asyncpg.Record) -> ConsumerRecord:
    scope_grants = row["scope_grants"]
    if isinstance(scope_grants, str):
        scope_grants = json.loads(scope_grants)
    return ConsumerRecord(
        id=row["id"],
        name=row["name"],
        trust_tier=TrustTier(row["trust_tier"]),
        scope_grants=scope_grants,
        disabled_at=row["disabled_at"],
        created_at=row["created_at"],
    )


def _row_to_credential(row: asyncpg.Record) -> CredentialRecord:
    metadata = row["metadata"]
    if isinstance(metadata, str):
        metadata = json.loads(metadata)
    return CredentialRecord(
        id=row["id"],
        consumer_id=row["consumer_id"],
        credential_type=row["credential_type"],
        credential_hash=row["credential_hash"],
        algorithm=row["algorithm"],
        metadata=metadata,
        created_at=row["created_at"],
    )
