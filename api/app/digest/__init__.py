"""The weekly digest: the feature that keeps a subscription.

Most owners will never open a dashboard. They will read four lines on a phone
before the shop opens on Monday, and those four lines have to be true.

Three modules: `build` gathers the figures from the semantic layer and the
insights, `render` writes the email (letting the cheap model improve the
connecting sentences, and throwing its answer away if it contains a figure the
payload did not), and `service` decides whether to send at all.

    prepared = await prepare(session, ctx)     # what the preview shows
    await send(session, ctx)                   # what Monday 7am does
"""

from app.digest.build import Comparison, Digest, build, last_full_week
from app.digest.render import Copy, Rendered, plain_copy, render, write_copy
from app.digest.service import DigestSkipped, Prepared, prepare, send, send_test, staleness

__all__ = [
    "Comparison",
    "Copy",
    "Digest",
    "DigestSkipped",
    "Prepared",
    "Rendered",
    "build",
    "last_full_week",
    "plain_copy",
    "prepare",
    "render",
    "send",
    "send_test",
    "staleness",
    "write_copy",
]
