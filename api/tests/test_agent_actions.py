"""The catalogue of things an answer may offer to do.

The point of `app.agent.actions` is that the model picks a key and we supply
the route, so what is worth testing is the refusals: a key we do not have, an
email where a screen was asked for, a third button, and — the one that would
be caught by nothing else — a route in the catalogue that the web app does not
actually serve.

No database and no model here. The catalogue is a constant.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.agent import actions as act

WEB_ROUTER = Path(__file__).resolve().parents[2] / "web" / "src" / "router.ts"


def test_every_key_resolves_to_something_the_model_can_offer() -> None:
    for offer in act.CATALOG:
        email = (
            act.EmailDraft(subject="s", body="b") if offer.kind is act.ActionKind.EMAIL else None
        )
        spec = act.resolve(offer.key, email=email)
        assert spec.key == offer.key
        assert spec.label
        # Exactly one of the three ways a button does something.
        assert sum(x is not None for x in (spec.route, spec.task, spec.email)) == 1


def test_an_invented_key_is_refused_and_the_real_ones_are_named() -> None:
    """The message goes back to the model, so it has to say what to use instead."""
    with pytest.raises(act.ActionRejected) as raised:
        act.resolve("open_the_pod_bay_doors")
    assert "open_reorder" in str(raised.value)


def test_a_screen_may_not_carry_an_email_and_an_email_needs_one() -> None:
    with pytest.raises(act.ActionRejected):
        act.resolve("open_reorder", email=act.EmailDraft(subject="s", body="b"))
    with pytest.raises(act.ActionRejected):
        act.resolve("draft_email")


def test_the_label_is_the_models_when_it_wrote_one() -> None:
    plain = act.resolve("open_reorder")
    written = act.resolve("open_reorder", label="Order the four that are low")
    assert plain.label == "Open the reorder list"
    assert written.label == "Order the four that are low"


def test_a_third_button_is_refused_and_a_repeat_is_not() -> None:
    offered: list[act.ActionSpec] = []
    act.add(offered, act.resolve("open_reorder"))
    # Emphasis, not an error: it costs a round trip to tell the model off for it.
    act.add(offered, act.resolve("open_reorder"))
    assert len(offered) == 1

    act.add(offered, act.resolve("open_not_selling"))
    with pytest.raises(act.ActionRejected):
        act.add(offered, act.resolve("open_stock"))
    assert len(offered) == act.MAX_PER_TURN


def test_the_catalogue_only_names_screens_the_web_app_has() -> None:
    """A button that 404s is worse than no button.

    The routes live in `web/src/router.ts` and the keys live here, and nothing
    else connects the two — so this reads the router and checks that every
    route we hand out is one it declares. `router.ts` also lists the *old*
    names as redirects, and those are deliberately not accepted: an action
    written against a redirect breaks the day someone tidies the redirects up.
    """
    if not WEB_ROUTER.exists():  # pragma: no cover - API checked out on its own
        pytest.skip("the web app is not in this checkout")
    source = WEB_ROUTER.read_text(encoding="utf-8")
    # The `path:` of each child of `/:tenant`, which is what an action's route
    # is relative to. `''` is the shop's home.
    declared = set(re.findall(r"^\s*path: '([a-z-]*)',$", source, re.MULTILINE))

    for offer in act.CATALOG:
        if offer.route is None:
            continue
        screen = offer.route.split("?")[0]
        assert screen in declared, (
            f"{offer.key} points at /{screen}, which the web app has no route for"
        )
