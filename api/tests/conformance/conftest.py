from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.canonical import tables as t
from app.canonical.models import Capabilities
from app.connectors import registry
from app.connectors.base import SourceAdapter
from app.connectors.secrets import SecretNotFound, resolve
from tests.conformance.targets import TARGETS, ConformanceTarget


@dataclass
class SyncedTenant:
    target: ConformanceTarget
    tenant: t.Tenant
    integration: t.Integration
    capabilities: Capabilities
    session: AsyncSession

    @property
    def source(self) -> str:
        return self.integration.source


@pytest.fixture(params=TARGETS, ids=lambda target: target.id)
def target(request: pytest.FixtureRequest) -> ConformanceTarget:
    return request.param


@pytest.fixture
async def synced(db: AsyncSession, target: ConformanceTarget) -> SyncedTenant:
    tenant = (
        await db.execute(select(t.Tenant).where(t.Tenant.slug == target.tenant_slug))
    ).scalar_one_or_none()
    if tenant is None:
        pytest.skip(f"tenant {target.tenant_slug!r} does not exist. {target.setup_hint}")

    # By source, not `.first()`: this tenant may run several registers and the
    # suite grades one adapter at a time.
    integration = (
        await db.execute(
            select(t.Integration).where(
                t.Integration.tenant_id == tenant.id, t.Integration.source == target.source
            )
        )
    ).scalar_one_or_none()
    if integration is None:
        pytest.skip(
            f"{target.tenant_slug!r} has no {target.source!r} integration. {target.setup_hint}"
        )

    orders = (
        await db.execute(
            select(func.count())
            .select_from(t.Order)
            .where(t.Order.tenant_id == tenant.id, t.Order.source == target.source)
        )
    ).scalar_one()
    if not orders:
        pytest.skip(
            f"{target.tenant_slug!r} has nothing synced from {target.source!r}. {target.setup_hint}"
        )

    return SyncedTenant(
        target=target,
        tenant=tenant,
        integration=integration,
        capabilities=Capabilities(**integration.capabilities),
        session=db,
    )


@pytest.fixture
async def adapter(synced: SyncedTenant) -> AsyncIterator[SourceAdapter]:
    registry.load_builtin_adapters()
    try:
        secret = resolve(synced.integration.secret_ref)
    except SecretNotFound as exc:
        pytest.skip(str(exc))
    built = registry.build(synced.integration.adapter, synced.integration.config, secret)
    try:
        health = await built.healthcheck()
        if not health.ok:
            pytest.skip(f"{synced.integration.adapter} is not reachable: {health.detail}")
        yield built
    finally:
        await built.aclose()
