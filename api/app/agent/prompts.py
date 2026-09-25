"""What the assistant is told to be, and what it is told about the shop.

Two blocks, in this order, because prompt caching is a prefix match: the part
that never changes first, the shop's own briefing second, with the cache
breakpoint at the end of it. Tools render before the system prompt, so one
breakpoint here covers the tool definitions too, and a five-turn conversation
pays for all of it once.

The instructions are deliberately short. A current model does not need to be
told how to be helpful; it needs to be told the two or three things about *this*
job that it could not guess — that the numbers come from tools, that the caveats
matter, and that a shop owner reading this has about fifteen seconds.
"""

from __future__ import annotations

from anthropic.types import TextBlockParam

from app.agent.context import ShopContext

ANALYST = """\
You are the analyst for a small retail shop. You work for the owner and the
people behind the counter, and you answer in plain language — no dashboards, no
jargon, no hedging.

How you work:

- Every number you give comes from a tool. You never estimate, never extrapolate
  from a previous answer, and never do arithmetic the tools can do for you. If
  the tools cannot answer something, say so plainly and say what would fix it.
- You say which dates you used, because "sales are up" means nothing without
  them. Dates are the shop's local dates.
- When a tool returns caveats, you pass on the ones that change how the number
  should be read — a margin covering 34% of sales is a different claim from a
  margin, and the owner has to hear that in the same breath.
- When a tool refuses because the shop's system does not record something, that
  is the answer. Explain it in one sentence and move on. Do not substitute a
  number that means something else.
- You are brief. Lead with the answer, give the two or three numbers that
  support it, and stop.
- When you notice something worth doing, end with one concrete action: reorder
  this, mark that down, bundle these two, put someone extra on that weekend. One,
  not a list, and only when the data actually supports it.
- When that action is something this app can do — a screen that does the thing,
  an email worth writing — offer the button for it with `offer_action` or
  `draft_email`, and say what it is in the sentence rather than leaving the
  button to explain itself. Do not offer one on every answer, and never offer
  one instead of answering.

You only ever see this one shop's data, and every tool is already scoped to it.
"""


def _capability_lines(shop: ShopContext) -> list[str]:
    """What this shop's system can and cannot tell us, in the shop's terms.

    Spelling out the absences is the point. A model that is only told what it
    has will keep reaching for what it does not.
    """
    capabilities = shop.analytics.capabilities
    lines = []
    if capabilities.has_costs:
        lines.append("- Item costs are recorded, so margin is available (mind the coverage).")
    else:
        lines.append("- No item costs, so margin and profit cannot be worked out at all.")
    if capabilities.has_customers:
        lines.append("- Sales can be tied to customers, so repeat business is measurable.")
    else:
        lines.append(
            "- Sales carry no customer, so there is no way to tell a regular from a first-timer."
        )
    if not capabilities.has_inventory_history:
        lines.append("- Stock is a snapshot as of the last sync, with no history behind it.")
    return lines


def shop_briefing(shop: ShopContext) -> str:
    locations = ", ".join(location.name for location in shop.locations) or "none recorded"
    channels = ", ".join(channel.value for channel in shop.channels) or "none recorded"
    categories = ", ".join(shop.categories) or "none recorded"
    window = (
        f"{shop.first_sale.isoformat()} to {shop.last_sale.isoformat()}"
        if shop.first_sale and shop.last_sale
        else "no sales synced yet"
    )

    parts = [
        f"The shop: {shop.name}",
        f"Today, in the shop's timezone ({shop.analytics.timezone}): {shop.today.isoformat()}",
        f"Money is in {shop.analytics.currency}.",
        f"Sales data covers {window}. Anything outside that is missing, not zero.",
        f"Locations: {locations}",
        f"Channels in use: {channels}",
        f"Categories: {categories}",
        "",
        "What this shop's system records:",
        *_capability_lines(shop),
    ]

    if shop.caveats:
        parts += [
            "",
            "Known problems with this shop's data, from the last sync:",
            *(f"- {caveat}" for caveat in shop.caveats),
        ]

    return "\n".join(parts)


def system_blocks(shop: ShopContext) -> list[TextBlockParam]:
    """The system prompt, with the cache breakpoint after the briefing."""
    return [
        TextBlockParam(type="text", text=ANALYST),
        TextBlockParam(
            type="text",
            text=shop_briefing(shop),
            cache_control={"type": "ephemeral"},
        ),
    ]


TITLE_PROMPT = """\
Give this question a title for a conversation list: at most six words, no
quotes, no trailing full stop, in the words the person used. Reply with the
title and nothing else.

Question: {question}\
"""


def starter_questions(shop: ShopContext) -> list[str]:
    """Four openers, tailored to what this shop's system can actually answer.

    The dashboard shows these on an empty conversation. A starter question the
    shop's data cannot support is a promise broken on the first click.
    """
    questions = ["How did last month go?", "What should I reorder this week?"]
    if shop.analytics.capabilities.has_costs:
        questions.append("Which categories make me the most money?")
    else:
        questions.append("What's been sitting on the shelf longest?")
    if shop.analytics.capabilities.multi_location:
        questions.append("How did the event booth do against a normal weekend?")
    elif shop.analytics.capabilities.has_customers:
        questions.append("How many of my customers come back?")
    else:
        questions.append("What sells best on a Saturday?")
    return questions
