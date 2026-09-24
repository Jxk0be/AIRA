"""Turning a digest into an email somebody will actually read.

Two stages, and the second one is optional. The plain template always produces
a complete, correct, slightly flat email. The model is then asked for better
connecting sentences, its answer is checked figure by figure against the
payload, and anything that fails goes in the bin and the plain wording is sent.

An unreachable model therefore costs a duller Monday email and nothing else,
which is the only acceptable failure mode for something that goes out
unattended at seven in the morning.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.digest.build import Digest, deep_link
from app.llm import Phraser, check_numbers, facts_from
from app.notify import app_link

COPY_SYSTEM = (
    "You write the connecting sentences in a weekly email from a retail analytics tool to "
    "the owner of a small shop. You are given every figure as data. "
    "Rules, in order of importance: never write a number that is not in the data; never "
    "add a claim the data does not make; write like a sharp employee, not a marketer. "
    "Plain sentences, no hype, no emoji, no exclamation marks. Short."
)


@dataclass(slots=True)
class Copy:
    """The written parts of the email."""

    subject: str
    intro: str
    action_blurbs: list[str] = field(default_factory=list)
    sold_blurb: str = ""


@dataclass(slots=True)
class Rendered:
    subject: str
    text: str
    html: str
    copy_source: str  # "model" or "template"


def plain_copy(digest: Digest) -> Copy:
    """The wording that always works, built straight from the figures."""
    net = digest.net_sales
    if net.change is None:
        headline = f"{digest.shop_name} took ${net.value:,.2f} last week."
    else:
        word = "up" if net.change > 0 else "down" if net.change < 0 else "level with"
        share = (
            f" ({abs(net.change_share):.0%})"
            if net.change_share is not None and net.change_share
            else ""
        )
        headline = (
            f"{digest.shop_name} took ${net.value:,.2f} last week, "
            f"{word} ${abs(net.change):,.2f}{share} on the week before."
        )

    year = ""
    if digest.year_ago is not None and digest.year_ago.previous is not None:
        year = f" The same week last year was ${digest.year_ago.previous:,.2f}."

    intro = (
        f"{headline}{year} "
        f"{int(digest.orders.value)} orders, ${digest.average_order_value.value:,.2f} each "
        f"on average."
    )

    blurbs = [insight.summary for insight in digest.actions]
    sold = " ".join(digest.notable) if digest.notable else ""
    if not sold and digest.top_products:
        best = digest.top_products[0]
        sold = f"{best['name']} led the week at ${best['net_sales']}."

    return Copy(
        subject=f"{digest.shop_name}: last week in one minute",
        intro=intro,
        action_blurbs=blurbs,
        sold_blurb=sold,
    )


async def write_copy(digest: Digest, phraser: Phraser | None = None) -> tuple[Copy, str]:
    """Ask the model for better sentences; keep them only if every figure checks out."""
    fallback = plain_copy(digest)
    phraser = phraser or Phraser()
    if not phraser.available:
        return fallback, "template"

    payload = digest.payload()
    facts = facts_from(payload)
    # Dates in the payload are allowed to reappear in prose as their parts.
    allow = {
        str(digest.week.start.year),
        str(digest.week.start.day),
        str(digest.week.end.day),
        str(digest.week.start.month),
    }

    def parse(raw: Any) -> Copy:
        if not isinstance(raw, dict):
            raise TypeError("expected an object")
        subject = str(raw.get("subject") or "").strip()
        intro = str(raw.get("intro") or "").strip()
        blurbs = [str(item).strip() for item in raw.get("action_blurbs") or [] if str(item).strip()]
        sold = str(raw.get("sold_blurb") or "").strip()
        if not subject or not intro:
            raise ValueError("the model left out the subject or the intro")
        for piece in [subject, intro, sold, *blurbs]:
            check_numbers(piece, facts, allow=allow)
        return Copy(
            subject=subject[:120],
            intro=intro,
            action_blurbs=blurbs[: len(digest.actions)] or fallback.action_blurbs,
            sold_blurb=sold or fallback.sold_blurb,
        )

    copy = await phraser.json(
        system=COPY_SYSTEM,
        payload=payload,
        instruction=(
            "Write the email's connecting sentences. Reply with "
            '{"subject", "intro", "action_blurbs", "sold_blurb"}. '
            "The subject is one short line naming the shop and how the week went. "
            "The intro is one or two sentences on last week's trade. "
            "action_blurbs is one sentence per action, in the order given, saying what it is "
            "and why it is worth doing. sold_blurb is one sentence on what sold."
        ),
        parse=parse,
        fallback=fallback,
        max_tokens=900,
    )
    return copy, "template" if copy is fallback else "model"


def render(digest: Digest, copy: Copy, *, source: str = "template") -> Rendered:
    return Rendered(
        subject=copy.subject,
        text=_text(digest, copy),
        html=_html(digest, copy),
        copy_source=source,
    )


def _text(digest: Digest, copy: Copy) -> str:
    lines = [
        copy.subject,
        "=" * min(len(copy.subject), 60),
        "",
        copy.intro,
        "",
    ]

    if digest.actions:
        lines.append("WORTH DOING THIS WEEK")
        for index, insight in enumerate(digest.actions):
            blurb = (
                copy.action_blurbs[index] if index < len(copy.action_blurbs) else insight.summary
            )
            money = f" (${insight.dollar_impact:,.0f})" if insight.dollar_impact is not None else ""
            lines += [f"  {index + 1}. {insight.title}{money}", f"     {blurb}", ""]

    if digest.top_products:
        lines.append("WHAT SOLD")
        for row in digest.top_products:
            lines.append(f"  {row['name']} — ${row['net_sales']} ({row['units']} units)")
        if copy.sold_blurb:
            lines += ["", f"  {copy.sold_blurb}"]
        lines.append("")

    if digest.has_value_to_report and digest.ledger:
        ledger = digest.ledger
        lines += [
            "VALUE THIS MONTH",
            f"  {ledger.insights_acted} things acted on",
        ]
        if ledger.attributed_revenue:
            lines.append(f"  ${ledger.attributed_revenue:,.2f} of sales we can tie to them")
        if ledger.cash_recovered:
            lines.append(f"  ${ledger.cash_recovered:,.2f} of stuck stock turned back into cash")
        lines.append("")

    if digest.caveats:
        lines.append("WORTH KNOWING")
        lines += [f"  {caveat}" for caveat in digest.caveats]
        lines.append("")

    lines += [
        f"See everything: {app_link(digest.tenant_slug, 'dashboard')}",
        "",
        "Stop these emails: {{unsubscribe_url}}",
    ]
    return "\n".join(lines)


def _html(digest: Digest, copy: Copy) -> str:
    """A responsive email, built with tables and inline styles.

    Not because anyone enjoys it, but because half of these will be opened in
    a mail client that ignores stylesheets, and a digest that arrives as a wall
    of unstyled text is a digest that stops being read.
    """
    actions = ""
    for index, insight in enumerate(digest.actions):
        blurb = copy.action_blurbs[index] if index < len(copy.action_blurbs) else insight.summary
        money = (
            f'<span style="color:#7a7f99;font-weight:400"> · ${insight.dollar_impact:,.0f}</span>'
            if insight.dollar_impact is not None
            else ""
        )
        actions += f"""
        <tr><td style="padding:0 0 18px">
          <div style="font-weight:600;font-size:15px;color:#161a33">
            {_escape(insight.title)}{money}
          </div>
          <div style="font-size:14px;line-height:1.5;color:#474c66;margin-top:3px">
            {_escape(blurb)}
          </div>
          <a href="{_escape(deep_link(digest.tenant_slug, insight))}"
             style="display:inline-block;margin-top:7px;font-size:13px;color:#2b50e8;
                    text-decoration:none">
            {_escape(str(insight.suggested_action.get("label") or "Open it"))} &rarr;
          </a>
        </td></tr>"""

    products = "".join(
        f"""
        <tr>
          <td style="padding:5px 0;font-size:14px;color:#161a33">{_escape(str(row["name"]))}</td>
          <td style="padding:5px 0;font-size:14px;color:#474c66;text-align:right;
                     white-space:nowrap">${row["net_sales"]}</td>
        </tr>"""
        for row in digest.top_products
    )

    value = ""
    if digest.has_value_to_report and digest.ledger:
        ledger = digest.ledger
        bits = [f"{ledger.insights_acted} things acted on"]
        if ledger.attributed_revenue:
            bits.append(f"${ledger.attributed_revenue:,.2f} of sales we can tie to them")
        if ledger.cash_recovered:
            bits.append(f"${ledger.cash_recovered:,.2f} of stuck stock turned back into cash")
        value = f"""
        <tr><td style="padding:22px 0 0;border-top:1px solid #e3e5ef">
          <div style="font-size:11px;letter-spacing:.09em;text-transform:uppercase;
                      color:#7a7f99;margin-bottom:6px">This month so far</div>
          <div style="font-size:14px;color:#474c66">{_escape(" · ".join(bits))}</div>
        </td></tr>"""

    caveats = ""
    if digest.caveats:
        items = "".join(
            f'<li style="margin:3px 0">{_escape(caveat)}</li>' for caveat in digest.caveats
        )
        caveats = f"""
        <tr><td style="padding:18px 0 0">
          <div style="font-size:12px;color:#7a7f99">
            <ul style="margin:0;padding-left:18px">{items}</ul>
          </div>
        </td></tr>"""

    net = digest.net_sales
    change = ""
    if net.change is not None and net.change_share is not None:
        colour = "#1c7c4a" if net.change >= 0 else "#b4304f"
        arrow = "&uarr;" if net.change >= 0 else "&darr;"
        change = (
            f'<span style="color:{colour};font-size:15px;font-weight:600">'
            f"{arrow} {abs(net.change_share):.0%}</span>"
        )

    return f"""<!doctype html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_escape(copy.subject)}</title></head>
<body style="margin:0;padding:0;background:#f2f3f6">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"
       style="background:#f2f3f6;padding:24px 12px">
<tr><td align="center">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
         style="max-width:560px;background:#ffffff;border-radius:10px;padding:28px 26px;
                font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif">
    <tr><td style="padding-bottom:4px">
      <div style="font-size:11px;letter-spacing:.09em;text-transform:uppercase;color:#7a7f99">
        {_escape(digest.week.start.strftime("%d %b"))} –
        {_escape(digest.week.end.strftime("%d %b %Y"))}
      </div>
    </td></tr>
    <tr><td style="padding-bottom:14px">
      <div style="font-size:30px;font-weight:700;color:#161a33;line-height:1.15">
        ${digest.net_sales.value:,.2f} {change}
      </div>
      <div style="font-size:15px;line-height:1.55;color:#474c66;margin-top:8px">
        {_escape(copy.intro)}
      </div>
    </td></tr>
    {
        f'<tr><td style="padding:16px 0 10px;border-top:1px solid #e3e5ef">'
        f'<div style="font-size:11px;letter-spacing:.09em;text-transform:uppercase;'
        f'color:#7a7f99">Worth doing this week</div></td></tr>{actions}'
        if digest.actions
        else ""
    }
    {
        f'<tr><td style="padding:16px 0 6px;border-top:1px solid #e3e5ef">'
        f'<div style="font-size:11px;letter-spacing:.09em;text-transform:uppercase;'
        f'color:#7a7f99">What sold</div></td></tr>'
        f'<tr><td><table role="presentation" width="100%" cellpadding="0" cellspacing="0">'
        f"{products}</table>"
        f'<div style="font-size:14px;color:#474c66;margin-top:10px">'
        f"{_escape(copy.sold_blurb)}</div></td></tr>"
        if digest.top_products
        else ""
    }
    {value}
    {caveats}
    <tr><td style="padding:24px 0 0;border-top:1px solid #e3e5ef;margin-top:20px">
      <a href="{_escape(app_link(digest.tenant_slug, "dashboard"))}"
         style="display:inline-block;background:#2b50e8;color:#ffffff;font-size:14px;
                font-weight:600;text-decoration:none;padding:10px 18px;border-radius:6px">
        See everything
      </a>
    </td></tr>
    <tr><td style="padding:18px 0 0">
      <div style="font-size:11px;color:#9a9eb5">
        Sent to you because you run {_escape(digest.shop_name)}.
        <a href="{{{{unsubscribe_url}}}}" style="color:#9a9eb5">Stop these emails</a>.
      </div>
    </td></tr>
  </table>
</td></tr>
</table>
</body></html>"""


def _escape(text: str) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
