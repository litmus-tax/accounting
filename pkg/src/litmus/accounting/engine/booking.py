"""
Booking a leg into lots, with settlement shortfalls in perpetual compartments
(policy 05 rule 8.6), income and expense flows, trades and perpetual fills.
"""

from dataclasses import replace
from datetime import datetime
from decimal import Decimal

from litmus.accounting.engine.core import Core, Finding
from litmus.accounting.engine.perps import fills
from litmus.accounting.engine.totals import EXACT
from litmus.accounting.model import (
  Event,
  Flow,
  Leg,
)
from litmus.accounting.pricing import PriceGap
from typing_extensions import Literal


class Booking(Core):
  """Legs into lots, flows, trades and perpetual fills."""

  def book_leg(
    self,
    event: Event,
    leg: Leg,
    value: Decimal,
    *,
    fees: Decimal = Decimal(0),
    acquired: datetime | None = None,
  ):
    """
    Book a leg worth `value` (signed like its quantity) into lots, recording the
    realized part. `fees` is the fee value already netted out of `value` under
    `fee_treatment: capitalize`, reported on the realized row. Functional-currency
    legs carry no lots. In a perpetual compartment an asset never goes below
    zero: the shortfall is a liability (policy 05 rule 8.6). Elsewhere the
    holding is checked for shortness when the atomic group closes (rule 6.3).
    """
    if leg.asset == self.fc:
      self.journal.add(
        event, 'holding', value, compartment=self.key(leg)[0], asset=leg.asset
      )
      return
    if leg.compartment in self.perp_compartments:
      self.settle(event, leg, value, fees=fees)
      return
    self.lots(event, leg, value, fees=fees, acquired=acquired)
    scope = (leg.compartment, leg.asset)
    self.scope_positions[scope] = EXACT.add(
      self.scope_positions.get(scope, Decimal(0)), leg.quantity
    )
    if leg.asset not in self.positions and scope not in self.notional_scopes:
      self.watch(
        ('scope', scope) if self.notional_scopes else ('key', self.key(leg)),
        Finding(
          'negative_position',
          event.id,
          f'{leg.asset} in {leg.compartment} is net short ({{position}}) and '
          + (
            'not an eligible notional scope'
            if self.notional_scopes
            else 'not a position asset'
          ),
          {'asset': leg.asset, 'compartment': leg.compartment},
        ),
      )

  def settle(
    self, event: Event, leg: Leg, value: Decimal, *, fees: Decimal = Decimal(0)
  ):
    """
    Book a leg in a perpetual compartment (policy 05 rule 8.6). An outflow beyond
    what the compartment holds is owed in that asset, a liability identified by
    `(compartment, asset)`; an inflow first clears what is owed.
    """
    scope = (leg.compartment, leg.asset)
    held = self.scope_positions.get(scope, Decimal(0))
    if leg.quantity < 0:
      covered = min(-leg.quantity, max(held, Decimal(0)))
      parts = ((-covered, self.lots), (leg.quantity + covered, self.owe))
    else:
      cleared = min(leg.quantity, self.owed(scope))
      parts = ((cleared, self.clear), (leg.quantity - cleared, self.lots))
    for quantity, book in parts:
      if quantity:
        share = quantity / leg.quantity
        book(event, replace(leg, quantity=quantity), value * share, fees=fees * share)
    self.scope_positions[scope] = EXACT.add(held, leg.quantity)

  def owe(self, event: Event, leg: Leg, value: Decimal, *, fees: Decimal = Decimal(0)):
    """A settlement shortfall: the liability grows by the outflow's quantity at its market value."""
    liability = self.liability(event, replace(leg, label='settlement', liability=None))
    liability.quantity -= leg.quantity
    liability.cost -= value
    liability.updated = event.time
    self.journal.add(
      event, 'liability', value, compartment=liability.compartment, asset=leg.asset
    )

  def clear(
    self, event: Event, leg: Leg, value: Decimal, *, fees: Decimal = Decimal(0)
  ):
    """
    An inflow that clears a settlement shortfall: the liability releases its
    basis pro rata and the difference with the inflow's market value is realized
    against it (rule 9.4).
    """
    liability = self.liability(event, leg)
    released = liability.cost * leg.quantity / liability.quantity
    liability.quantity -= leg.quantity
    liability.cost -= released
    liability.updated = event.time
    self.realize_liability(
      event, liability, quantity=leg.quantity, given=-value, released=released
    )

  def flow(self, event: Event, leg: Leg, *, instrument: str | None = None):
    """Book an income or expense leg at market value."""
    try:
      value = self.market(event, leg)
    except PriceGap as e:
      self.gap(event, e)
      self.unbooked(event, leg, 'price gap')
      return
    self.journal.add(
      event,
      'income' if leg.quantity > 0 else 'expense',
      -value,
      compartment=leg.compartment,
      asset=leg.asset,
      label=leg.label,
      ref=('flow', len(self.flows)),
    )
    self.flows.append(
      Flow(
        event=event.id,
        time=event.time,
        asset=leg.asset,
        compartment=leg.compartment,
        quantity=leg.quantity,
        value=abs(value),
        kind='income' if leg.quantity > 0 else 'expense',
        fee=leg.fee,
        label=leg.label,
        instrument=instrument,
      )
    )
    self.book_leg(event, leg, value)

  def pick_side(
    self, given: list[Leg], received: list[Leg]
  ) -> Literal['given', 'received']:
    """Which side fixes the trade's value: functional currency, else cash, else policy."""
    sides: tuple[tuple[Literal['given', 'received'], list[Leg]], ...] = (
      ('given', given),
      ('received', received),
    )
    for side, legs in sides:
      if any(l.asset == self.fc for l in legs):
        return side
    for side, legs in sides:
      if any(l.asset in self.cash for l in legs):
        return side
    return self.policy.trade_valuation

  def trade(self, event: Event, legs: list[Leg], fees: list[Leg]):
    """
    Book the trade legs of one event. The value of the trade is fixed by one side
    and allocated to the other side in proportion to market value. Under
    `fee_treatment: capitalize` the event's fee legs are valued at market and
    folded into the other side: added to basis when it is received, deducted from
    proceeds when it is given. The fee legs themselves still leave their lots.
    """
    given = [l for l in legs if l.quantity < 0]
    received = [l for l in legs if l.quantity > 0]
    if not given or not received:
      for leg in legs:
        self.unbooked(event, leg, 'trade needs legs of both signs')
      for leg in fees:
        self.flow(event, leg)
      return
    side = self.pick_side(given, received)
    fixing = given if side == 'given' else received
    other = received if side == 'given' else given
    try:
      fixing_values = [self.market(event, l) for l in fixing]
      fee_values = [abs(self.market(event, l)) for l in fees]
      total = abs(sum(fixing_values, Decimal(0)))
      fee_total = sum(fee_values, Decimal(0))
      other_total = total + fee_total if other is received else total - fee_total
      if len(other) == 1:
        weights = [Decimal(1)]
      else:
        weights = [abs(self.market(event, l)) for l in other]
    except PriceGap as e:
      self.gap(event, e)
      for leg in [*legs, *fees]:
        self.unbooked(event, leg, 'price gap')
      return
    wsum = sum(weights, Decimal(0))
    for leg, value in zip(fixing, fixing_values):
      self.book_leg(event, leg, value)
    for leg, w in zip(other, weights):
      s = 1 if leg.quantity > 0 else -1
      self.book_leg(
        event,
        leg,
        other_total * w / wsum * s,
        fees=fee_total * w / wsum if other is given else Decimal(0),
      )
    for leg, v in zip(fees, fee_values):
      self.book_leg(event, leg, -v)

  def fill(self, event: Event):
    """
    Apply the event's perpetual fills to their open positions (policy 05 rule 8).
    Nothing is booked, except the P&L a reduction realizes on a `notional`
    compartment: `income` or `expense` in the settlement asset, label
    `realized_pnl` (rule 8.2).
    """
    found, bad = fills(
      replace(
        event,
        legs=tuple(leg for leg in event.legs if leg.compartment not in self.opaque),
      )
    )
    for leg, reason in bad:
      self.unbooked(event, leg, reason)
    for fill in found:
      if self.perps.conflict(fill):
        self.unbooked(
          event,
          fill.leg,
          'fill convention or settlement asset differs from its position',
        )
        continue
      pnl = self.perps.apply(fill, time=event.time, event=event.id)
      if fill.settlement == 'notional' and pnl:
        result = Leg(
          fill.settles_in,
          pnl,
          fill.leg.compartment,
          'income' if pnl > 0 else 'expense',
          label='realized_pnl',
        )
        self.flow(event, result, instrument=fill.leg.asset)
