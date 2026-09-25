"""Verifying a Supabase Auth access token, without asking Supabase.

Every request carries a bearer token GoTrue issued. We check its signature
against the project's published public keys, cached in this process, rather than
calling the auth server once per request: a dashboard's first paint is one HTTP
call to us, and it should not become two, one of which is somebody else's
uptime.

Three decisions worth keeping:

* **Asymmetric signatures only.** A verifier that also accepts HS256 has the
  classic algorithm-confusion hole: the JWKS key is public by definition, and an
  attacker can sign a token of their own choosing using that public key as an
  HMAC secret. Refusing the algorithm outright closes it. Supabase signs with
  ES256 (P-256) on current projects and RS256 on older ones, and neither needs a
  shared secret to exist anywhere.
* **The issuer and audience are checked, not just the signature.** A validly
  signed token from a *different* Supabase project is a valid token; it is just
  not one of our users.
* **`user_metadata` is never read.** The signed-in user can write to it through
  the auth API, so a claim in there is an input, not a fact. Identity comes from
  `sub`; authorisation comes from our own `memberships` table.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

import httpx
import jwt
from jwt import PyJWK

from app.config import Settings, get_settings

ALGORITHMS: Final[tuple[str, ...]] = ("ES256", "RS256")

# Claims we refuse to work without. `role` and `aud` are what tell an access
# token apart from the other things GoTrue signs.
REQUIRED_CLAIMS: Final[tuple[str, ...]] = ("exp", "iat", "sub", "aud", "iss", "role")

# Supabase's own CDN caches the JWKS for ten minutes, so a shorter TTL here buys
# nothing.
JWKS_TTL_SECONDS: Final[float] = 600.0

# A token naming a `kid` we have never seen is the signal that keys rotated, so
# we refetch — but an attacker can put any `kid` they like in an unsigned
# header, and that must not turn into one outbound request per attempt.
JWKS_MIN_REFRESH_SECONDS: Final[float] = 10.0

JWKS_TIMEOUT: Final[float] = 10.0

# GoTrue's clock and ours are not the same clock.
LEEWAY_SECONDS: Final[float] = 10.0


class TokenInvalid(Exception):
    """This token cannot be trusted.

    The message is written to be returned to the caller: it says which check
    failed, never what the correct value would have been.
    """


class AuthNotConfigured(RuntimeError):
    """We cannot verify anything, because SUPABASE_URL is not set.

    A misconfiguration on our side, so callers turn this into a 503 rather than
    a 401 — telling an honest user their token is bad would be a lie.
    """


@dataclass(frozen=True, slots=True)
class AccessToken:
    """A verified token, reduced to the parts we act on."""

    # `sub`: the auth user's id. Stable for the life of the account, and what
    # our `users.auth_user_id` points at.
    subject: uuid.UUID
    email: str
    # Which sign-in this token belongs to. Recorded so that a future "sign out
    # everywhere" has something to compare against.
    session_id: str | None
    issued_at: datetime
    expires_at: datetime
    claims: Mapping[str, Any]


class JwksCache:
    """The project's public signing keys, held for a while.

    One instance per issuer. `key_for` is the whole interface; `put` exists so a
    test can hand it a key it generated instead of standing up an auth server,
    which means the tests exercise the same verification path production does.
    """

    def __init__(self, url: str, ttl: float = JWKS_TTL_SECONDS) -> None:
        self.url = url
        self.ttl = ttl
        self._keys: dict[str, PyJWK] = {}
        self._fetched_at: float = 0.0
        self._lock = asyncio.Lock()

    def put(self, jwk: Mapping[str, Any]) -> None:
        """Add one key by hand, and treat the cache as fresh."""
        key = PyJWK.from_dict(dict(jwk))
        if key.key_id is None:
            raise ValueError("a JWK with no kid cannot be cached")
        self._keys[key.key_id] = key
        self._fetched_at = time.monotonic()

    def clear(self) -> None:
        self._keys.clear()
        self._fetched_at = 0.0

    @property
    def count(self) -> int:
        """How many signing keys we are holding. Read by the health check."""
        return len(self._keys)

    @property
    def _stale(self) -> bool:
        return time.monotonic() - self._fetched_at > self.ttl

    async def ensure(self) -> None:
        """Fetch the keys if we have none or they are stale.

        For the health check, which wants to know whether the auth server is
        reachable *before* somebody's first sign-in rather than after it fails.
        """
        async with self._lock:
            if self._stale or not self._keys:
                await self._refresh()

    async def key_for(self, kid: str) -> PyJWK:
        if not self._stale and kid in self._keys:
            return self._keys[kid]

        async with self._lock:
            # Another request may have refreshed while we waited for the lock.
            if kid in self._keys and not self._stale:
                return self._keys[kid]
            if self._stale or time.monotonic() - self._fetched_at > JWKS_MIN_REFRESH_SECONDS:
                await self._refresh()

        try:
            return self._keys[kid]
        except KeyError:
            raise TokenInvalid("This token was signed with a key we do not know.") from None

    async def _refresh(self) -> None:
        try:
            async with httpx.AsyncClient(timeout=JWKS_TIMEOUT) as client:
                response = await client.get(self.url)
                response.raise_for_status()
                document = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise TokenInvalid(f"Could not reach the sign-in service ({exc}).") from exc

        keys: dict[str, PyJWK] = {}
        for entry in document.get("keys", []):
            try:
                key = PyJWK.from_dict(entry)
            except Exception:
                continue
            if key.key_id is not None:
                keys[key.key_id] = key
        self._keys = keys
        self._fetched_at = time.monotonic()


_caches: dict[str, JwksCache] = {}


def jwks_url(settings: Settings) -> str:
    if not settings.supabase_url:
        raise AuthNotConfigured("SUPABASE_URL is not set, so no token can be verified.")
    return f"{settings.supabase_url.rstrip('/')}/auth/v1/.well-known/jwks.json"


def expected_issuer(settings: Settings) -> str:
    """What `iss` has to say.

    Overridable, because a project behind a custom auth domain issues tokens
    naming that domain rather than its supabase.co URL.
    """
    if settings.supabase_jwt_issuer:
        return settings.supabase_jwt_issuer
    if not settings.supabase_url:
        raise AuthNotConfigured("SUPABASE_URL is not set, so no token can be verified.")
    return f"{settings.supabase_url.rstrip('/')}/auth/v1"


def jwks_cache(settings: Settings | None = None) -> JwksCache:
    """The cache for this project's keys, made once per issuer."""
    settings = settings or get_settings()
    url = jwks_url(settings)
    cache = _caches.get(url)
    if cache is None:
        cache = _caches[url] = JwksCache(url)
    return cache


def reset_jwks_caches() -> None:
    """Drop every cached key. Called between tests, and on nothing else."""
    _caches.clear()


@dataclass(frozen=True, slots=True)
class AuthHealth:
    """Whether this server can authenticate anybody, and why not.

    Reported by `/health`, because the way this goes wrong in a deploy is quiet:
    `SUPABASE_URL` left pointing at a laptop's loopback address means every
    request answers 401 with "could not reach the sign-in service", and the
    person reading that message is the one user who cannot fix it. Better for the
    health check to say so before anybody tries to sign in.
    """

    configured: bool
    issuer: str | None = None
    reachable: bool = False
    keys: int = 0
    detail: str | None = None


async def check_auth(settings: Settings | None = None) -> AuthHealth:
    """Can we verify a token right now?"""
    settings = settings or get_settings()
    try:
        issuer = expected_issuer(settings)
    except AuthNotConfigured as exc:
        return AuthHealth(configured=False, detail=str(exc))

    cache = jwks_cache(settings)
    try:
        await cache.ensure()
    except TokenInvalid as exc:
        # `ensure` reports an unreachable JWKS as TokenInvalid, because that is
        # what it means on the request path. Here it means the opposite: nothing
        # is wrong with anybody's token.
        return AuthHealth(configured=True, issuer=issuer, reachable=False, detail=str(exc))

    return AuthHealth(
        configured=True,
        issuer=issuer,
        reachable=cache.count > 0,
        keys=cache.count,
        detail=None if cache.count else "The auth server published no signing keys.",
    )


async def verify_access_token(token: str, settings: Settings | None = None) -> AccessToken:
    """Check a bearer token and return what it proves.

    Raises `TokenInvalid` for anything a caller could have sent, and
    `AuthNotConfigured` for anything that is our fault.
    """
    settings = settings or get_settings()

    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as exc:
        raise TokenInvalid("This is not a token we can read.") from exc

    algorithm = header.get("alg")
    if algorithm not in ALGORITHMS:
        # Refused here rather than left to `jwt.decode`, so that the rule and
        # the reason for it sit in the same file.
        raise TokenInvalid(f"Tokens signed with {algorithm!r} are not accepted.")

    kid = header.get("kid")
    if not isinstance(kid, str) or not kid:
        raise TokenInvalid("This token does not say which key signed it.")

    key = await jwks_cache(settings).key_for(kid)

    try:
        claims = jwt.decode(
            token,
            key=key,
            algorithms=list(ALGORITHMS),
            audience="authenticated",
            issuer=expected_issuer(settings),
            leeway=LEEWAY_SECONDS,
            options={"require": list(REQUIRED_CLAIMS), "verify_aud": True, "verify_iss": True},
        )
    except jwt.ExpiredSignatureError as exc:
        # The web app refreshes and retries on this one, so it has to be
        # distinguishable from "your token is wrong".
        raise TokenInvalid("Your session has expired. Sign in again.") from exc
    except jwt.InvalidAudienceError as exc:
        raise TokenInvalid("This token was not issued for signing in to AIRA.") from exc
    except jwt.InvalidIssuerError as exc:
        raise TokenInvalid("This token came from a different project.") from exc
    except jwt.PyJWTError as exc:
        raise TokenInvalid("This token could not be verified.") from exc

    if claims.get("role") != "authenticated":
        raise TokenInvalid("This token does not belong to a signed-in user.")

    # Anonymous sign-ins are off in `supabase/config.toml`, and this check is
    # what keeps a later flip of that switch from quietly creating accountless
    # users who can hold memberships.
    if claims.get("is_anonymous") is True:
        raise TokenInvalid("Anonymous sessions cannot be used here.")

    try:
        subject = uuid.UUID(str(claims["sub"]))
    except (KeyError, ValueError) as exc:
        raise TokenInvalid("This token names no user.") from exc

    email = str(claims.get("email") or "").strip().lower()
    if not email:
        raise TokenInvalid("This token carries no email address.")

    session_id = claims.get("session_id")
    return AccessToken(
        subject=subject,
        email=email,
        session_id=str(session_id) if session_id else None,
        issued_at=datetime.fromtimestamp(float(claims["iat"]), tz=UTC),
        expires_at=datetime.fromtimestamp(float(claims["exp"]), tz=UTC),
        claims=claims,
    )
