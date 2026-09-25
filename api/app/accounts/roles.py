"""What a member of a shop is allowed to do.

Three roles, ordered, because every permission question this product has so far
is "is this person at least a ...?" rather than a matrix. Keeping it ordered is
what lets a route say `MemberRole.MANAGER` and be done.

The split follows who carries the consequence:

* **owner** — the shop. Billing, connecting and disconnecting a register,
  adding and removing people. Things that cost money or change who has access.
* **manager** — the day job. Approving a reorder, raising a purchase order,
  entering costs, changing notification settings, running a sync.
* **staff** — reads the screens and asks the assistant. Writes nothing.

`staff` exists because Shopify charges for staff accounts on a $105 plan and we
do not; a shop with a weekend assistant should not be sharing the owner's login.
"""

from __future__ import annotations

from enum import StrEnum


class MemberRole(StrEnum):
    OWNER = "owner"
    MANAGER = "manager"
    STAFF = "staff"

    @property
    def rank(self) -> int:
        return _RANK[self]

    def at_least(self, needed: MemberRole) -> bool:
        """True when this role carries everything `needed` carries."""
        return self.rank >= needed.rank


# Higher is more. Only `at_least` reads this, so the numbers are private and a
# role can be inserted between two others without anything else changing.
_RANK: dict[MemberRole, int] = {
    MemberRole.STAFF: 0,
    MemberRole.MANAGER: 1,
    MemberRole.OWNER: 2,
}
