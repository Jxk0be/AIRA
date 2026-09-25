"""HTTP for the digest preview and the "send me a test" button.

The preview is the same code path the Monday send uses, so the owner cannot be
shown one email and sent a different one.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field

from app.digest import prepare
from app.digest.service import send_test
from app.http import MANAGER_ONLY, ShopDep
from app.notify import recipients

router = APIRouter(tags=["digest"])


class PreviewOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant: str
    subject: str
    text: str
    html: str
    # "model" when the cheap model's wording passed the number check,
    # "template" when it did not or was unavailable. Shown in the preview so
    # the difference is never a mystery.
    copy_source: str
    week_start: date
    week_end: date
    net_sales: Decimal
    net_sales_previous: Decimal | None
    actions: int
    payload: dict[str, Any]


class TestIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str | None = Field(default=None, max_length=320)


# The real link is per-recipient and only exists once a message is addressed to
# somebody, so a preview has nothing to put there. Saying so beats showing the
# owner a template variable and beats dropping the line, which would read as if
# we send email with no way out of it.
PREVIEW_UNSUBSCRIBE = "(a link unique to each recipient)"


@router.get("/tenants/{tenant}/digest/preview", response_model=PreviewOut)
async def preview(shop: ShopDep, as_of: date | None = None) -> PreviewOut:
    prepared = await prepare(shop.session, shop.ctx, as_of=as_of)
    digest = prepared.digest
    return PreviewOut(
        tenant=shop.slug,
        subject=prepared.subject,
        text=prepared.rendered.text.replace("{{unsubscribe_url}}", PREVIEW_UNSUBSCRIBE),
        html=prepared.rendered.html.replace("{{unsubscribe_url}}", "#"),
        copy_source=prepared.rendered.copy_source,
        week_start=digest.week.start,
        week_end=digest.week.end,
        net_sales=digest.net_sales.value,
        net_sales_previous=digest.net_sales.previous,
        actions=len(digest.actions),
        payload=digest.payload(),
    )


@router.get("/tenants/{tenant}/digest/preview.html", response_class=Response)
async def preview_html(shop: ShopDep, as_of: date | None = None) -> Response:
    """The rendered email itself, for dropping into an iframe."""
    prepared = await prepare(shop.session, shop.ctx, as_of=as_of)
    html = prepared.rendered.html.replace("{{unsubscribe_url}}", "#")
    return Response(content=html, media_type="text/html")


@router.post("/tenants/{tenant}/digest/test", dependencies=MANAGER_ONLY)
async def test_send(shop: ShopDep, body: TestIn, as_of: date | None = None) -> dict[str, str]:
    """Send one copy to one person now, without marking anything as notified."""
    people = await recipients(shop.session, shop.ctx)
    if body.email:
        people = [person for person in people if person.email == body.email.strip().lower()]
    if not people:
        raise HTTPException(
            status_code=409,
            detail=(
                "Nobody at this shop is set up to receive email yet. Add a contact on the "
                "notifications screen first."
            ),
        )

    result = await send_test(shop.session, shop.ctx, people[0], as_of=as_of)
    return {
        "to": result.recipient,
        "status": str(result.status),
        "detail": result.detail or "",
    }
