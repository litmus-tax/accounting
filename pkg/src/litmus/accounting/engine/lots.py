"""
Lot book: holds open lots per key and applies signed quantity changes.

All cost methods share the sign-crossing logic (a change that opposes the
current position closes it first and opens the remainder on the other side).
They differ only in the order a close consumes lots: FIFO oldest first, LIFO
newest first, HIFO highest unit cost first, or proportionally out of a single
pool for average cost.

Quantities are exact (policy 05 rule 20.3): a lot book only adds, subtracts
and compares them, and finite decimals are closed under those, so every
quantity operation runs without rounding (`difference`, `EXACT`, `copy_abs`,
`copy_negate`). Only basis is divided, and basis is money. The one exception
is the quantity net: a take or an apply that would leave a holding nonzero but
smaller than `NET` treats it as zero and records a `Residue`.
"""

import bisect
import copy
from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from typing_extensions import Iterable
from litmus.accounting.model import CostMethod, Lot, Origin
from litmus.accounting.engine.arithmetic import exact_sum, difference
from litmus.accounting.engine.records import (
  Applied,
  Consumed,
  OpenLot,
  Rank,
  rank,
  sign,
  split_origins,
)
from litmus.accounting.engine.totals import EXACT, ZERO, LotKey, Residue, Totals, crumb


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
    self.residues: list[Residue] = []
    """Holdings the quantity net treated as zero, until the caller reports them."""

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
    """Net signed quantity held under `key`, exact."""
    return self.tally.exact(key)

  def sign(self, key: LotKey) -> int:
    """The sign of `position(key)`."""
    return sign(self.tally.exact(key))

  def fork(self, keys: Iterable[LotKey]) -> 'LotBook':
    """A copy in which the lots of `keys` can change without touching this book."""
    keys = set(keys)
    fork = copy.copy(self)
    fork.lots = dict(self.lots)
    lots: dict[str, OpenLot] = {}
    for key in keys:
      fork.lots[key] = [copy.copy(lot) for lot in self.lots.get(key, [])]
      lots.update((lot.id, lot) for lot in fork.lots[key])
    fork.tally = self.tally.fork()
    fork.residues = list(self.residues)
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
      pool.quantity = EXACT.add(pool.quantity, quantity)
      pool.exact_basis = pool.exact_basis or exact_basis
      basis = pool.cost
      pool.cost = exact_sum((pool.cost, cost)) if pool.exact_basis else pool.cost + cost
      self.tally.change(key, held, pool.quantity, EXACT.subtract(pool.cost, basis))
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
    self.tally.change(key, ZERO, lot.quantity, cost)
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
    removed. Takes less than asked when the key holds less, unless the
    quantity net applies (`net`).
    """
    lots = self.lots.get(key, [])
    start = self.tally.exact(key)
    remaining = quantity.copy_abs()
    after = (
      EXACT.subtract(start, remaining) if start > 0 else EXACT.add(start, remaining)
    )
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
      held = lot.quantity.copy_abs()
      take = min(remaining, held)
      signed = take if lot.quantity > 0 else take.copy_negate()
      share = lot.cost if lot.exact_basis and take == held else lot.cost * take / held
      origins = split_origins(lot.origins, take / held, share) if self.origins else ()
      consumed = replace(lot, quantity=signed, cost=share, origins=origins)
      if self.origins:
        lot.origins = tuple(
          replace(origin, cost=difference(origin.cost, used.cost))
          for origin, used in zip(lot.origins, origins)
        )
      before, basis = lot.quantity, lot.cost
      lot.cost = difference(lot.cost, share) if lot.exact_basis else lot.cost - share
      if self.origins and not lot.exact_basis:
        lot.origins = split_origins(lot.origins, Decimal(1), lot.cost)
      lot.quantity = difference(lot.quantity, signed)
      self.tally.change(key, before, lot.quantity, EXACT.subtract(lot.cost, basis))
      out.append((consumed, take, share))
      remaining = difference(remaining, take)
    netted = bool(out) and start != 0 and crumb(after)
    if netted:
      out[-1] = self.net(key, out[-1], remaining, walked)
      self.residues.append(Residue(key, after))
    # Only the lots this take walked can have emptied: a prefix under FIFO, a
    # suffix under LIFO. The net can empty any lot, so it filters them all.
    for lot, old in walked:
      self.rank(key, lot, old)
    touched = len(out)
    if touched and not selected and not netted and self.method == 'fifo':
      lots[:touched] = [lot for lot in lots[:touched] if lot.quantity != 0]
    elif touched and not selected and not netted and self.method == 'lifo':
      start_at = len(lots) - touched
      lots[start_at:] = [lot for lot in lots[start_at:] if lot.quantity != 0]
    elif touched:
      lots[:] = [lot for lot in lots if lot.quantity != 0]
    return out

  def net(
    self,
    key: LotKey,
    last: tuple[OpenLot, Decimal, Decimal],
    remaining: Decimal,
    walked: list[tuple[OpenLot, Rank]],
  ) -> tuple[OpenLot, Decimal, Decimal]:
    """
    Treat as zero the crumb a take would leave under `key` (policy 05 rule
    20.3), and return the take's last piece with it. Asked a crumb more than
    the key held, the last piece is taken as asked: the shortfall is discarded.
    Asked a crumb less, the lots left go with the last piece: their quantity is
    discarded and their basis released with it, so no basis is lost.
    """
    consumed, take, share = last
    if remaining:
      extra = remaining if consumed.quantity > 0 else remaining.copy_negate()
      return (
        replace(consumed, quantity=EXACT.add(consumed.quantity, extra)),
        EXACT.add(take, remaining),
        share,
      )
    seen = {lot.id for lot, _ in walked}
    for lot in self.lots.get(key, []):
      if lot.quantity == 0:
        continue
      if self.method == 'hifo' and lot.id not in seen:
        walked.append((lot, rank(lot)))
      share = exact_sum((share, lot.cost))
      if lot.id == consumed.id and len(lot.origins) == len(consumed.origins):
        origins = tuple(
          replace(used, cost=exact_sum((used.cost, left.cost)))
          for used, left in zip(consumed.origins, lot.origins)
        )
      else:
        origins = consumed.origins + lot.origins
      consumed = replace(consumed, cost=share, origins=origins)
      self.tally.change(key, lot.quantity, ZERO, lot.cost.copy_negate())
      lot.quantity, lot.cost, lot.origins = ZERO, ZERO, ()
    return consumed, take, share

  def close(self, key: LotKey, quantity: Decimal) -> Consumed:
    """
    Close `quantity` (opposite sign to the position) against open lots. The
    caller guarantees `|quantity| <= |position|`, or that the excess is a crumb
    the quantity net discards.
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
    the proportional share of `value`. A change that would leave a crumb on
    either side closes the whole position instead (the quantity net, `take`).
    """
    closed = Consumed(quantity=Decimal(0), cost=Decimal(0), lots=())
    opened: OpenLot | None = None
    total = self.tally.exact(key)
    after = EXACT.add(total, quantity)
    residues = len(self.residues)
    open_qty = quantity
    if sign(total) == -sign(quantity):
      if total.copy_abs() >= quantity.copy_abs() or crumb(after):
        closed = self.close(key, quantity)
        open_qty = ZERO
      else:
        # The lots' own sum, not the running total, so the closed quantity is
        # written as the lots write it.
        held = exact_sum(lot.quantity for lot in self.lots.get(key, []))
        closed = self.close(key, held.copy_negate())
        open_qty = difference(quantity, closed.quantity)
    if open_qty != 0:
      open_value = value * open_qty / quantity
      opened = self.open(
        key, quantity=open_qty, cost=open_value, time=time, event=event
      )
    netted = len(self.residues) > residues
    return Applied(closed=closed, opened=opened, position=ZERO if netted else after)

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
    moved = [take if quantity > 0 else take.copy_negate() for _, take, _ in taken]
    created = [
      self.open(
        dst,
        quantity=signed,
        cost=share,
        time=lot.acquired,
        event=lot.event,
        origins=lot.origins,
        exact_basis=lot.exact_basis,
      )
      for (lot, _, share), signed in zip(taken, moved)
    ]
    return (
      Consumed(
        quantity=exact_sum(moved),
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
