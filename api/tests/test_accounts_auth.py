"""The access checks, from the outside.

Four things are checked here, and the first is the one that keeps the others
honest over time.

1. **Every route is private.** Not "every route we remembered to protect" — the
   test enumerates the app's own routing table and asserts that each path either
   refuses an unauthenticated caller or is on `guard.PUBLIC_ROUTES`. A route
   added next month is covered without anybody editing this file.
2. **A token is not enough.** Signing up gets you an account and no shops. A
   member of one shop gets 404, not 403, from another — the same answer a shop
   that does not exist gives, so the difference cannot be used to find out which
   slugs are taken.
3. **The signature checks are real.** Wrong key, wrong issuer, wrong audience,
   expired, HS256 instead of ES256. The last one matters most: a verifier that
   accepts HMAC can be fed a token signed with its own public key.
4. **Roles gate writes**, and a shop can never be left with no owner.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.routing import APIRoute

from app.accounts.guard import PUBLIC_ROUTES, TENANT_PATH_PARAMS
from app.accounts.roles import MemberRole
from app.accounts.tokens import check_auth, expected_issuer
from app.config import get_settings
from app.main import app
from tests.conftest import Account, AccountFactory, Signer

POS_SHOP = "animanga_knox"
SPREADSHEET_SHOP = "panel_and_pawn"


def _probes() -> list[tuple[str, APIRoute]]:
    """Every (method, route) pair that is supposed to need a token.

    Each route is probed with its own method, including the writes — a POST or
    DELETE that answered without a token is the worst version of this bug, so
    they are exactly the ones not to skip. Nothing happens if a probe is refused,
    and the guard runs before the body is parsed or any path parameter is looked
    up, so an empty request against a made-up id is safe.
    """
    return [
        (method, route)
        for route in _api_routes()
        if route.path not in PUBLIC_ROUTES
        for method in sorted(route.methods)
        if method not in {"HEAD", "OPTIONS"}
    ]


def _api_routes() -> list[APIRoute]:
    """Every route the app serves.

    FastAPI 0.141 keeps included routers as their own objects rather than
    flattening them into `app.routes`, so this walks into them. Getting this
    wrong would silently shrink the test to two routes, hence the count
    assertion in `test_the_route_table_is_fully_walked`.
    """

    def walk(routes: list[Any]) -> list[APIRoute]:
        found: list[APIRoute] = []
        for route in routes:
            if isinstance(route, APIRoute):
                found.append(route)
            original = getattr(route, "original_router", None)
            if original is not None:
                found.extend(walk(list(original.routes)))
        return found

    return walk(list(app.routes))


def _example_url(route: APIRoute) -> str:
    """A concrete URL for a path template, with plausible junk in the holes.

    The values never need to exist: the point is to be refused before anything
    looks them up.
    """
    path = route.path
    for name in route.param_convertors:
        if name in TENANT_PATH_PARAMS:
            value = "no-such-shop"
        else:
            value = str(uuid.uuid4())
        path = path.replace(f"{{{name}}}", value)
    return path


def test_the_route_table_is_fully_walked() -> None:
    """Guard against the enumeration above quietly finding nothing.

    If FastAPI changes how included routers are stored, `_api_routes` could
    return two routes and every test below would pass while checking almost
    nothing.
    """
    routes = _api_routes()
    assert len(routes) > 40, f"only found {len(routes)} routes; the walk is broken"
    paths = {route.path for route in routes}
    assert "/tenants/{tenant}/reorder" in paths
    assert "/tenants/{slug}/dashboard" in paths


@pytest.mark.parametrize("method,route", _probes(), ids=lambda item: str(item))
async def test_every_route_refuses_an_unauthenticated_caller(
    method: str, route: APIRoute, anon_client: httpx.AsyncClient
) -> None:
    response = await anon_client.request(method, _example_url(route))
    assert response.status_code == 401, (
        f"{method} {route.path} answered {response.status_code} with no token. "
        "If it is meant to be public, add it to guard.PUBLIC_ROUTES and say why."
    )
    assert response.headers.get("WWW-Authenticate") == "Bearer"


@pytest.mark.parametrize("path", sorted(PUBLIC_ROUTES))
async def test_the_public_routes_stay_public(path: str, anon_client: httpx.AsyncClient) -> None:
    response = await anon_client.get(path.replace("{token}", "not-a-real-token"))
    assert response.status_code != 401, path


async def test_a_brand_new_account_can_see_itself_and_no_shops(
    account: AccountFactory, anon_client: httpx.AsyncClient
) -> None:
    """Signing up is not a grant. This is the "ask your owner" screen's data."""
    nobody: Account = await account(tenants=[])

    response = await anon_client.get("/me", headers=nobody.headers)
    assert response.status_code == 200
    body = response.json()
    assert body["email"] == nobody.email
    assert body["shops"] == []

    listed = await anon_client.get("/tenants", headers=nobody.headers)
    assert listed.status_code == 200
    assert listed.json() == []


async def test_another_shop_looks_exactly_like_a_shop_that_does_not_exist(
    account: AccountFactory, anon_client: httpx.AsyncClient
) -> None:
    """404 and not 403, so the response cannot be used to enumerate slugs."""
    outsider: Account = await account(tenants=[POS_SHOP])

    theirs = await anon_client.get(
        f"/tenants/{SPREADSHEET_SHOP}/dashboard", headers=outsider.headers
    )
    imaginary = await anon_client.get(
        "/tenants/definitely-not-a-shop/dashboard", headers=outsider.headers
    )

    assert theirs.status_code == 404
    assert imaginary.status_code == 404
    assert theirs.json()["detail"].replace(SPREADSHEET_SHOP, "X") == imaginary.json()[
        "detail"
    ].replace("definitely-not-a-shop", "X")


async def test_a_member_of_one_shop_is_not_listed_the_other(
    account: AccountFactory, anon_client: httpx.AsyncClient
) -> None:
    one: Account = await account(tenants=[POS_SHOP])
    listed = (await anon_client.get("/tenants", headers=one.headers)).json()
    assert [row["tenant"] for row in listed] == [POS_SHOP]


# --------------------------------------------------------------------------
# The signature checks
# --------------------------------------------------------------------------


async def test_a_token_signed_by_somebody_else_is_refused(
    signer: Signer, owner: Account, anon_client: httpx.AsyncClient
) -> None:
    """Right shape, right claims, wrong key."""
    impostor = ec.generate_private_key(ec.SECP256R1())
    token = signer.token(subject=owner.auth_user_id, email=owner.email, key=impostor)
    response = await anon_client.get("/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


async def test_an_hs256_token_is_refused_outright(
    signer: Signer, owner: Account, anon_client: httpx.AsyncClient
) -> None:
    """The algorithm-confusion case.

    A JWKS key is public. If HS256 were accepted, anyone could take that public
    key, use its bytes as an HMAC secret, and mint a token for any user they
    liked. The refusal is on the algorithm, before any key is chosen.
    """
    token = signer.token(
        subject=owner.auth_user_id,
        email=owner.email,
        algorithm="HS256",
        key="super-secret-jwt-token-with-at-least-32-characters-long",
    )
    response = await anon_client.get("/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert "HS256" in response.json()["detail"]


async def test_a_token_from_another_project_is_refused(
    signer: Signer, owner: Account, anon_client: httpx.AsyncClient
) -> None:
    token = signer.token(
        subject=owner.auth_user_id,
        email=owner.email,
        iss="https://someone-elses-project.supabase.co/auth/v1",
    )
    response = await anon_client.get("/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert "different project" in response.json()["detail"]


async def test_a_token_for_another_audience_is_refused(
    signer: Signer, owner: Account, anon_client: httpx.AsyncClient
) -> None:
    token = signer.token(subject=owner.auth_user_id, email=owner.email, aud="some-other-service")
    response = await anon_client.get("/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


async def test_an_expired_token_says_so(
    signer: Signer, owner: Account, anon_client: httpx.AsyncClient
) -> None:
    """The message matters: the web app refreshes on this one rather than
    bouncing the owner to the sign-in screen."""
    token = signer.token(
        subject=owner.auth_user_id, email=owner.email, expires_in=timedelta(minutes=-5)
    )
    response = await anon_client.get("/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert "expired" in response.json()["detail"].lower()


async def test_an_anonymous_session_is_refused(
    signer: Signer, owner: Account, anon_client: httpx.AsyncClient
) -> None:
    token = signer.token(subject=owner.auth_user_id, email=owner.email, is_anonymous=True)
    response = await anon_client.get("/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


async def test_a_service_role_token_is_not_a_user(
    signer: Signer, owner: Account, anon_client: httpx.AsyncClient
) -> None:
    """`service_role` is our own key, not a person. It must not act as one."""
    token = signer.token(
        subject=owner.auth_user_id, email=owner.email, role="service_role", aud="authenticated"
    )
    response = await anon_client.get("/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


async def test_a_malformed_header_is_refused(anon_client: httpx.AsyncClient) -> None:
    for header in ("", "Bearer", "Bearer ", "Basic abc123", "token abc123", "Bearer not.a.jwt"):
        response = await anon_client.get("/me", headers={"Authorization": header})
        assert response.status_code == 401, header


async def test_the_issuer_we_expect_matches_the_local_stack() -> None:
    """A guard against the .env and the running stack drifting apart, which
    would show up as every request failing with "different project"."""
    settings = get_settings()
    assert expected_issuer(settings).endswith("/auth/v1")
    assert settings.supabase_url, "SUPABASE_URL is unset; see .env.example"


def test_the_jwt_library_will_not_verify_without_a_key() -> None:
    """Belt and braces on the `alg: none` family of attacks.

    PyJWT refuses these itself; this asserts that it still does, because the
    consequence of it quietly changing is total.
    """
    forged = jwt.encode({"sub": "x", "role": "authenticated"}, key="", algorithm="none")
    with pytest.raises(jwt.PyJWTError):
        jwt.decode(forged, key="", algorithms=["ES256", "RS256"])


# --------------------------------------------------------------------------
# Roles
# --------------------------------------------------------------------------


async def test_staff_may_read_but_not_write(
    account: AccountFactory, anon_client: httpx.AsyncClient
) -> None:
    staff: Account = await account(role=MemberRole.STAFF, tenants=[POS_SHOP])

    readable = await anon_client.get(f"/tenants/{POS_SHOP}/members", headers=staff.headers)
    assert readable.status_code == 200

    refused = await anon_client.post(
        f"/tenants/{POS_SHOP}/members",
        headers=staff.headers,
        json={"email": "someone@example.com", "role": "staff"},
    )
    assert refused.status_code == 403
    assert "owner" in refused.json()["detail"]


async def test_a_manager_is_not_an_owner(
    account: AccountFactory, anon_client: httpx.AsyncClient
) -> None:
    manager: Account = await account(role=MemberRole.MANAGER, tenants=[POS_SHOP])
    refused = await anon_client.post(
        f"/tenants/{POS_SHOP}/members",
        headers=manager.headers,
        json={"email": "someone@example.com", "role": "staff"},
    )
    assert refused.status_code == 403


async def test_adding_somebody_with_no_account_says_what_to_do(
    account: AccountFactory, anon_client: httpx.AsyncClient
) -> None:
    boss: Account = await account(role=MemberRole.OWNER, tenants=[POS_SHOP])
    response = await anon_client.post(
        f"/tenants/{POS_SHOP}/members",
        headers=boss.headers,
        json={"email": "never-signed-up@example.com", "role": "staff"},
    )
    assert response.status_code == 404
    assert "sign up" in response.json()["detail"]


async def test_the_last_owner_cannot_be_demoted_or_removed(
    account: AccountFactory, anon_client: httpx.AsyncClient, scratch_shop: str
) -> None:
    """A shop with no owner has nobody who can connect a register or add anyone
    back. Both routes have to refuse, not just the delete.

    On a shop of this test's own, so that "the only owner" is a fact rather than
    a hope about what else is in the local database.
    """
    boss: Account = await account(role=MemberRole.OWNER, tenants=[scratch_shop])

    members = (
        await anon_client.get(f"/tenants/{scratch_shop}/members", headers=boss.headers)
    ).json()
    owners = [row for row in members if row["role"] == "owner"]
    assert len(owners) == 1
    mine = owners[0]["id"]

    demoted = await anon_client.put(
        f"/tenants/{scratch_shop}/members/{mine}", headers=boss.headers, json={"role": "manager"}
    )
    assert demoted.status_code == 409

    removed = await anon_client.delete(
        f"/tenants/{scratch_shop}/members/{mine}", headers=boss.headers
    )
    assert removed.status_code == 409

    # And the one that has to work: promote somebody else first, then step down.
    second: Account = await account(role=MemberRole.OWNER, tenants=[scratch_shop])
    assert second.email
    stepped_down = await anon_client.put(
        f"/tenants/{scratch_shop}/members/{mine}", headers=boss.headers, json={"role": "manager"}
    )
    assert stepped_down.status_code == 200
    assert stepped_down.json()["role"] == "manager"


async def test_a_membership_from_another_shop_cannot_be_edited(
    account: AccountFactory, anon_client: httpx.AsyncClient
) -> None:
    """The membership id is a uuid from the client, so the route has to filter
    by tenant as well — owning *a* shop is not owning *this* row."""
    boss_here: Account = await account(role=MemberRole.OWNER, tenants=[POS_SHOP])
    boss_there: Account = await account(role=MemberRole.OWNER, tenants=[SPREADSHEET_SHOP])

    theirs = (
        await anon_client.get(f"/tenants/{SPREADSHEET_SHOP}/members", headers=boss_there.headers)
    ).json()
    target = next(row["id"] for row in theirs if row["is_you"])

    response = await anon_client.put(
        f"/tenants/{POS_SHOP}/members/{target}",
        headers=boss_here.headers,
        json={"role": "staff"},
    )
    assert response.status_code == 404


async def test_revoking_a_membership_takes_effect_on_the_next_request(
    account: AccountFactory, anon_client: httpx.AsyncClient
) -> None:
    """The point of checking membership per request rather than trusting the
    token: the token is unchanged and still valid, and access is gone anyway."""
    boss: Account = await account(role=MemberRole.OWNER, tenants=[POS_SHOP])
    doomed: Account = await account(role=MemberRole.STAFF, tenants=[POS_SHOP])

    before = await anon_client.get(f"/tenants/{POS_SHOP}/members", headers=doomed.headers)
    assert before.status_code == 200

    members = (await anon_client.get(f"/tenants/{POS_SHOP}/members", headers=boss.headers)).json()
    target = next(row["id"] for row in members if row["email"] == doomed.email)
    assert (
        await anon_client.delete(f"/tenants/{POS_SHOP}/members/{target}", headers=boss.headers)
    ).status_code == 204

    after = await anon_client.get(f"/tenants/{POS_SHOP}/members", headers=doomed.headers)
    assert after.status_code == 404


# --------------------------------------------------------------------------
# Which writes need a manager
# --------------------------------------------------------------------------

# Writes a staff account may make, each because it changes nothing about the
# shop. Anything not on this list has to carry a role requirement.
STAFF_MAY_WRITE = {
    # Their own display name.
    "PUT /me",
    # Asking a question, and tidying up their own conversations afterwards.
    "POST /tenants/{slug}/chat",
    "PATCH /tenants/{slug}/conversations/{conversation_id}",
    "DELETE /tenants/{slug}/conversations/{conversation_id}",
    # "this finding was useful" — the signal is worth having from everyone, and
    # it does not mark anything as handled.
    "POST /tenants/{tenant}/insights/{insight_id}/feedback",
}


def _role_gated(route: APIRoute) -> bool:
    return any(
        getattr(dependency.call, "__qualname__", "").startswith("requires")
        for dependency in route.dependant.dependencies
    )


@pytest.mark.parametrize(
    "method,route",
    [(m, r) for m, r in _probes() if m not in {"GET"}],
    ids=lambda item: str(item),
)
def test_every_write_either_needs_a_role_or_is_listed_as_harmless(
    method: str, route: APIRoute
) -> None:
    """Checked by declaration rather than by calling it.

    A test that actually *sent* these as staff would, on the day a gate broke,
    fire a real sync at a customer's system and a real email at whoever is on the
    notifications list — `NOTIFY_TRANSPORT` is `live` on a machine with a Resend
    key. Reading the dependency off the route catches the drift without that
    risk; the behavioural check below covers the routes that are safe to poke.
    """
    if f"{method} {route.path}" in STAFF_MAY_WRITE:
        assert not _role_gated(route), (
            f"{method} {route.path} is listed as harmless for staff but carries a role "
            "requirement. Remove it from STAFF_MAY_WRITE or from the route."
        )
        return
    assert _role_gated(route), (
        f"{method} {route.path} lets a staff account write. Add "
        "`dependencies=MANAGER_ONLY` to the route, or add it to STAFF_MAY_WRITE "
        "with a reason."
    )


@pytest.mark.parametrize(
    "method,path",
    [
        ("POST", "/tenants/{tenant}/reorder/drafts"),
        ("PUT", "/tenants/{tenant}/staffing/shifts"),
        ("POST", "/tenants/{tenant}/members"),
        ("PUT", "/tenants/{slug}/appearance"),
    ],
)
async def test_staff_are_actually_refused_these(
    method: str, path: str, account: AccountFactory, anon_client: httpx.AsyncClient
) -> None:
    """The declaration above, proven end to end on the routes where being wrong
    costs nothing: a draft order, a shift, a membership, a colour."""
    staff: Account = await account(role=MemberRole.STAFF, tenants=[POS_SHOP])
    url = path.replace("{tenant}", POS_SHOP).replace("{slug}", POS_SHOP)

    response = await anon_client.request(method, url, headers=staff.headers, json={})
    assert response.status_code == 403, response.text
    assert "You are staff here" in response.json()["detail"]


async def test_health_says_whether_it_can_authenticate_anybody() -> None:
    """The check a deploy should read.

    `SUPABASE_URL` left pointing at a laptop is the way this goes wrong in
    production, and the symptom — every request 401s with "could not reach the
    sign-in service" — is visible only to the one person who cannot fix it.
    """
    health = await check_auth(get_settings())
    assert health.configured, health.detail
    assert health.reachable, health.detail
    assert health.keys >= 1
    assert health.issuer and health.issuer.endswith("/auth/v1")


async def test_health_reports_an_unconfigured_server_rather_than_pretending() -> None:
    settings = get_settings().model_copy(update={"supabase_url": "", "supabase_jwt_issuer": ""})
    health = await check_auth(settings)
    assert not health.configured
    assert not health.reachable
    assert health.detail and "SUPABASE_URL" in health.detail


async def test_health_reports_an_unreachable_auth_server() -> None:
    """A project URL that resolves to nothing. Configured, but no use."""
    settings = get_settings().model_copy(
        update={"supabase_url": "http://127.0.0.1:1", "supabase_jwt_issuer": ""}
    )
    health = await check_auth(settings)
    assert health.configured
    assert not health.reachable
    assert health.keys == 0
    assert health.detail
