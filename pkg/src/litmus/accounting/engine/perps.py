"""
Open perpetual positions on a settled basis (policy 05 rule 8).

A fill's `position` size leg changes an open position per `(compartment,
instrument)`; the position books nothing. Its entry basis is kept in the
settlement asset: on `notional` compartments the fill price is the fill's
`notional` cash leg divided by its size, and a reduction realizes P&L under the
policy's `perp_cost_method`; on `pnl` compartments the size leg carries the
venue's fill price and the position is kept at average entry, while the venue's
own `realized_pnl` legs are the booked result.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing_extensions import Literal
from litmus.accounting.model import CostMethod, Event, Leg, PerpPosition
from litmus.accounting.engine.lots import LotBook

Settlement = Literal['notional', 'pnl']
"""The compartment's settlement convention, as the fill's legs show it."""
PositionKey = tuple[str, str]
"""`(compartment, instrument)`."""


@dataclass(frozen=True)
class Fill:
  """A size leg paired with its price and settlement asset."""

  leg: Leg
  price: Decimal
  """Settlement-asset units per unit of the instrument."""
  settles_in: str
  settlement: Settlement


def fills(event: Event) -> tuple[list[Fill], list[tuple[Leg, str]]]:
  """
  Pair each `position` leg of an event with its price (policy 05 rules 8.2, 8.3).

  Returns:
    The fills, and the `position` and `notional` legs that cannot be read, each
    with the reason (rule 8.7: never priced at zero).
  """
  sizes: dict[str, list[Leg]] = {}
  cash: dict[str, list[Leg]] = {}
  for leg in event.legs:
    if leg.tag == 'position':
      sizes.setdefault(leg.compartment, []).append(leg)
    elif leg.tag == 'notional':
      cash.setdefault(leg.compartment, []).append(leg)
  found: list[Fill] = []
  bad: list[tuple[Leg, str]] = []
  for compartment in sorted(sizes.keys() | cash.keys()):
    size, notional = sizes.get(compartment, []), cash.get(compartment, [])
    if notional:
      if (
        len(size) == 1
        and len(notional) == 1
        and size[0].price is None
        and size[0].quantity * notional[0].quantity < 0
      ):
        price = -notional[0].quantity / size[0].quantity
        found.append(Fill(size[0], price, notional[0].asset, 'notional'))
      else:
        reason = (
          'a notional fill needs one size leg without a price and one notional leg '
          'of the opposite sign in its compartment'
        )
        bad.extend((leg, reason) for leg in (*size, *notional))
      continue
    for leg in size:
      if leg.price is None or leg.settles_in is None:
        bad.append((leg, 'fill price missing: no notional leg and no price'))
      else:
        found.append(Fill(leg, leg.price, leg.settles_in, 'pnl'))
  return found, bad


class PositionBook:
  """Open positions per `(compartment, instrument)`, with entry basis in the settlement asset."""

  def __init__(self, perp_cost_method: CostMethod):
    self.books: dict[Settlement, LotBook] = {
      'notional': LotBook(perp_cost_method, origins=False),
      'pnl': LotBook('average', origins=False),
    }
    self.terms: dict[PositionKey, tuple[Settlement, str]] = {}

  def conflict(self, fill: Fill) -> bool:
    """Whether the fill's convention or settlement asset differs from its position's."""
    key = (fill.leg.compartment, fill.leg.asset)
    known = self.terms.get(key)
    return known is not None and known != (fill.settlement, fill.settles_in)

  def apply(self, fill: Fill, *, time: datetime, event: str) -> Decimal:
    """
    Apply a fill to its position.

    Returns:
      The P&L realized by the part of the fill that reduces the position, in
      settlement-asset units: `proceeds - entry basis released`. Zero when the
      fill only adds to the position.
    """
    key = (fill.leg.compartment, fill.leg.asset)
    self.terms[key] = (fill.settlement, fill.settles_in)
    quantity = fill.leg.quantity
    value = quantity * fill.price
    applied = self.books[fill.settlement].apply(
      (fill.leg.compartment, fill.leg.asset),
      quantity=quantity,
      value=value,
      time=time,
      event=event,
    )
    closed = applied.closed
    if closed.quantity == 0:
      return Decimal(0)
    proceeds = -value * closed.quantity / quantity
    return proceeds - closed.cost

  def open_positions(self) -> list[PerpPosition]:
    """Every open position, by compartment then instrument."""
    out: list[PerpPosition] = []
    for (compartment, instrument), (settlement, settles_in) in sorted(
      self.terms.items()
    ):
      book = self.books[settlement]
      lots = book.lots.get((compartment, instrument), [])
      size = book.position((compartment, instrument))
      if size == 0:
        continue
      entry = sum((lot.cost for lot in lots), Decimal(0))
      out.append(
        PerpPosition(
          compartment=compartment,
          instrument=instrument,
          settles_in=settles_in,
          settlement=settlement,
          size=size,
          entry=entry,
          opened=min(lot.acquired for lot in lots),
        )
      )
    return out
