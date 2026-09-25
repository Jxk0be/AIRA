"""The one place a request is let in.

Installed as an application-wide dependency in `app.main`, so **every** route is
private unless its path template is named in `PUBLIC_ROUTES` below. That
direction matters: a route added next month is protected because nobody did
anything, rather than unprotected because somebody forgot. `test_accounts_auth`
walks `app.routes` and asserts exactly that, so adding a route and forgetting to
think about auth fails the suite rather than shipping.

The guard does two separate jobs and it is worth keeping them separate in your
head:

1. **Authentication** — whose request is this? Answered by the bearer token.
2. **Authorisation** — may they see *this shop*? Answered by a `memberships`
   row, read from our database on every request, never from the token.

Routes keep resolving the tenant from the path slug exactly as they did before,
because the guard has already established that the caller is entitled to it. The
slug is still never an id from the client (CLAUDE.md rule 3).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any, Final

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.accounts.roles import MemberRole
from app.accounts.service import (
    EmailAlreadyClaimed,
    Member,
    Viewer,
    member_for_slug,
    resolve_viewer,
)
from app.accounts.tokens import AuthNotConfigured, TokenInvalid, verify_access_token
from app.db import get_session

# Path templates served without a signed-in user, each for a stated reason.
PUBLIC_ROUTES: frozenset[str] = frozenset(
    {
        # Service metadata. Says nothing about any shop.
        "/",
        "/health",
        # One-click unsubscribe from our own emails. The whole point is that it
        # works from a mail client with no login, and the token in the path is
        # the authorisation.
        "/unsubscribe/{token}",
    }
)

# The path parameter that names a shop. Two spellings exist in the codebase; the
# guard accepts either rather than making every router rename its parameter.
TENANT_PATH_PARAMS: tuple[str, ...] = ("tenant", "slug")

UNAUTHENTICATED = {"WWW-Authenticate": "Bearer"}


def _bearer(request: Request) -> str:
    header = request.headers.get("Authorization") or ""
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sign in to use AIRA.",
            headers=UNAUTHENTICATED,
        )
    return token.strip()


async def guard(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    """Authenticate the caller, and check they belong to the shop in the path."""
    route = request.scope.get("route")
    template = getattr(route, "path", None) or request.url.path
    if template in PUBLIC_ROUTES:
        return

    token_text = _bearer(request)
    try:
        token = await verify_access_token(token_text)
    except TokenInvalid as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers=UNAUTHENTICATED,
        ) from exc
    except AuthNotConfigured as exc:
        # Our fault, not theirs. A 401 here would send an honest user round the
        # sign-in loop forever.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Sign-in is not configured on this server.",
        ) from exc

    try:
        viewer = await resolve_viewer(session, token)
    except EmailAlreadyClaimed as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    request.state.viewer = viewer

    slug = next(
        (value for name in TENANT_PATH_PARAMS if (value := request.path_params.get(name))), None
    )
    if slug is None:
        # A route about the person rather than a shop, e.g. GET /tenants.
        return

    member = await member_for_slug(session, viewer, str(slug))
    if member is None:
        # Deliberately identical to the answer for a shop that does not exist.
        # Whether a slug is taken is not something a stranger gets to learn.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"no such shop: {slug}")
    request.state.member = member


def viewer_of(request: Request) -> Viewer:
    """The signed-in person. Only valid on a guarded route."""
    viewer: Viewer | None = getattr(request.state, "viewer", None)
    if viewer is None:  # pragma: no cover — means a route escaped the guard
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sign in to use AIRA.",
            headers=UNAUTHENTICATED,
        )
    return viewer


def member_of(request: Request) -> Member:
    """The caller's membership at the shop in the path."""
    member: Member | None = getattr(request.state, "member", None)
    if member is None:  # pragma: no cover — means the route has no tenant param
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="no shop was named in this request"
        )
    return member


def requires(role: MemberRole) -> Callable[[Member], Member]:
    """Refuse the request unless the caller holds at least `role` at this shop.

    A 403, not a 404: they can already see the shop, so there is nothing left to
    hide, and "ask your owner" is a more useful answer than "no such thing".
    """

    def check(member: Annotated[Member, Depends(member_of)]) -> Member:
        if not member.at_least(role):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"This needs the {role.value} role at {member.tenant_slug}. "
                    f"You are {member.role.value} here."
                ),
            )
        return member

    return check


# Ready-made for a route decorator: `dependencies=MANAGER_ONLY`. Declaring it
# there rather than as a parameter keeps it out of the handler's signature while
# still putting it in the OpenAPI schema, which is what the web app reads to know
# whether to render a button.
MANAGER_ONLY: Final[list[Any]] = [Depends(requires(MemberRole.MANAGER))]
OWNER_ONLY: Final[list[Any]] = [Depends(requires(MemberRole.OWNER))]

ViewerDep = Annotated[Viewer, Depends(viewer_of)]
MemberDep = Annotated[Member, Depends(member_of)]
ManagerDep = Annotated[Member, Depends(requires(MemberRole.MANAGER))]
OwnerDep = Annotated[Member, Depends(requires(MemberRole.OWNER))]
