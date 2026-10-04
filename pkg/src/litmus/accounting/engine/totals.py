"""
A lot book's exact quantities (policy 05 rule 20.3): the per-key running
totals of quantity and basis, so that a position is read without summing the
key's lots, and the quantity net.

Every total is kept in a context in which additions of finite decimals never
round, and a position is that exact total, never a sum rounded to the engine's
context.
"""

from dataclasses import dataclass
from decimal import Context, Decimal, MAX_EMAX, MAX_PREC, MIN_EMIN

EXACT = Context(prec=MAX_PREC, Emax=MAX_EMAX, Emin=MIN_EMIN)
"""A context in which additions of finite decimals never round."""
ZERO = Decimal(0)
"""The empty sum."""

LotKey = tuple[str | None, str]
"""`(compartment or None, asset)`."""

NET = Decimal('1e-18')
"""
A holding smaller than this in absolute value after a take or an apply is
treated as zero (policy 05 rule 20.3). It is below the smallest unit of any
catalogued asset; one wei (`1e-18`) itself stays a holding.
"""


def crumb(quantity: Decimal) -> bool:
  """Whether `quantity` is a nonzero holding the quantity net treats as zero."""
  return quantity != 0 and quantity.copy_abs() < NET


@dataclass(frozen=True)
class Residue:
  """A holding the quantity net treated as zero (policy 05 rule 20.3)."""

  key: LotKey
  quantity: Decimal
  """What the holding would have been: positive left over, negative short."""


class Totals:
  """Exact running quantity and basis, per key."""

  def __init__(self):
    self.totals: dict[LotKey, Decimal] = {}
    """Net quantity per key, exact."""
    self.costs: dict[LotKey, Decimal] = {}
    """Net basis per key, exact (the open-lot rows read it)."""
    self.cost = ZERO
    """Net basis of every key, exact (the cost identity reads it)."""

  def fork(self) -> 'Totals':
    """A copy that can change without touching this one."""
    copy = Totals()
    copy.totals = dict(self.totals)
    copy.costs = dict(self.costs)
    copy.cost = self.cost
    return copy

  def change(self, key: LotKey, held: Decimal, now: Decimal, cost: Decimal):
    """A lot under `key` went from `held` to `now` units and its basis changed by `cost`, exactly."""
    self.totals[key] = EXACT.add(self.totals.get(key, ZERO), EXACT.subtract(now, held))
    self.costs[key] = EXACT.add(self.costs.get(key, ZERO), cost)
    self.cost = EXACT.add(self.cost, cost)

  def exact(self, key: LotKey) -> Decimal:
    """The exact sum of a key's lot quantities."""
    return self.totals.get(key, ZERO)
