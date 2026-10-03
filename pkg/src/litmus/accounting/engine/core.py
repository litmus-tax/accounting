"""
The engine's state and the primitives every booking uses: lot keys, the
exceptions report, market values, applying a leg to the lot book, the
liability ledger with the realization of its repaid side, and the shortness
checks run after each atomic group (policy 05 rule 6.3).
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing_extensions import Literal

from litmus.accounting.engine.journal import Journal
from litmus.accounting.engine.lots import LotBook
from litmus.accounting.engine.records import Applied
from litmus.accounting.engine.totals import ZERO, LotKey
from litmus.accounting.engine.perps import PositionBook
from litmus.accounting.model import (
  Event,
  ExceptionCode,
  ExceptionItem,
  Flow,
  Leg,
  Liability,
  Move,
  Policy,
  Realized,
  RolloverRecord,
)
from litmus.accounting.pricing import PriceGap, Pricing, Valuer


@dataclass
class OpenLiability:
  """Mutable working copy of a liability while the engine is running."""

  id: str
  asset: str
  compartment: str
  quantity: Decimal
  cost: Decimal
  label: str | None
  opened: datetime
  updated: datetime
  event: str

  def freeze(self) -> Liability:
    """Snapshot as an output record."""
    return Liability(
      id=self.id,
      asset=self.asset,
      compartment=self.compartment,
      quantity=self.quantity,
      cost=self.cost,
      label=self.label,
      opened=self.opened,
      updated=self.updated,
      event=self.event,
    )


Watch = tuple[Literal['key', 'scope', 'liability'], tuple[str | None, str]]
"""What a shortness check reads: a lot key's position, a `(compartment, asset)` scope position, or a liability."""


@dataclass(frozen=True)
class Finding:
  """The item a shortness check reports when what it watches is still short after the group."""

  code: ExceptionCode
  event: str
  message: str
  """The message, with `{position}` standing for the position after the group."""
  detail: dict[str, str]


class Core:
  """One run's mutable state and its booking primitives."""

  def __init__(self, policy: Policy, pricing: Pricing, *, strict: bool = False):
    self.policy = policy
    self.strict = strict
    self.fc = policy.functional_currency
    self.valuer = Valuer(pricing, policy)
    self.book = LotBook(policy.cost_method, origins=policy.cost_method != 'average')
    """Under `average` a pool is quantity and total cost only: no origins (policy 05 rule 18.2)."""
    self.liabilities: dict[tuple[str, str], OpenLiability] = {}
    self.realized: list[Realized] = []
    self.flows: list[Flow] = []
    self.moves: list[Move] = []
    self.rollovers: list[RolloverRecord] = []
    self.scope_positions: dict[tuple[str, str], Decimal] = {}
    self.notional_scopes = {
      (scope.compartment, scope.asset) for scope in policy.notional_scopes
    }
    self.exceptions: list[ExceptionItem] = []
    self.booked_links: set[tuple[str, str]] = set()
    self.complete = True
    self.cash = set(policy.cash)
    self.positions = set(policy.position_assets)
    self.perps = PositionBook(policy.perp_cost_method)
    self.earned: set[str] = set()
    """Events whose income legs are booked (policy 05 rule 14.3)."""
    self.journal = Journal()
    self.perp_compartments: set[str] = set()
    """Compartments holding perpetual positions: a settlement asset there never goes below zero (rule 8.6)."""
    self.watched: dict[Watch, Finding] = {}
    """Holdings and liabilities the current group may have left short, checked when it closes."""
    self.opening: dict[LotKey, Decimal] | None = None
    """Each lot key's position when the current group of several events opened; `None` for a group of one."""
    self.opaque = set(policy.opaque_compartments)
    """Compartments booked as one position by value (policy 05 rule 14)."""
    self.contents: dict[str, dict[str, Decimal]] = {}
    """Per opaque compartment, its event-implied contents per asset: the sum of its legs so far (rule 14.5)."""

  def key(self, leg: Leg) -> LotKey:
    """Lot key for a leg under the policy's lot scope."""
    return (
      leg.compartment
      if self.policy.lot_scope == 'compartment'
      or (leg.compartment, leg.asset) in self.notional_scopes
      else None,
      leg.asset,
    )

  def report(self, code: ExceptionCode, event: str | None, message: str, **detail: str):
    """Append to the exceptions report."""
    self.exceptions.append(ExceptionItem(code, event, message, detail))

  def open_group(self, size: int):
    """
    Start an atomic group of `size` events (policy 05 rule 6.3): shortness is
    checked when it closes, and a holding short when it opened stays short to a
    rollover output (`short_before`).
    """
    self.watched = {}
    self.opening = dict(self.book.totals) if size > 1 else None

  def short_before(self, key: LotKey) -> bool:
    """Whether the lot key was short when the current group opened; for a group of one, whether it is short now."""
    if self.opening is None:
      return self.book.sign(key) < 0
    return self.opening.get(key, ZERO) < 0

  def watch(self, watch: Watch, finding: Finding):
    """Check `watch` for shortness when the group closes; the latest finding names it."""
    self.watched.pop(watch, None)
    self.watched[watch] = finding

  def close_group(self):
    """
    Report what the group left short (policy 05 rule 6.3): each watched holding
    outside the position assets and notional scopes whose position is negative,
    and each watched liability that is negative, once per group.
    """
    for (kind, scope), finding in self.watched.items():
      if kind == 'key':
        position = self.book.position(scope)
      elif kind == 'scope':
        position = self.scope_positions.get((str(scope[0]), scope[1]), ZERO)
      else:
        liability = self.liabilities.get((str(scope[0]), scope[1]))
        position = liability.quantity if liability else ZERO
      if position < 0:
        self.report(
          finding.code,
          finding.event,
          finding.message.replace('{position}', str(position)),
          **{
            **finding.detail,
            **({} if kind == 'liability' else {'position': str(position)}),
          },
        )
    self.watched = {}
    self.opening = None

  def residues(self, event: Event):
    """
    Report each holding the quantity net treated as zero while `event` was
    booked (policy 05 rule 20.3), so that new crumbs stay visible. The items
    are informational: the run stays complete.
    """
    for book in (self.book, *self.perps.books.values()):
      for residue in book.residues:
        compartment, asset = residue.key
        if compartment is None:
          compartment = next(
            (leg.compartment for leg in event.legs if leg.asset == asset), ''
          )
        self.report(
          'quantity_residue',
          event.id,
          f'{asset} in {compartment}: a holding of {residue.quantity} was treated '
          'as zero (quantity net, policy 05 rule 20.3)',
          asset=asset,
          compartment=compartment,
          residue=str(residue.quantity),
        )
      book.residues.clear()

  def unbooked(self, event: Event, leg: Leg, reason: str):
    """Record that a leg was left out of the books; the run is then incomplete."""
    self.complete = False
    self.report(
      'unbooked',
      event.id,
      f'{leg.quantity} {leg.asset} in {leg.compartment} not booked: {reason}',
      asset=leg.asset,
      compartment=leg.compartment,
      quantity=str(leg.quantity),
    )

  def gap(self, event: Event, e: PriceGap):
    """Report a price gap, or raise it in strict mode."""
    if self.strict:
      raise e
    self.report(
      'price_gap',
      event.id,
      str(e),
      asset=e.asset,
      quote=e.quote,
      time=e.time.isoformat(),
    )

  def market(self, event: Event, leg: Leg) -> Decimal:
    """Market value of a leg, signed like its quantity."""
    return self.valuer.value(leg.asset, leg.quantity, event.time)

  def lots(
    self,
    event: Event,
    leg: Leg,
    value: Decimal,
    *,
    fees: Decimal = Decimal(0),
    acquired: datetime | None = None,
  ) -> Applied:
    """
    Apply a leg to the lot book and record the realized row of what it closed.
    Its journal lines move the holding by `value` less what was realized.
    """
    compartment = self.key(leg)[0]
    applied = self.book.apply(
      self.key(leg),
      quantity=leg.quantity,
      value=value,
      time=acquired or event.time,
      event=event.id,
    )
    closed = applied.closed
    opened = value
    if closed.quantity != 0:
      share = closed.quantity / leg.quantity
      proceeds = -value * share
      opened = value + proceeds
      index = len(self.realized)
      self.journal.add(
        event,
        'holding',
        -closed.cost,
        compartment=compartment,
        asset=leg.asset,
        ref=('realized_cost', index),
      )
      self.journal.add(
        event,
        'realized',
        closed.cost - proceeds,
        compartment=leg.compartment,
        asset=leg.asset,
        ref=('realized_pnl', index),
      )
      self.realized.append(
        Realized(
          event=event.id,
          time=event.time,
          asset=leg.asset,
          compartment=leg.compartment,
          quantity=closed.quantity,
          proceeds=proceeds,
          cost=closed.cost,
          pnl=proceeds - closed.cost,
          lots=closed.lots,
          fees=fees * share,
        )
      )
    if applied.opened is not None:
      self.journal.add(
        event, 'holding', opened, compartment=compartment, asset=leg.asset
      )
    return applied

  def liability(self, event: Event, leg: Leg) -> OpenLiability:
    """
    The liability of the leg's facility, opened now if there is none: keyed by
    `(liability compartment, asset)` (policy 05 rule 9.1).
    """
    compartment = leg.liability or leg.compartment
    key = (compartment, leg.asset)
    if key not in self.liabilities:
      self.liabilities[key] = OpenLiability(
        id=f'liability-{len(self.liabilities) + 1}',
        asset=leg.asset,
        compartment=compartment,
        quantity=Decimal(0),
        cost=Decimal(0),
        label=leg.label,
        opened=event.time,
        updated=event.time,
        event=event.id,
      )
    return self.liabilities[key]

  def realize_liability(
    self,
    event: Event,
    leg: Leg,
    liability: OpenLiability,
    *,
    given: Decimal,
    released: Decimal,
  ):
    """
    The liability side of a repayment (policy 05 rule 9.4): `given` (negative)
    is the market value given up, `released` the basis the liability released;
    their sum is realized.
    """
    index = len(self.realized)
    self.journal.add(
      event,
      'liability',
      released,
      compartment=liability.compartment,
      asset=leg.asset,
      ref=('realized_cost', index),
    )
    self.journal.add(
      event,
      'realized',
      -(given + released),
      compartment=liability.compartment,
      asset=leg.asset,
      ref=('realized_pnl', index),
    )
    self.realized.append(
      Realized(
        event=event.id,
        time=event.time,
        asset=leg.asset,
        compartment=liability.compartment,
        quantity=abs(leg.quantity),
        proceeds=given,
        cost=-released,
        pnl=given + released,
        lots=(liability.id,),
      )
    )

  def owed(self, scope: tuple[str, str]) -> Decimal:
    """What is owed under a `(compartment, asset)` liability, zero when nothing is."""
    liability = self.liabilities.get(scope)
    return max(liability.quantity, Decimal(0)) if liability else Decimal(0)
