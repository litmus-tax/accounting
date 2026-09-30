"""
Per-key running totals of a lot book: what `LotBook.position` returns without
summing a key's lots on every call.

The engine has always taken a key's position as the sum of its lots'
quantities in the order they were opened, in the decimal context. These totals
reproduce that sum exactly: from the exact running total whenever no partial
sum can have rounded, from a cached sum while lots are only appended, and by
summing the lots otherwise. Callers that only need a decision (a sign, or
whether a change closes the position) get it from the exact total whenever
the rounding margin cannot change it.
"""

from decimal import (
  Context,
  Decimal,
  MAX_EMAX,
  MAX_PREC,
  MIN_EMIN,
  getcontext,
)
from typing_extensions import Iterable, Protocol

EXACT = Context(prec=MAX_PREC, Emax=MAX_EMAX, Emin=MIN_EMIN)
"""A context in which additions of finite decimals never round."""
ZERO = Decimal(0)
"""The empty sum."""

LotKey = tuple[str | None, str]
"""`(compartment or None, asset)`."""


class Quantity(Protocol):
  """What the totals read of a lot."""

  id: str
  quantity: Decimal


def sign(x: Decimal) -> int:
  """-1, 0 or 1."""
  return (x > 0) - (x < 0)


class Totals:
  """Exact running totals and the old position sum, per key."""

  def __init__(self):
    self.totals: dict[LotKey, Decimal] = {}
    """Net quantity per key, exact."""
    self.sizes: dict[LotKey, Decimal] = {}
    """Sum of the absolute quantities of a key's lots, exact: bounds every partial sum."""
    self.costs: dict[LotKey, Decimal] = {}
    """Net basis per key, exact (series points read it)."""
    self.exponents: dict[LotKey, dict[int, int]] = {}
    """How many of a key's lots have each quantity exponent."""
    self.opened: dict[LotKey, dict[str, Quantity]] = {}
    """A key's lots by id, in the order they were opened."""
    self.sums: dict[LotKey, Decimal] = {}
    """The context sum of a key's lots in opening order, while only lots were appended."""

  def fork(self, keys: Iterable[LotKey], lots: dict[str, Quantity]) -> 'Totals':
    """A copy whose `keys` are independent, their lots replaced by `lots` by id."""
    copy = Totals()
    copy.totals = dict(self.totals)
    copy.sizes = dict(self.sizes)
    copy.costs = dict(self.costs)
    copy.exponents = dict(self.exponents)
    copy.opened = dict(self.opened)
    copy.sums = dict(self.sums)
    for key in keys:
      copy.exponents[key] = dict(self.exponents.get(key, {}))
      copy.opened[key] = {i: lots[i] for i in self.opened.get(key, {})}
    return copy

  def opens(self, key: LotKey, lot: Quantity, cost: Decimal):
    """A new lot at the end of the opening order."""
    self.opened.setdefault(key, {})[lot.id] = lot
    self.change(key, ZERO, lot.quantity, cost)
    if key in self.sums:
      self.sums[key] += lot.quantity

  def changes(self, key: LotKey, lot: Quantity, held: Decimal, cost: Decimal):
    """An existing lot's quantity went from `held` to its current one and its basis by `cost`."""
    self.change(key, held, lot.quantity, cost)
    self.sums.pop(key, None)
    if lot.quantity == 0:
      self.opened.get(key, {}).pop(lot.id, None)

  def change(self, key: LotKey, held: Decimal, now: Decimal, cost: Decimal):
    """Add a quantity change, exactly, to the key's totals and exponent counts."""
    self.totals[key] = EXACT.add(self.totals.get(key, ZERO), EXACT.subtract(now, held))
    self.sizes[key] = EXACT.add(
      self.sizes.get(key, ZERO), EXACT.subtract(now.copy_abs(), held.copy_abs())
    )
    self.costs[key] = EXACT.add(self.costs.get(key, ZERO), cost)
    counts = self.exponents.setdefault(key, {})
    for quantity, step in ((held, -1), (now, 1)):
      if quantity != 0:
        exponent = int(quantity.as_tuple().exponent)
        counts[exponent] = counts.get(exponent, 0) + step
        if not counts[exponent]:
          del counts[exponent]

  def exact(self, key: LotKey) -> Decimal:
    """The exact sum of a key's lot quantities."""
    return self.totals.get(key, ZERO)

  def fast(self, key: LotKey) -> Decimal | None:
    """
    The position from the exact total, when no partial sum of the lots can have
    rounded: every partial sum is at most `sizes` and no finer than the finest
    lot exponent. `None` otherwise.
    """
    size = self.sizes.get(key, ZERO)
    finest = min(self.exponents.get(key, {}), default=0)
    if (size.adjusted() if size else 0) - finest + 1 > getcontext().prec:
      return None
    # The old sum started from Decimal(0): its exponent is the finest of 0 and
    # the lots', and a zero sum is positive.
    exponent = min(0, finest)
    total = self.totals.get(key, ZERO)
    if total == 0:
      return ZERO.scaleb(exponent)
    return total.quantize(Decimal(1).scaleb(exponent))

  def position(self, key: LotKey) -> Decimal:
    """The context sum of a key's lot quantities in the order they were opened."""
    fast = self.fast(key)
    if fast is not None:
      return fast
    if key not in self.sums:
      self.sums[key] = sum(
        (lot.quantity for lot in self.opened.get(key, {}).values()), ZERO
      )
    return self.sums[key]

  def margin(self, key: LotKey) -> Decimal:
    """A bound on how far `position(key)` can be from the exact total."""
    size = self.sizes.get(key, ZERO)
    steps = len(self.opened.get(key, {})) + 1
    return steps * Decimal(1).scaleb(size.adjusted() - getcontext().prec + 1)

  def sign(self, key: LotKey) -> int:
    """The sign of `position(key)`, from the exact total when it is clear of the margin."""
    fast = self.fast(key)
    if fast is not None:
      return sign(fast)
    total = self.exact(key)
    if total.copy_abs() > self.margin(key):
      return sign(total)
    return sign(self.position(key))

  def clear(self, key: LotKey, quantity: Decimal) -> bool:
    """
    Whether the exact total decides a change of `quantity` as the context sum
    would: well clear of zero, and either on the same side or larger than it.
    """
    total = self.exact(key).copy_abs()
    margin = self.margin(key)
    return total > margin and (
      sign(self.exact(key)) == sign(quantity) or quantity.copy_abs() < total - margin
    )
