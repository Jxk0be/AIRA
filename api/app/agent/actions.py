"""What an answer is allowed to offer to do next, and nothing else.

An answer that ends "those four should go on an order" and leaves the owner to
go and find the reorder screen has done about half the job. So a turn may end
with one or two buttons: open the screen that does the thing, draft the email
that says it, run the checks again.

The model does not get to say where a button goes. It picks a key out of
`CATALOG` and we supply the route, for the same reason `charts.py` refuses
numbers no tool returned: a model that can write its own URL will eventually
write one that 404s in front of a customer, and a model that can name its own
endpoint is one borrowed document away from being a way to POST things. Every
route in here is a route the app already has, and a key that is not in the
catalogue is simply not an action.

Email is the one action that carries the model's own words, and it is
deliberately the one action we never perform. `draft_email` produces a draft
the owner reads in their own mail client and sends themselves — nothing leaves
this machine because a button was pressed. That is not timidity about the
feature; it is what makes it safe to let the model write to a vendor at all.
The notify pipeline in `app.notify` stays the only way we send anything, with
its consent, quiet hours and daily ceiling intact.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

# Two is a choice; three is a menu, and a menu at the end of an answer is the
# model hedging about which one it meant.
MAX_PER_TURN = 2


class ActionKind(StrEnum):
    #: Go to a screen this app already has.
    OPEN = "open"
    #: Hand the owner a written draft to send themselves.
    EMAIL = "email"
    #: Do something here, on the owner's own data. Always confirmed first.
    RUN = "run"


class EmailDraft(BaseModel):
    """A message the owner is about to read, edit and send. We never send it."""

    model_config = ConfigDict(extra="forbid")

    #: Who it is for, when the answer knows — a vendor from a purchase order,
    #: say. Usually empty, and the owner fills it in.
    to: str | None = Field(default=None, max_length=320)
    subject: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=4000)


class ActionSpec(BaseModel):
    """One button, as the UI receives it and as the message stores it."""

    model_config = ConfigDict(extra="forbid")

    key: str
    kind: ActionKind
    label: str = Field(max_length=40)
    #: One line under the button saying why it is being offered. Optional, and
    #: worth having: "because four of these are below a week's cover".
    detail: str | None = Field(default=None, max_length=140)
    #: Where it goes, for OPEN. Relative to the shop, so `/:tenant/` prefixes it.
    route: str | None = None
    #: What it does, for RUN. A name the client maps onto one of its own calls —
    #: never a URL, so the answer cannot aim a POST at anything.
    task: str | None = None
    email: EmailDraft | None = None


class ActionRejected(ValueError):
    """The offer is not one we make. The message goes back to the model."""


@dataclass(frozen=True, slots=True)
class Offer:
    """A catalogue entry: what the model may offer, and what happens if pressed."""

    key: str
    kind: ActionKind
    #: What the button says when the model does not write something better.
    label: str
    #: What this is for, in the words the model reads. Ends up in the tool
    #: description, so it is the only thing telling it when to reach for this.
    purpose: str
    route: str | None = None
    task: str | None = None


# Routes are the ones in `web/src/router.ts` as they stand today. The old names
# still redirect, but an action written against a redirect is a bug waiting for
# someone to tidy the redirects up.
CATALOG: tuple[Offer, ...] = (
    Offer(
        key="open_worth_doing",
        kind=ActionKind.OPEN,
        label="See what needs doing",
        purpose="the list of everything the product has noticed, worst first",
        route="",
    ),
    Offer(
        key="open_reorder",
        kind=ActionKind.OPEN,
        label="Open the reorder list",
        purpose="what to reorder and how many, grouped by supplier, where an order is raised",
        route="stock?tab=reorder",
    ),
    Offer(
        key="open_not_selling",
        kind=ActionKind.OPEN,
        label="Open what is not selling",
        purpose="dead stock: money sitting still, and the markdown plays for it",
        route="stock?tab=not-selling",
    ),
    Offer(
        key="open_stock",
        kind=ActionKind.OPEN,
        label="Open the stock list",
        purpose="every item with its stock on hand, searchable",
        route="stock?tab=all",
    ),
    Offer(
        key="open_sales_report",
        kind=ActionKind.OPEN,
        label="Open the sales report",
        purpose="sales over time with the breakdowns, for a period the owner picks",
        route="reports?tab=sales",
    ),
    Offer(
        key="open_busy_hours",
        kind=ActionKind.OPEN,
        label="Open busy hours",
        purpose="when the shop is actually busy, by weekday and hour, and the shifts against it",
        route="reports?tab=busy-hours",
    ),
    Offer(
        key="open_month_end",
        kind=ActionKind.OPEN,
        label="Open month end",
        purpose="the month-end packet: the PDF and spreadsheet a bookkeeper is sent",
        route="reports?tab=month-end",
    ),
    Offer(
        key="open_data",
        kind=ActionKind.OPEN,
        label="Open data & sync",
        purpose="when the data last synced and what it complained about; where a sync is started",
        route="settings?tab=data",
    ),
    Offer(
        key="open_documents",
        kind=ActionKind.OPEN,
        label="Open documents",
        purpose=(
            "the shop's own uploaded documents — policies, FAQs, schedules — and how to add one"
        ),
        route="settings?tab=documents",
    ),
    Offer(
        key="open_notifications",
        kind=ActionKind.OPEN,
        label="Open notification settings",
        purpose="who hears from us, when, and what we have sent them",
        route="settings?tab=notifications",
    ),
    Offer(
        key="run_checks",
        kind=ActionKind.RUN,
        label="Run the checks now",
        purpose=(
            "re-run every detector against the latest synced data. Offer it when the answer "
            "turned on something that may have changed since the last run, not by default"
        ),
        task="run_checks",
    ),
    Offer(
        key="draft_email",
        kind=ActionKind.EMAIL,
        label="Open this email",
        purpose="a written draft the owner reviews and sends from their own mail app",
    ),
)

BY_KEY = {offer.key: offer for offer in CATALOG}

#: The keys `offer_action` accepts, which is everything except the email — that
#: one needs a subject and a body, so it has a tool of its own.
OFFERABLE = tuple(offer.key for offer in CATALOG if offer.kind is not ActionKind.EMAIL)


def catalogue_lines() -> str:
    """The catalogue as the model reads it, one key per line."""
    return "\n".join(
        f"- {offer.key}: {offer.purpose}" for offer in CATALOG if offer.key in OFFERABLE
    )


def resolve(
    key: str,
    *,
    label: str | None = None,
    detail: str | None = None,
    email: EmailDraft | None = None,
) -> ActionSpec:
    """Turn what the model asked for into a button, or refuse it.

    Raises `ActionRejected` with a message written for the model, because the
    model is the one that has to fix it: it goes straight back as the tool's
    error result.
    """
    offer = BY_KEY.get(key)
    if offer is None:
        known = ", ".join(sorted(BY_KEY))
        raise ActionRejected(
            f"There is no action called {key!r}. The ones that exist are: {known}."
        )
    if offer.kind is ActionKind.EMAIL and email is None:
        raise ActionRejected("An email draft needs both a subject and a body.")
    if offer.kind is not ActionKind.EMAIL and email is not None:
        raise ActionRejected(
            f"{key!r} opens a screen; only draft_email carries a subject and a body."
        )

    chosen = (label or offer.label).strip()
    return ActionSpec(
        key=offer.key,
        kind=offer.kind,
        # A label longer than the button is a label nobody reads the end of.
        label=chosen[:40] or offer.label,
        detail=(detail or "").strip()[:140] or None,
        route=offer.route,
        task=offer.task,
        email=email,
    )


def add(offered: list[ActionSpec], action: ActionSpec) -> None:
    """Add one to this turn's offers, refusing a third and quietly ignoring a repeat.

    A repeat is not worth an error: the model offering the reorder list twice in
    one turn is it being emphatic, not it being wrong, and sending that back as
    a failure costs a whole extra round trip to fix nothing.
    """
    if any(existing.key == action.key for existing in offered):
        return
    if len(offered) >= MAX_PER_TURN:
        raise ActionRejected(
            f"You have already offered {MAX_PER_TURN} actions this turn, which is the most "
            "an answer may end with. Pick the one that matters."
        )
    offered.append(action)


__all__ = [
    "BY_KEY",
    "CATALOG",
    "MAX_PER_TURN",
    "OFFERABLE",
    "ActionKind",
    "ActionRejected",
    "ActionSpec",
    "EmailDraft",
    "Offer",
    "add",
    "catalogue_lines",
    "resolve",
]
