"""The month-end packet: unglamorous, and the reason a shop renews in January.

The numbers a bookkeeper asks for, ready on the 1st, in a PDF to file and a
workbook to work with. Two commitments run through it:

* **Report, never advise.** Tax shown is what the POS recorded, not what is
  owed, and nothing here computes a liability.
* **Print the gap.** Net sales must agree with the semantic layer to the cent,
  and takings must reconcile with sales plus tax plus tips within a few
  dollars. Where they do not, the difference goes in the data notes rather
  than being adjusted away.

    packet = await generate(session, ctx)
    pdf = pdf_of(packet)
"""

from app.monthend.packet import Packet, build, month_before
from app.monthend.render import packet_pdf, packet_workbook
from app.monthend.service import (
    StoredPacket,
    email_packet,
    generate,
    get_packet,
    list_packets,
    pdf_of,
    store,
    workbook_of,
)
from app.monthend.tables import MonthEndPacket

__all__ = [
    "MonthEndPacket",
    "Packet",
    "StoredPacket",
    "build",
    "email_packet",
    "generate",
    "get_packet",
    "list_packets",
    "month_before",
    "packet_pdf",
    "packet_workbook",
    "pdf_of",
    "store",
    "workbook_of",
]
