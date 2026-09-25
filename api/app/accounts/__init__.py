"""Who is asking, and which shops they are allowed to ask about.

Authentication is Supabase Auth: the web app signs in against GoTrue and sends
its access token as a bearer. We verify that token ourselves against the
project's public JWKS (`app.accounts.tokens`) rather than calling the auth
server on every request, and we keep our own `users` and `memberships` tables
so that "which shops may this person see" is a join in our database rather than
a claim in a token somebody else issues.

The split matters. A token proves *identity* and nothing else; every
authorisation decision in this product is a membership row (CLAUDE.md rule 3).
Nothing here ever reads `user_metadata`, which the signed-in user can edit
themselves.
"""

from __future__ import annotations

from app.accounts.roles import MemberRole
from app.accounts.tokens import AccessToken, TokenInvalid, verify_access_token

__all__ = ["AccessToken", "MemberRole", "TokenInvalid", "verify_access_token"]
