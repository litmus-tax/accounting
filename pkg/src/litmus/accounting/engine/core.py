"""
The engine's state and the primitives every booking uses: lot keys, the
exceptions report, market values, applying a leg to the lot book, and the
liability ledger with the realization of its repaid side.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from litmus.accounting.engine.journal import Journal
from litmus.accounting.engine.lots import Applied, LotBook, LotKey
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


class Core:
  """One run's mutable state and its booking primitives."""

  def __init__(self, policy: Policy, pricing: Pricing, *, strict: bool = False):
    self.policy = policy
    self.strict = strict
    self.fc = policy.functional_currency
    self.valuer = Valuer(pricing, policy)
    self.book = LotBook(policy.cost_method)
    self.book.report_position = not policy.notional_scopes
    """`Applied.position` is read only without notional scopes (`book_leg`)."""
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
