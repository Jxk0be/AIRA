"""Merging the same person entered twice.

Shops create duplicates constantly: someone signs up at the register on a
Saturday and again online in March. The rows are *merged*, never deleted —
old orders point at whichever id the POS used that day, and those orders have
to keep resolving.

Matching happens on normalised values only. Comparing raw emails misses
`Avery.Bell@Gmail.com` against `avery.bell@gmail.com`; comparing raw phones
misses `(865) 555-0134` against `+18655550134`.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.canonical import tables as t
from app.canonical.identity import normalise_email, normalise_phone
from app.db import table_of

# Re-exported: this module is where callers have always imported them from,
# and they now live in the canonical layer so the AI-facing code can use them
# without importing an adapter module (CLAUDE.md rule 1).
__all__ = ["merge_duplicate_customers", "normalise_email", "normalise_phone"]


async def merge_duplicate_customers(
    session: AsyncSession, tenant_id: uuid.UUID, source: str
) -> int:
    """Point duplicates at a survivor. Returns how many rows were merged."""
    table = table_of(t.Customer)
    rows = (
        await session.execute(
            select(
                table.c.id,
                table.c.external_id,
                table.c.email_normalized,
                table.c.phone_normalized,
                table.c.source_created_at,
                table.c.merged_into_id,
            ).where(
                table.c.tenant_id == tenant_id,
                table.c.source == source,
                table.c.deleted_at.is_(None),
            )
        )
    ).all()

    groups: dict[tuple[str, str], list[Any]] = defaultdict(list)
    for row in rows:
        if row.email_normalized:
            groups[("email", row.email_normalized)].append(row)
        if row.phone_normalized:
            groups[("phone", row.phone_normalized)].append(row)

    # A customer can match on email in one group and phone in another; union
    # those so one person does not end up with two different survivors.
    parent: dict[uuid.UUID, uuid.UUID] = {}

    def find(node: uuid.UUID) -> uuid.UUID:
        while parent.get(node, node) != node:
            node = parent[node]
        return node

    by_id = {row.id: row for row in rows}

    def rank(row: Any) -> tuple[datetime, str]:
        # Oldest record wins; external_id breaks ties so the choice is stable
        # across runs rather than depending on row order.
        created = row.source_created_at or datetime.max.replace(tzinfo=UTC)
        return (created, row.external_id)

    for members in groups.values():
        if len(members) < 2:
            continue
        survivor = min(members, key=rank)
        for member in members:
            if member.id == survivor.id:
                continue
            a, b = find(member.id), find(survivor.id)
            if a == b:
                continue
            # Keep the better-ranked of the two roots as the new root.
            winner, loser = (a, b) if rank(by_id[a]) <= rank(by_id[b]) else (b, a)
            parent[loser] = winner

    merged = 0
    now = datetime.now(tz=UTC)
    for row in rows:
        survivor_id = find(row.id)
        target = None if survivor_id == row.id else survivor_id
        if row.merged_into_id == target:
            continue
        await session.execute(
            update(table).where(table.c.id == row.id).values(merged_into_id=target, updated_at=now)
        )
        if target is not None:
            merged += 1
    return merged
