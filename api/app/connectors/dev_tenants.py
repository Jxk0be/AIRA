"""Development tenants, wired to the fake customer systems in `sources/`.

Strictly a convenience for local work. Phase 11's `python -m app.onboard`
replaces this with a real create-tenant / connect flow; until then this is what
gives `python -m app.sync --tenant animanga_knox` something to sync.

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
from app.notify import upsert_recipient


@dataclass(frozen=True)
class DevSource:
    """One register a dev tenant runs.

    A tenant holds a list of these rather than one set of adapter fields,
    because a shop with a till on the floor and a marketplace online is the case
    this product exists for, and a fixture layer that cannot express it is a
    fixture layer that quietly guarantees nobody tests it.
    """

    adapter: str
    source: str
    # What a screen calls this register. Stored on the integration row so that
    # `app.analytics` can label a consolidated figure without asking
    # `app.connectors` anything (CLAUDE.md rule 1).
    display_name: str
    config: dict[str, Any] = field(default_factory=dict)
    secret_ref: str | None = None


@dataclass(frozen=True)
class DevTenant:
    slug: str
    name: str
    timezone: str
    currency: str
    # In the order they were connected, which is the order screens list them in.
    sources: tuple[DevSource, ...]
    settings: dict[str, Any] = field(default_factory=dict)
    # Who the worker addresses the Monday digest to. Without one the digest job
    # runs and then skips with "nobody at this shop has asked for the digest",
    # which looks exactly like a broken worker. `.example` is reserved by RFC
    # 2606 and cannot be delivered to, so a dev tenant switched to a live
    # transport by accident still cannot reach a real person.
    digest_to: str | None = None


DEV_TENANTS: dict[str, DevTenant] = {
    "animanga_knox": DevTenant(
        slug="animanga_knox",
        name="Animanga Knox",
        timezone="America/New_York",
        currency="USD",
        sources=(
            DevSource(
                adapter="registerone",
                source="registerone",
                display_name="RegisterOne (counter and booth)",
                config={
                    "base_url": "http://localhost:8100",
                    "event_location_ids": ["LOC_CON"],
                },
                secret_ref="env:REGISTERONE_TOKEN",
            ),
            # The shop's second register, connected in July 2026: a TCG
            # marketplace with no API, which hands them a spreadsheet a month.
            # This is what makes Animanga Knox a two-source shop by default, and
            # it is the whole demo — neither Square nor Shopify will ever add a
            # competitor's takings to their own.
            #
            # Same adapter as Panel & Pawn. What makes it CardNexus is the mapping
            # file, not any code.
            DevSource(
                adapter="mapping",
                source="animanga_knox_online",
                display_name="CardNexus (online storefront)",
                config={"mapping_file": "mappings/animanga_knox_online.yaml"},
            ),
        ),
        settings={"low_stock_threshold": 3, "dead_stock_days": 90},
        digest_to="owner@animanga_knox.example",
    ),
    "panel_and_pawn": DevTenant(
        slug="panel_and_pawn",
        name="Panel & Pawn",
        timezone="America/New_York",
        currency="USD",
        sources=(
            # Same adapter every table-shaped customer uses. What makes this one
            # Panel & Pawn is the mapping file, not any code.
            DevSource(
                adapter="mapping",
                source="panel_and_pawn",
                display_name="Register export (spreadsheet)",
                config={"mapping_file": "mappings/panel_and_pawn.yaml"},
            ),
        ),
        settings={"low_stock_threshold": 2, "dead_stock_days": 120},
        digest_to="owner@panelandpawn.example",
    ),
}


def _capabilities_of(spec: DevSource) -> Any:
    """Ask the adapter what it can do. Never written by hand."""
    if spec.adapter == "mapping":
        # This adapter has no fixed capabilities: they are whatever the
        # customer's mapping file declares, so it has to be built to be asked.
        return registry.build(spec.adapter, spec.config).describe().capabilities
    return registry.describe(spec.adapter).capabilities


async def ensure_dev_tenant(
    session: AsyncSession, slug: str
) -> tuple[t.Tenant, list[t.Integration]]:
    """Create the tenant and every one of its integrations if they are not there.

    Returns all of them, in the order the spec lists them. A caller that syncs
    only the first is a caller that silently ignores a shop's second register —
    which is the bug this signature exists to make hard to write.
    """
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

    integrations: list[t.Integration] = []
    for source in spec.sources:
        capabilities = _capabilities_of(source)
        integration = (
            await session.execute(
                select(t.Integration).where(
                    t.Integration.tenant_id == tenant.id, t.Integration.source == source.source
                )
            )
        ).scalar_one_or_none()
        if integration is None:
            integration = t.Integration(
                id=uuid.uuid4(),
                tenant_id=tenant.id,
                adapter=source.adapter,
                source=source.source,
                display_name=source.display_name,
                config=source.config,
                secret_ref=source.secret_ref,
                capabilities=capabilities.model_dump(),
            )
            session.add(integration)
        else:
            # The adapter is the authority on what it can do; a stale row is not.
            integration.capabilities = capabilities.model_dump()
            integration.config = source.config
            integration.secret_ref = source.secret_ref
            integration.display_name = source.display_name
        integrations.append(integration)

    if spec.digest_to:
        await upsert_recipient(
            session,
            tenant.id,
            email=spec.digest_to,
            name=f"{spec.name} (owner)",
        )

    await session.commit()
    await session.refresh(tenant)
    for integration in integrations:
        await session.refresh(integration)
    return tenant, integrations
