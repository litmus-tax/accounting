"""
A lot book's records: the working `OpenLot`, what a close consumed and what a
change applied, the HIFO rank, and exact allocation of a lot's origins.
"""

from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from litmus.accounting.model import Lot, Origin
from litmus.accounting.engine.arithmetic import difference


@dataclass
class OpenLot:
  """Mutable working copy of a lot while the book is running."""

  id: str
  asset: str
  compartment: str | None
  quantity: Decimal
  cost: Decimal
  acquired: datetime
  event: str
  origins: tuple[Origin, ...] = ()
  exact_basis: bool = False
  """Rollover ancestry uses exact finite remainders; ordinary lots retain legacy arithmetic."""

  def unit_cost(self) -> Decimal:
    """Absolute basis per unit."""
    return abs(self.cost / self.quantity)

  def freeze(self) -> Lot:
    """Snapshot as an output record."""
    return Lot(
      id=self.id,
      asset=self.asset,
      compartment=self.compartment,
      quantity=self.quantity,
      cost=self.cost,
      acquired=self.acquired,
      event=self.event,
      origins=self.origins,
    )


@dataclass(frozen=True)
class Consumed:
  """Result of closing part of a position."""

  quantity: Decimal
  """Signed quantity closed (same sign as the change that closed it)."""
  cost: Decimal
  """Basis released, signed like the lots it came from."""
  lots: tuple[str, ...]


@dataclass(frozen=True)
class Applied:
  """Result of `LotBook.apply`: what was closed and what was opened."""

  closed: Consumed
  opened: OpenLot | None
  position: Decimal
  """Net position under the key after the change, exact: zero when the quantity net applied."""


Rank = tuple[Decimal, datetime, int]
"""HIFO consumption order: highest unit cost first, then oldest, then first opened."""


def rank(lot: OpenLot) -> Rank:
  """A lot's HIFO rank: the key the engine has always sorted by, with its stable ties made explicit."""
  return (-lot.unit_cost(), lot.acquired, int(lot.id.removeprefix('lot-')))


def sign(x: Decimal) -> int:
  """-1, 0 or 1."""
  return (x > 0) - (x < 0)


def split_origins(
  origins: tuple[Origin, ...], fraction: Decimal, total: Decimal
) -> tuple[Origin, ...]:
  """Allocate ancestry exactly, giving arithmetic residue to the last origin."""
  allocated: list[Origin] = []
  remaining = total
  for index, origin in enumerate(origins):
    cost = remaining if index == len(origins) - 1 else origin.cost * fraction
    allocated.append(replace(origin, cost=cost))
    remaining = difference(remaining, cost)
  return tuple(allocated)
