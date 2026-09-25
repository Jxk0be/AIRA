from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from httpx import ASGITransport
from jwt.algorithms import ECAlgorithm
from sqlalchemy import delete, select, text
from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.accounts.roles import MemberRole
from app.accounts.tables import Membership, User
from app.accounts.tokens import expected_issuer, jwks_cache
from app.canonical import tables as t
from app.config import get_settings
from app.db import dispose_engine
from app.main import app

# Errors that mean "the stack isn't running", as opposed to "the query is wrong".
CONNECTION_ERRORS = (OSError, ConnectionError, OperationalError, InterfaceError, DBAPIError)


@pytest.fixture
async def db() -> AsyncIterator[AsyncSession]:
    """A session against the local Supabase stack.

    Its own engine with NullPool, not the application's: pytest-asyncio gives
    each test a fresh event loop, and an asyncpg connection pooled on a loop
    that has since closed blows up in confusing ways when the next test
    borrows it.

    Skips rather than fails when the stack is down, so `task test` still runs
    the pure-Python tests on a laptop with no Docker running.
    """
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    session = async_sessionmaker(engine, expire_on_commit=False)()
    try:
        try:
            await session.execute(text("select 1"))
        except CONNECTION_ERRORS as exc:
            pytest.skip(f"no database reachable ({type(exc).__name__}); run `python tasks.py db`")
        yield session
    finally:
        # Nothing a test writes is kept: every fixture row dies with the rollback.
        await session.rollback()
        await session.close()
        await engine.dispose()


# --------------------------------------------------------------------------
# Signing in
#
# Every route is behind `app.accounts.guard`, so a test needs a token. Rather
# than mock the guard out — which would leave the thing most worth testing
# untested — the suite generates its own ES256 key, hands the public half to the
# JWKS cache, and signs real tokens with the private half. Verification then runs
# the same code path production does: same algorithm, same issuer and audience
# checks, same membership lookup.
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Signer:
    """A stand-in for the project's auth server."""

    kid: str
    private_key: Any
    issuer: str

    def token(
        self,
        *,
        subject: uuid.UUID,
        email: str,
        expires_in: timedelta = timedelta(hours=1),
        algorithm: str = "ES256",
        key: Any = None,
        **overrides: Any,
    ) -> str:
        """One access token, shaped exactly like GoTrue's.

        The claim set was taken from a real token minted by the local stack, so
        a test that passes here is not passing against a simplified fake.
        """
        now = datetime.now(UTC)
        claims: dict[str, Any] = {
            "iss": self.issuer,
            "sub": str(subject),
            "aud": "authenticated",
            "iat": int(now.timestamp()),
            "exp": int((now + expires_in).timestamp()),
            "email": email,
            "phone": "",
            "app_metadata": {"provider": "email", "providers": ["email"]},
            "user_metadata": {"email": email, "email_verified": True},
            "role": "authenticated",
            "aal": "aal1",
            "amr": [{"method": "password", "timestamp": int(now.timestamp())}],
            "session_id": str(uuid.uuid4()),
            "is_anonymous": False,
        }
        claims.update(overrides)
        return jwt.encode(
            claims,
            key if key is not None else self.private_key,
            algorithm=algorithm,
            headers={"kid": self.kid},
        )


@pytest.fixture(scope="session")
def signer() -> Signer:
    """The suite's own signing key, installed as if Supabase had published it."""
    settings = get_settings()
    private_key = ec.generate_private_key(ec.SECP256R1())
    kid = str(uuid.uuid4())
    public_jwk = json.loads(ECAlgorithm.to_jwk(private_key.public_key()))
    public_jwk.update({"kid": kid, "use": "sig", "alg": "ES256"})
    jwks_cache(settings).put(public_jwk)
    return Signer(kid=kid, private_key=private_key, issuer=expected_issuer(settings))


async def _commit_session() -> AsyncSession:
    """A session that keeps what it writes.

    The `db` fixture rolls back, which is right for shop data a test invents.
    Accounts are different: the app resolves the caller through its *own*
    session, so a user that only exists inside an uncommitted transaction is a
    user the app cannot see.
    """
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    return async_sessionmaker(engine, expire_on_commit=False)()


@dataclass(frozen=True, slots=True)
class Account:
    """A signed-in person the suite created, and their token."""

    user_id: uuid.UUID
    auth_user_id: uuid.UUID
    email: str
    token: str

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}


AccountFactory = Callable[..., Any]


@pytest.fixture
async def account(signer: Signer) -> AsyncIterator[AccountFactory]:
    """Make a user with memberships, and clean them up afterwards.

    `await account()` gives an owner at every shop in the database, which is what
    the rest of the suite assumes — those tests are about analytics and screens,
    not about access. `await account(role=MemberRole.STAFF)` or
    `await account(tenants=["animanga_knox"])` narrows it for the tests that are.
    """
    session = await _commit_session()
    try:
        await session.execute(text("select 1"))
    except CONNECTION_ERRORS as exc:
        await session.close()
        pytest.skip(f"no database reachable ({type(exc).__name__}); run `python tasks.py db`")

    created: list[uuid.UUID] = []

    async def make(
        *,
        role: MemberRole = MemberRole.OWNER,
        tenants: list[str] | None = None,
        email: str | None = None,
    ) -> Account:
        auth_user_id = uuid.uuid4()
        address = email or f"pytest-{auth_user_id.hex[:12]}@aira.test"
        user = User(auth_user_id=auth_user_id, email=address)
        session.add(user)
        await session.flush()
        created.append(user.id)

        wanted = (
            select(t.Tenant.id).where(t.Tenant.deleted_at.is_(None))
            if tenants is None
            else select(t.Tenant.id).where(t.Tenant.slug.in_(tenants))
        )
        for (tenant_id,) in (await session.execute(wanted)).all():
            session.add(Membership(user_id=user.id, tenant_id=tenant_id, role=role))
        await session.commit()

        return Account(
            user_id=user.id,
            auth_user_id=auth_user_id,
            email=address,
            token=signer.token(subject=auth_user_id, email=address),
        )

    try:
        yield make
    finally:
        if created:
            # Memberships cascade from the user, so one delete is enough.
            await session.execute(delete(User).where(User.id.in_(created)))
            await session.commit()
        await session.close()


@pytest.fixture
async def scratch_shop() -> AsyncIterator[str]:
    """A shop of this test's own, deleted afterwards.

    For the access tests that need to know exactly who the members are. The two
    fake shops accumulate accounts as somebody works on the machine, so a test
    asserting "this is the only owner" against them is a test that skips on
    Tuesdays.

    It has no integration and no data, which is fine: the routes that care about
    membership do not go near the analytics layer.
    """
    session = await _commit_session()
    try:
        await session.execute(text("select 1"))
    except CONNECTION_ERRORS as exc:
        await session.close()
        pytest.skip(f"no database reachable ({type(exc).__name__}); run `python tasks.py db`")

    slug = f"pytest-shop-{uuid.uuid4().hex[:8]}"
    tenant = t.Tenant(slug=slug, name="Pytest Scratch Shop", timezone="UTC", currency="USD")
    session.add(tenant)
    await session.commit()
    try:
        yield slug
    finally:
        # Memberships cascade from the tenant.
        await session.execute(delete(t.Tenant).where(t.Tenant.id == tenant.id))
        await session.commit()
        await session.close()


@pytest.fixture
async def owner(account: AccountFactory) -> Account:
    """An owner at every shop. What the default `client` signs in as."""
    return await account()  # type: ignore[no-any-return]


@pytest.fixture
async def anon_client() -> AsyncIterator[httpx.AsyncClient]:
    """A client with no token, for the tests that check a route refuses one."""
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test", timeout=60) as c:
        yield c
    await dispose_engine()


@pytest.fixture
async def client(owner: Account) -> AsyncIterator[httpx.AsyncClient]:
    """An HTTP client wired straight into the app, signed in as a shop owner.

    The app's own engine is disposed afterwards. It pools connections on
    whichever event loop first opened them, pytest-asyncio hands every test a
    new loop, and a pooled asyncpg connection borrowed across that boundary
    fails inside SQLAlchemy's teardown with an error that names neither cause.

    Signed in by default so that the several hundred assertions about metrics and
    screens stay about metrics and screens. The guard itself is tested against
    `anon_client` in `test_accounts_auth.py`.
    """
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", timeout=60, headers=owner.headers
    ) as c:
        yield c
    await dispose_engine()
