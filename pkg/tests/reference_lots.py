"""The pre-#17 `LotBook` (543bbfc), kept as a test oracle for the linear lot book; only `origins=`, `sign()`, `fork()` and the running totals (`totals`, `costs`, `cost`) are shims for the current engine's calls. Since accounting 0.10 its quantity arithmetic is exact, as policy 05 rule 20.3 requires: quantities are added, subtracted and compared without rounding. It has no quantity net."""

from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from litmus.accounting.model import CostMethod, Lot, Origin
from litmus.accounting.engine.arithmetic import exact_sum, difference

LotKey = tuple[str | None, str]
"""`(compartment or None, asset)`."""


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
  """Net position under the key after the change."""


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
    self.origins = True
    """Shim: this book always tracks origins, as the engine did before #26."""
    self.lots: dict[LotKey, list[OpenLot]] = {}
    self.counter = 0
    self.residues: list[object] = []
    """Shim for the current engine's call: this book has no quantity net, so it records none."""

  def next_id(self) -> str:
    """Sequential lot id."""
    self.counter += 1
    return f'lot-{self.counter}'

  def fork(self, keys: object) -> 'LotBook':
    """Shim for the current engine's call: the whole-book copy the engine used to make."""
    from copy import deepcopy

    return deepcopy(self)

  def sign(self, key: LotKey) -> int:
    """Shim for the current engine's call: the sign of the position."""
    return sign(self.position(key))

  @property
  def totals(self) -> dict[LotKey, Decimal]:
    """Shim for the current engine's call: net quantity per key."""
    return {key: self.position(key) for key in self.lots}

  @property
  def costs(self) -> dict[LotKey, Decimal]:
    """Shim for the current engine's call: net basis per key."""
    return {key: exact_sum(lot.cost for lot in lots) for key, lots in self.lots.items()}

  @property
  def cost(self) -> Decimal:
    """Shim for the current engine's call: net basis of every key."""
    return exact_sum(lot.cost for lots in self.lots.values() for lot in lots)

  def position(self, key: LotKey) -> Decimal:
    """Net signed quantity held under `key`."""
    return exact_sum(lot.quantity for lot in self.lots.get(key, []))

  def order(self, lots: list[OpenLot]) -> list[OpenLot]:
    """
    Lots in the order the cost method consumes them. FIFO and LIFO go by
    acquisition time (ties in insertion order, LIFO reversed), so lots recreated
    by a move keep their place; HIFO by unit cost, ties oldest first.
    """
    if self.method == 'lifo':
      return sorted(reversed(lots), key=lambda lot: lot.acquired, reverse=True)
    by_age = sorted(lots, key=lambda lot: lot.acquired)
    if self.method == 'hifo':
      return sorted(by_age, key=lambda lot: -lot.unit_cost())
    return by_age

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
    origins = (Origin(event, time, cost),) if origins is None else origins
    lots = self.lots.setdefault(key, [])
    if self.method == 'average' and lots:
      pool = lots[0]
      pool.quantity = exact_sum((pool.quantity, quantity))
      pool.exact_basis = pool.exact_basis or exact_basis
      pool.cost = exact_sum((pool.cost, cost)) if pool.exact_basis else pool.cost + cost
      pool.origins += origins
      if not pool.exact_basis:
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
    remaining = quantity.copy_abs()
    out: list[tuple[OpenLot, Decimal, Decimal]] = []
    ordered = (
      self.order(lots)
      if not selected
      else [next(l for l in lots if l.id == i) for i in selected]
    )
    for lot in ordered:
      if remaining == 0:
        break
      lot.exact_basis = lot.exact_basis or exact_basis
      take = min(remaining, lot.quantity.copy_abs())
      share = (
        lot.cost
        if lot.exact_basis and take == lot.quantity.copy_abs()
        else lot.cost * take / lot.quantity.copy_abs()
      )
      origins = split_origins(lot.origins, take / lot.quantity.copy_abs(), share)
      consumed = replace(
        lot,
        quantity=take if lot.quantity > 0 else take.copy_negate(),
        cost=share,
        origins=origins,
      )
      lot.origins = tuple(
        replace(origin, cost=difference(origin.cost, used.cost))
        for origin, used in zip(lot.origins, origins)
      )
      lot.cost = difference(lot.cost, share) if lot.exact_basis else lot.cost - share
      if not lot.exact_basis:
        lot.origins = split_origins(lot.origins, Decimal(1), lot.cost)
      lot.quantity = difference(lot.quantity, consumed.quantity)
      out.append((consumed, take, share))
      remaining = difference(remaining, take)
      if lot.quantity == 0:
        lots.remove(lot)
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
    pos = self.position(key)
    closed = Consumed(quantity=Decimal(0), cost=Decimal(0), lots=())
    opened: OpenLot | None = None
    close_qty = Decimal(0)
    if sign(pos) == -sign(quantity):
      close_qty = min(pos.copy_abs(), quantity.copy_abs()).copy_sign(quantity)
      closed = self.close(key, close_qty)
    open_qty = difference(quantity, close_qty)
    if open_qty != 0:
      open_value = value * open_qty / quantity
      opened = self.open(
        key, quantity=open_qty, cost=open_value, time=time, event=event
      )
    return Applied(closed=closed, opened=opened, position=exact_sum((pos, quantity)))

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
        quantity=take if quantity > 0 else take.copy_negate(),
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
        quantity=exact_sum(
          take if quantity > 0 else take.copy_negate() for _, take, _ in taken
        ),
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
