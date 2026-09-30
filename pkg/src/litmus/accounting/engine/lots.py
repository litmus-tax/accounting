"""
Lot book: holds open lots per key and applies signed quantity changes.

All cost methods share the sign-crossing logic (a change that opposes the
current position closes it first and opens the remainder on the other side).
They differ only in the order a close consumes lots: FIFO oldest first, LIFO
newest first, HIFO highest unit cost first, or proportionally out of a single
pool for average cost.
"""

import bisect
import copy
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from typing_extensions import Iterable
from litmus.accounting.model import CostMethod, Lot, Origin
from litmus.accounting.engine.arithmetic import exact_sum, difference
from litmus.accounting.engine.totals import EXACT, LotKey, Totals


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
  """
  Net position under the key after the change. Exact only when the book has
  `report_position` set; otherwise an unrounded stand-in the caller must not read.
  """


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


class LotBook:
  """Open lots per key, under one cost method."""

  def __init__(self, method: CostMethod, *, origins: bool = True):
    self.method = method
    self.origins = origins
    """Track `Lot.origins`; off for books whose lots are never output (open positions)."""
    self.lots: dict[LotKey, list[OpenLot]] = {}
    """Open lots per key, in acquisition order (ties in insertion order)."""
    self.ranked: dict[LotKey, list[tuple[Rank, OpenLot]]] = {}
    """HIFO only: a key's lots in consumption order, by `rank`."""
    self.tally = Totals()
    """Per-key running totals: the position without summing lots."""
    self.counter = 0
    self.report_position = True
    """Whether callers read `Applied.position`; when not, `apply` can skip the context sum."""

  def next_id(self) -> str:
    """Sequential lot id."""
    self.counter += 1
    return f'lot-{self.counter}'

  @property
  def totals(self) -> dict[LotKey, Decimal]:
    """Exact net quantity per key."""
    return self.tally.totals

  @property
  def costs(self) -> dict[LotKey, Decimal]:
    """Exact net basis per key."""
    return self.tally.costs

  def position(self, key: LotKey) -> Decimal:
    """
    Net signed quantity held under `key`: the sum of its lots' quantities in the
    order they were opened, in the current decimal context, as the engine has
    always taken it (see `engine.totals`).
    """
    return self.tally.position(key)

  def sign(self, key: LotKey) -> int:
    """The sign of `position(key)`."""
    return self.tally.sign(key)

  def fork(self, keys: Iterable[LotKey]) -> 'LotBook':
    """A copy in which the lots of `keys` can change without touching this book."""
    keys = set(keys)
    fork = copy.copy(self)
    fork.lots = dict(self.lots)
    lots: dict[str, OpenLot] = {}
    for key in keys:
      fork.lots[key] = [copy.copy(lot) for lot in self.lots.get(key, [])]
      lots.update((lot.id, lot) for lot in fork.lots[key])
    fork.tally = self.tally.fork(keys, dict(lots))
    fork.ranked = dict(self.ranked)
    for key in keys:
      if key in self.ranked:
        fork.ranked[key] = [(r, lots[lot.id]) for r, lot in self.ranked[key]]
    return fork

  def order(self, lots: list[OpenLot]) -> Iterable[OpenLot]:
    """
    Lots in the order the cost method consumes them: FIFO and LIFO by acquisition
    time (ties in insertion order, LIFO reversed), so lots recreated by a move
    keep their place; HIFO by `rank`. Both orders are kept, never sorted here.
    """
    if self.method == 'lifo':
      return reversed(lots)
    if self.method == 'hifo' and lots:
      key = (lots[0].compartment, lots[0].asset)
      return (lot for _, lot in self.ranked.get(key, []))
    return lots

  def rank(self, key: LotKey, lot: OpenLot, old: Rank | None):
    """Keep a HIFO lot's place in `ranked` after it opened or changed (`old`: its previous rank)."""
    ranked = self.ranked.setdefault(key, [])
    if old is not None:
      del ranked[bisect.bisect_left(ranked, (old,), key=lambda entry: entry[:1])]
    if lot.quantity != 0:
      bisect.insort(ranked, (rank(lot), lot), key=lambda entry: entry[0])

  def open(
    self,
    key: LotKey,
    *,
    quantity: Decimal,
    cost: Decimal,
    time: datetime,
    event: str,
    origins: tuple[Origin, ...] | None = None,
    exact_basis: bool = False,
  ) -> OpenLot:
    """
    Open a lot. Under average cost it is merged into the key's single pool, which
    keeps the pool's original `acquired` and `event`.
    """
    if not self.origins:
      origins = ()
    elif origins is None:
      origins = (Origin(event, time, cost),)
    lots = self.lots.setdefault(key, [])
    if self.method == 'average' and lots:
      pool = lots[0]
      held = pool.quantity
      pool.quantity += quantity
      pool.exact_basis = pool.exact_basis or exact_basis
      basis = pool.cost
      pool.cost = exact_sum((pool.cost, cost)) if pool.exact_basis else pool.cost + cost
      self.tally.changes(key, pool, held, EXACT.subtract(pool.cost, basis))
      pool.origins += origins
      if self.origins and not pool.exact_basis:
        pool.origins = split_origins(pool.origins, Decimal(1), pool.cost)
      return pool
    lot = OpenLot(
      id=self.next_id(),
      asset=key[1],
      compartment=key[0],
      quantity=quantity,
      cost=cost,
      acquired=time,
      event=event,
      origins=origins,
      exact_basis=exact_basis,
    )
    self.tally.opens(key, lot, cost)
    if self.method == 'hifo':
      self.rank(key, lot, None)
    if lots and lots[-1].acquired > time:
      bisect.insort_right(lots, lot, key=lambda lot: lot.acquired)
    else:
      lots.append(lot)
    return lot

  def take(
    self,
    key: LotKey,
    quantity: Decimal,
    *,
    selected: tuple[str, ...] = (),
    exact_basis: bool = False,
  ) -> list[tuple[OpenLot, Decimal, Decimal]]:
    """
    Carve `|quantity|` out of the lots under `key` in cost-method order. Returns
    `(lot, quantity taken, basis released)` per lot touched; emptied lots are
    removed. Takes less than asked when the key holds less.
    """
    lots = self.lots.get(key, [])
    remaining = abs(quantity)
    out: list[tuple[OpenLot, Decimal, Decimal]] = []
    ordered = (
      self.order(lots)
      if not selected
      else [next(l for l in lots if l.id == i) for i in selected]
    )
    walked: list[tuple[OpenLot, Rank]] = []
    for lot in ordered:
      if remaining == 0:
        break
      if self.method == 'hifo':
        walked.append((lot, rank(lot)))
      lot.exact_basis = lot.exact_basis or exact_basis
      take = min(remaining, abs(lot.quantity))
      share = (
        lot.cost
        if lot.exact_basis and take == abs(lot.quantity)
        else lot.cost * take / abs(lot.quantity)
      )
      origins = (
        split_origins(lot.origins, take / abs(lot.quantity), share)
        if self.origins
        else ()
      )
      consumed = replace(
        lot, quantity=take * sign(lot.quantity), cost=share, origins=origins
      )
      if self.origins:
        lot.origins = tuple(
          replace(origin, cost=difference(origin.cost, used.cost))
          for origin, used in zip(lot.origins, origins)
        )
      held, basis = lot.quantity, lot.cost
      lot.cost = difference(lot.cost, share) if lot.exact_basis else lot.cost - share
      if self.origins and not lot.exact_basis:
        lot.origins = split_origins(lot.origins, Decimal(1), lot.cost)
      lot.quantity -= take * sign(lot.quantity)
      self.tally.changes(key, lot, held, EXACT.subtract(lot.cost, basis))
      out.append((consumed, take, share))
      remaining -= take
    # Only the lots this take walked can have emptied: a prefix under FIFO, a
    # suffix under LIFO. Rounding can leave a walked lot with a residue while a
    # later one empties, so emptied lots are dropped by value, never by count.
    for lot, old in walked:
      self.rank(key, lot, old)
    touched = len(out)
    if touched and not selected and self.method == 'fifo':
      lots[:touched] = [lot for lot in lots[:touched] if lot.quantity != 0]
    elif touched and not selected and self.method == 'lifo':
      start = len(lots) - touched
      lots[start:] = [lot for lot in lots[start:] if lot.quantity != 0]
    elif touched:
      lots[:] = [lot for lot in lots if lot.quantity != 0]
    return out

  def close(self, key: LotKey, quantity: Decimal) -> Consumed:
    """
    Close `quantity` (opposite sign to the position) against open lots. The
    caller guarantees `|quantity| <= |position|`.
    """
    taken = self.take(key, quantity)
    return Consumed(
      quantity=quantity,
      cost=exact_sum(share for _, _, share in taken)
      if any(lot.exact_basis for lot, _, _ in taken)
      else sum((share for _, _, share in taken), Decimal(0)),
      lots=tuple(lot.id for lot, _, _ in taken),
    )

  def apply(
    self, key: LotKey, *, quantity: Decimal, value: Decimal, time: datetime, event: str
  ) -> Applied:
    """
    Apply a signed `quantity` change worth `value` (signed like `quantity`). The
    part opposing the current position is closed; the rest opens a new lot at
    the proportional share of `value`.
    """
    closed = Consumed(quantity=Decimal(0), cost=Decimal(0), lots=())
    opened: OpenLot | None = None
    close_qty = Decimal(0)
    pos = self.tally.fast(key)
    total = self.tally.exact(key)
    if pos is None and not self.report_position and self.tally.clear(key, quantity):
      # The context sum is on the same side as the exact total and larger than
      # `quantity`, so the decision below is the one it would give.
      pos = total
      if sign(total) == -sign(quantity):
        close_qty = abs(quantity) * sign(quantity)
        closed = self.close(key, close_qty)
    else:
      if pos is None:
        pos = self.position(key)
      if sign(pos) == -sign(quantity):
        close_qty = min(abs(pos), abs(quantity)) * sign(quantity)
        closed = self.close(key, close_qty)
    open_qty = quantity - close_qty
    if open_qty != 0:
      open_value = value * open_qty / quantity
      opened = self.open(
        key, quantity=open_qty, cost=open_value, time=time, event=event
      )
    return Applied(closed=closed, opened=opened, position=pos + quantity)

  def move(
    self, src: LotKey, dst: LotKey, quantity: Decimal
  ) -> tuple[Consumed, list[OpenLot]]:
    """
    Move up to `quantity` (signed like the source position) from `src` to `dst`
    at carried basis, taking lots in cost-method order. FIFO/LIFO/HIFO carve lots
    and keep their acquisition time; average moves a proportional share of the
    pool. `taken.quantity` is what actually moved, which is less than asked when
    the source holds less.
    """
    taken = self.take(src, quantity)
    created = [
      self.open(
        dst,
        quantity=take * sign(quantity),
        cost=share,
        time=lot.acquired,
        event=lot.event,
        origins=lot.origins,
        exact_basis=lot.exact_basis,
      )
      for lot, take, share in taken
    ]
    return (
      Consumed(
        quantity=sum((take * sign(quantity) for _, take, _ in taken), Decimal(0)),
        cost=exact_sum(share for _, _, share in taken)
        if any(lot.exact_basis for lot, _, _ in taken)
        else sum((share for _, _, share in taken), Decimal(0)),
        lots=tuple(lot.id for lot, _, _ in taken),
      ),
      created,
    )

  def open_lots(self) -> list[Lot]:
    """All open lots, by key then acquisition time."""
    out: list[Lot] = []
    for key in sorted(self.lots, key=str):
      out.extend(
        lot.freeze()
        for lot in sorted(self.lots[key], key=lambda lot: lot.acquired)
        if lot.quantity != 0
      )
    return out
