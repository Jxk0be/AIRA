"""close the Data API on our tables

Our tables were readable by anybody holding the project's publishable key.

Supabase serves PostgREST over the `public` schema and, matching the cloud
default, grants `anon` and `authenticated` on every table `postgres` creates
there — which is every table Alembic creates. So
`GET /rest/v1/orders?select=*` with the publishable key returned other people's
orders, straight past FastAPI and past every `tenant_id` filter in
`app.analytics`. Verified against the local stack before this was written; it
returned rows for both fake shops.

That key is *designed* to be public. It is about to be embedded in the web app
so the browser can talk to Supabase Auth, at which point the hole ships in a
JavaScript bundle. Adding a front door (`app.accounts.guard`) while this stayed
open would have been theatre, so it is closed in the same change.

Two layers, because one of them is a configuration setting somebody can undo
from a dashboard:

1. **Row-level security on every table, with no policies at all.** Not the usual
   `auth.uid()` policies: nothing is supposed to reach these tables through
   PostgREST, so the correct policy set is the empty one. `anon` and
   `authenticated` carry no `bypassrls`, so they see zero rows and can write
   nothing. Our own connection is `postgres`, which does carry it, so FastAPI is
   unaffected — and the tenant isolation that actually matters is still rule 3,
   enforced in `app.analytics`. `FORCE ROW LEVEL SECURITY` is deliberately *not*
   set: it would apply RLS to the table owner too, which is us.
2. **The grants themselves are revoked**, and the default privileges that handed
   them out are changed, so a table added by a later migration is not exposed the
   moment it is created.

Tables are enumerated from the catalog rather than listed, so this covers
everything in `public` when it runs. A table created by a *later* migration gets
its grants withheld by step 2, but does not get RLS — `test_data_api_is_closed`
walks `app.tables.metadata` and fails if any table is missing it, so the gap
shows up as a failing test rather than as exposed data.

Revision ID: 9a4c7e1f5b20
Revises: 5db7501b8290
Create Date: 2026-09-25 18:40:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "9a4c7e1f5b20"
down_revision: str | None = "5db7501b8290"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The Data API's roles. `service_role` is left alone: it bypasses RLS by design,
# it is a secret that never reaches a browser, and Studio uses it.
API_ROLES = ("anon", "authenticated")

# Alembic's own bookkeeping. Not ours to secure, and not interesting.
SKIP = ("alembic_version",)


def _our_tables() -> list[str]:
    rows = op.get_bind().exec_driver_sql(
        "select tablename from pg_tables where schemaname = 'public' order by tablename"
    )
    return [name for (name,) in rows if name not in SKIP]


def upgrade() -> None:
    bind = op.get_bind()
    roles = ", ".join(API_ROLES)

    for table in _our_tables():
        # No policies follow. An RLS-enabled table with no policy denies every
        # row to every role that does not bypass RLS, which is the intent.
        bind.exec_driver_sql(f'alter table public."{table}" enable row level security')

    bind.exec_driver_sql(f"revoke all on all tables in schema public from {roles}")
    bind.exec_driver_sql(f"revoke all on all sequences in schema public from {roles}")
    # Named rather than `all routines`: pgvector lives in `public` on this
    # project, and its operator functions are not ours to revoke.
    bind.exec_driver_sql(f"revoke all on function public.hybrid_search from {roles}")

    # Alembic connects as `postgres`, so it is `postgres`'s default privileges
    # that decide what a table created by a later migration arrives with.
    # `functions` is on the list because a `SECURITY DEFINER` function in
    # `public` with EXECUTE granted to `anon` is a public API endpoint that
    # bypasses RLS — the one shape of this mistake that RLS does not catch.
    for privilege in ("tables", "sequences", "functions"):
        bind.exec_driver_sql(
            f"alter default privileges for role postgres in schema public "
            f"revoke all on {privilege} from {roles}"
        )


def downgrade() -> None:
    # Reversible, as a migration has to be — but note what reversing it means:
    # it puts every table back on the public internet for anyone holding the
    # publishable key. There is no reason to run this except to get back to a
    # known state before re-applying it.
    bind = op.get_bind()
    roles = ", ".join(API_ROLES)

    for privilege in ("tables", "sequences", "functions"):
        bind.exec_driver_sql(
            f"alter default privileges for role postgres in schema public "
            f"grant all on {privilege} to {roles}"
        )

    bind.exec_driver_sql(f"grant all on all tables in schema public to {roles}")
    bind.exec_driver_sql(f"grant all on all sequences in schema public to {roles}")
    bind.exec_driver_sql(f"grant execute on function public.hybrid_search to {roles}")

    for table in _our_tables():
        bind.exec_driver_sql(f'alter table public."{table}" disable row level security')
