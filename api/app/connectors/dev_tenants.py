"""Development tenants, wired to the fake customer systems in `sources/`.

Strictly a convenience for local work. Phase 11's `python -m app.onboard`
replaces this with a real create-tenant / connect flow; until then this is what
gives `python -m app.sync --tenant tsundoku` something to sync.

Capabilities are never written by hand here: they come from the adapter's own
`describe()`, so a tenant cannot claim something its adapter cannot do.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.canonical import tables as t
from app.connectors import registry


@dataclass(frozen=True)
class DevTenant:
    slug: str
    name: str
    timezone: str
    currency: str
    adapter: str
    source: str
    config: dict[str, Any] = field(default_factory=dict)
    secret_ref: str | None = None
    settings: dict[str, Any] = field(default_factory=dict)


DEV_TENANTS: dict[str, DevTenant] = {
    "tsundoku": DevTenant(
        slug="tsundoku",
        name="Tsundoku & Tabletop",
        timezone="America/New_York",
        currency="USD",
        adapter="registerone",
        source="registerone",
        config={
            "base_url": "http://localhost:8100",
            "event_location_ids": ["LOC_CON"],
        },
        secret_ref="env:REGISTERONE_TOKEN",
        settings={"low_stock_threshold": 3, "dead_stock_days": 90},
    ),
    "panel_and_pawn": DevTenant(
        slug="panel_and_pawn",
        name="Panel & Pawn",
        timezone="America/New_York",
        currency="USD",
        # Same adapter every table-shaped customer uses. What makes this one
        # Panel & Pawn is the mapping file, not any code.
        adapter="mapping",
        source="panel_and_pawn",
        config={"mapping_file": "mappings/panel_and_pawn.yaml"},
        secret_ref=None,
        settings={"low_stock_threshold": 2, "dead_stock_days": 120},
    ),
}


async def ensure_dev_tenant(session: AsyncSession, slug: str) -> tuple[t.Tenant, t.Integration]:
    """Create the tenant and its integration if they are not there yet."""
    try:
        spec = DEV_TENANTS[slug]
    except KeyError:
        known = ", ".join(sorted(DEV_TENANTS))
        raise KeyError(f"no dev tenant {slug!r}. Known: {known}") from None

    tenant = (
        await session.execute(select(t.Tenant).where(t.Tenant.slug == spec.slug))
    ).scalar_one_or_none()
    if tenant is None:
        tenant = t.Tenant(
            id=uuid.uuid4(),
            slug=spec.slug,
            name=spec.name,
            timezone=spec.timezone,
            currency=spec.currency,
            settings=spec.settings,
        )
        session.add(tenant)
        await session.flush()

    registry.load_builtin_adapters()
    if spec.adapter == "mapping":
        # This adapter has no fixed capabilities: they are whatever the
        # customer's mapping file declares, so it has to be built to be asked.
        capabilities = registry.build(spec.adapter, spec.config).describe().capabilities
    else:
        capabilities = registry.describe(spec.adapter).capabilities

    integration = (
        await session.execute(
            select(t.Integration).where(
                t.Integration.tenant_id == tenant.id, t.Integration.source == spec.source
            )
        )
    ).scalar_one_or_none()
    if integration is None:
        integration = t.Integration(
            id=uuid.uuid4(),
            tenant_id=tenant.id,
            adapter=spec.adapter,
            source=spec.source,
            config=spec.config,
            secret_ref=spec.secret_ref,
            capabilities=capabilities.model_dump(),
        )
        session.add(integration)
    else:
        # The adapter is the authority on what it can do; a stale row is not.
        integration.capabilities = capabilities.model_dump()
        integration.config = spec.config
        integration.secret_ref = spec.secret_ref

    await session.commit()
    await session.refresh(tenant)
    await session.refresh(integration)
    return tenant, integration
