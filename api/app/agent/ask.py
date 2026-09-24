"""Ask a shop's assistant something, from the terminal.

    python -m app.agent.ask --tenant tsundoku "How did last December go?"
    python -m app.agent.ask --tenant panel_and_pawn "What's my margin on board games?"

The same code path the API uses, printed instead of streamed over HTTP. It is
the quickest way to see what a question costs and which tools it reached for,
and it is what the golden questions in phase 9 will be run through.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from app.agent.assistant import ClaudeAssistant
from app.agent.context import build_context
from app.agent.events import EventType
from app.analytics import TenantNotFound
from app.db import dispose_engine, get_sessionmaker


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ask a shop's assistant a question.")
    parser.add_argument("--tenant", required=True, help="tenant slug, e.g. tsundoku")
    parser.add_argument("question", help="what to ask")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    assistant = ClaudeAssistant()
    try:
        async with get_sessionmaker()() as session:
            try:
                shop = await build_context(session, args.tenant)
            except TenantNotFound as exc:
                print(f"  {exc}")
                return 1

            print(f"\n  {shop.name} — {args.question}\n")
            failed = False
            async for event in assistant.stream(session, shop, args.question):
                if event.type is EventType.TOKEN:
                    print(event.data["text"], end="", flush=True)
                elif event.type is EventType.TOOL_START:
                    print(f"\n  [{event.data['tool']} {event.data['args']}]", flush=True)
                elif event.type is EventType.TOOL_END and event.data["error"]:
                    print(f"  [!] {event.data['error']}", flush=True)
                elif event.type is EventType.CHART:
                    print(
                        f"\n  [chart: {event.data['type']} — {event.data['title']}, "
                        f"{len(event.data['data'])} rows]",
                        flush=True,
                    )
                elif event.type is EventType.ERROR:
                    print(f"\n  FAILED: {event.data['message']}")
                    failed = True
                elif event.type is EventType.DONE:
                    data = event.data
                    print(
                        f"\n\n  {data['latency_ms'] / 1000:.1f}s   ${data['cost_usd']}   "
                        f"in {data['input_tokens']:,} (+{data['cache_read_tokens']:,} cached) "
                        f"out {data['output_tokens']:,}   tools: "
                        f"{', '.join(data['tools']) or 'none'}\n"
                    )
            return 1 if failed else 0
    finally:
        await dispose_engine()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
