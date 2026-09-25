"""Our tables must not be reachable through Supabase's Data API.

Before the `close_the_data_api` migration they were: PostgREST serves the
`public` schema, Supabase grants `anon` and `authenticated` on every table
`postgres` creates there, and so `GET /rest/v1/orders` with the project's
*publishable* key returned other shops' orders — past FastAPI, past every
`tenant_id` filter in `app.analytics`. That key is meant to be public and is
about to ship inside the web app's JavaScript.

These checks are the reason that migration does not have to be remembered. The
first one is the important one: it walks the tables we actually declare, so a
table added next month fails here rather than quietly appearing on the internet.
The fix when it fails is one `alter table ... enable row level security` in a new
migration.

The checks read the catalog rather than making HTTP calls, so they need the
database but not the whole stack, and they do not need the publishable key.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.tables import metadata

# The roles PostgREST authenticates as. `service_role` is deliberately not here:
# it bypasses RLS by design, it is a secret that never reaches a browser, and
# Studio needs it.
API_ROLES = ("anon", "authenticated")

# Alembic's own bookkeeping. Not ours, and it holds one version string.
NOT_OURS = {"alembic_version"}


async def test_every_table_we_own_has_row_level_security(db: AsyncSession) -> None:
    """With no policies, RLS denies every row to every role that cannot bypass
    it. That is the intended policy set: nothing should reach these tables
    through PostgREST at all."""
    enabled = dict(
        (
            await db.execute(
                text(
                    "select c.relname, c.relrowsecurity from pg_class c "
                    "join pg_namespace n on n.oid = c.relnamespace "
                    "where n.nspname = 'public' and c.relkind = 'r'"
                )
            )
        ).all()
    )

    ours = sorted(set(metadata.tables) - NOT_OURS)
    assert ours, "no tables found; app.tables did not import them"

    missing = [name for name in ours if not enabled.get(name)]
    assert not missing, (
        f"row-level security is off on {missing}. Anything in `public` is served by "
        "PostgREST to anyone holding the publishable key. Add "
        "`alter table ... enable row level security` in a migration."
    )


async def test_no_table_we_own_has_a_policy_that_lets_the_api_in(db: AsyncSession) -> None:
    """RLS enabled but with a permissive policy would be worse than no RLS,
    because it would look protected."""
    rows = (
        await db.execute(
            text(
                "select tablename, policyname, roles::text from pg_policies "
                "where schemaname = 'public'"
            )
        )
    ).all()
    offending = [
        (table, policy, roles)
        for table, policy, roles in rows
        if any(role in roles for role in (*API_ROLES, "public"))
    ]
    assert not offending, f"these policies expose tables to the Data API: {offending}"


async def test_the_data_api_roles_hold_no_grants(db: AsyncSession) -> None:
    """The outer layer. RLS is the guarantee; revoked grants are what makes the
    refusal happen before a row is ever considered."""
    rows = (
        await db.execute(
            text(
                "select table_name, grantee, privilege_type "
                "from information_schema.role_table_grants "
                "where table_schema = 'public' and grantee = any(:roles)"
            ),
            {"roles": list(API_ROLES)},
        )
    ).all()
    granted = sorted({(table, grantee) for table, grantee, _ in rows if table not in NOT_OURS})
    assert not granted, f"the Data API roles can still reach {granted}"


async def test_a_new_table_would_not_be_exposed(db: AsyncSession) -> None:
    """Default privileges are what handed the grants out in the first place.

    Checked for role `postgres` specifically, because default privileges are keyed
    on the role that creates the object and Alembic connects as `postgres`. A
    `supabase_admin` entry granting `anon` still exists and is correct — it
    applies to tables Supabase itself creates, not to ours.

    Functions are included because a `SECURITY DEFINER` function in `public` with
    EXECUTE granted to `anon` is a public endpoint that bypasses RLS, which is the
    one version of this mistake the checks above would not catch.
    """
    acls = dict(
        (
            await db.execute(
                text(
                    "select d.defaclobjtype, d.defaclacl::text from pg_default_acl d "
                    "join pg_namespace n on n.oid = d.defaclnamespace "
                    "join pg_roles r on r.oid = d.defaclrole "
                    "where n.nspname = 'public' and r.rolname = 'postgres'"
                )
            )
        ).all()
    )
    kinds = {"r": "tables", "S": "sequences", "f": "functions"}
    for kind, what in kinds.items():
        acl = acls.get(kind) or acls.get(kind.encode()) or ""
        for role in API_ROLES:
            assert f"{role}=" not in acl, (
                f"new {what} in `public` created by postgres would still be granted to "
                f"{role}. Check `auto_expose_new_tables` in supabase/config.toml and the "
                "close_the_data_api migration."
            )


@pytest.mark.parametrize("role", API_ROLES)
async def test_the_api_roles_read_nothing_from_a_table_with_shop_data(
    db: AsyncSession, role: str
) -> None:
    """The end-to-end version of the above, at the only layer that matters.

    Run inside a savepoint, and rolled back explicitly: being refused aborts the
    surrounding transaction, so the savepoint has to be released by a rollback
    rather than by leaving the block normally.
    """
    savepoint = await db.begin_nested()
    try:
        await db.execute(text(f"set local role {role}"))
        count = (await db.execute(text("select count(*) from public.orders"))).scalar_one()
    except Exception as exc:
        await savepoint.rollback()
        assert "permission denied" in str(exc), exc
    else:
        await savepoint.rollback()
        assert count == 0, f"{role} can read {count} orders through the Data API"
