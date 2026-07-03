from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING

from orxtra.protocols import ConsumerRecord, CredentialRecord, TrustTier

if TYPE_CHECKING:
    from uuid import UUID

    import asyncpg


class AuthBackend:
    """asyncpg-backed auth storage.

    The pool is stored at construction time and used internally --
    callers never pass it per-call. This fixes the previous design
    where middleware/Authenticator had no pool to pass.
    """

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def create_consumer(
        self,
        name: str,
        trust_tier: TrustTier,
        scope_grants: list[str],
    ) -> UUID:
        async with self._pool.acquire() as conn, conn.transaction():
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
        consumer_id: UUID,
    ) -> ConsumerRecord | None:
        async with self._pool.acquire() as conn, conn.transaction():
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
        consumer_id: UUID,
    ) -> None:
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "UPDATE consumers SET disabled_at = now()"
                " WHERE id = $1",
                consumer_id,
            )

    async def create_credential(
        self,
        consumer_id: UUID,
        credential_type: str,
        raw_value: str,
        *,
        secret_ref: str | None = None,
    ) -> UUID:
        credential_hash = hashlib.sha256(raw_value.encode()).hexdigest()
        async with self._pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow(
                "INSERT INTO credentials"
                " (consumer_id, credential_type, credential_hash,"
                "  secret_ref)"
                " VALUES ($1, $2, $3, $4)"
                " RETURNING id",
                consumer_id,
                credential_type,
                credential_hash,
                secret_ref,
            )
        assert row is not None  # noqa: S101
        return row["id"]  # type: ignore[no-any-return]

    async def get_credential_by_id(
        self,
        credential_id: UUID,
    ) -> CredentialRecord | None:
        async with self._pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow(
                "SELECT id, consumer_id, credential_type,"
                " credential_hash, algorithm, metadata,"
                " secret_ref, created_at"
                " FROM credentials WHERE id = $1",
                credential_id,
            )
        if row is None:
            return None
        return _row_to_credential(row)

    async def get_credential_by_hash(
        self,
        credential_hash: str,
    ) -> CredentialRecord | None:
        async with self._pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow(
                "SELECT id, consumer_id, credential_type,"
                " credential_hash, algorithm, metadata,"
                " secret_ref, created_at"
                " FROM credentials WHERE credential_hash = $1",
                credential_hash,
            )
        if row is None:
            return None
        return _row_to_credential(row)

    async def get_credentials_by_consumer(
        self,
        consumer_id: UUID,
        *,
        credential_type: str | None = None,
    ) -> list[CredentialRecord]:
        if credential_type is not None:
            query = (
                "SELECT id, consumer_id, credential_type,"
                " credential_hash, algorithm, metadata,"
                " secret_ref, created_at"
                " FROM credentials"
                " WHERE consumer_id = $1 AND credential_type = $2"
            )
            args: tuple[object, ...] = (consumer_id, credential_type)
        else:
            query = (
                "SELECT id, consumer_id, credential_type,"
                " credential_hash, algorithm, metadata,"
                " secret_ref, created_at"
                " FROM credentials WHERE consumer_id = $1"
            )
            args = (consumer_id,)
        async with self._pool.acquire() as conn, conn.transaction():
            rows = await conn.fetch(query, *args)
        return [_row_to_credential(row) for row in rows]


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
        secret_ref=row["secret_ref"],
        created_at=row["created_at"],
    )
