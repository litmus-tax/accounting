"""
Liabilities (policy 05 rule 9): borrowing, accrued and reversed interest, and
repayment with the realization of its liability side.
"""

from decimal import Decimal

from litmus.accounting.engine.booking import Booking
from litmus.accounting.engine.core import Finding, OpenLiability
from litmus.accounting.model import (
  Event,
  Flow,
  Leg,
)
from litmus.accounting.pricing import PriceGap


class Debt(Booking):
  """Borrow, interest and repay legs."""

  def interest_flow(
    self, event: Event, leg: Leg, liability: OpenLiability, value: Decimal
  ):
    """
    The interest flow of a noncash accrual (`value` > 0, an expense) or reversal
    (`value` < 0, income) against `liability` (policy 05 rule 9.3.1).
    """
    self.journal.add(
      event,
      'liability',
      -value,
      compartment=liability.compartment,
      asset=leg.asset,
    )
    self.journal.add(
      event,
      'expense' if value > 0 else 'income',
      value,
      compartment=liability.compartment,
      asset=leg.asset,
      label=leg.label,
      ref=('flow', len(self.flows)),
    )
    self.flows.append(
      Flow(
        event=event.id,
        time=event.time,
        asset=leg.asset,
        compartment=liability.compartment,
        quantity=-leg.quantity,
        value=abs(value),
        kind='expense' if value > 0 else 'income',
        fee=False,
        label=leg.label,
      )
    )

  def borrow(self, event: Event, leg: Leg):
    """
    Book a borrow leg: the asset received opens a lot at market value and the
    liability grows by the same quantity and value. Labelled `interest` nothing
    is received: a positive accrual grows debt and is an expense at market value;
    a negative correction releases proportional debt basis as income, without
    cash movement or market realization (policy 05 rule 9.3).
    """
    if leg.label == 'interest' and leg.quantity < 0:
      self.reverse_interest(event, leg)
      return
    try:
      value = self.market(event, leg)
    except PriceGap as e:
      self.gap(event, e)
      self.unbooked(event, leg, 'price gap')
      return
    liability = self.liability(event, leg)
    if leg.label != 'interest':
      self.book_leg(event, leg, value)
      self.journal.add(
        event, 'liability', -value, compartment=liability.compartment, asset=leg.asset
      )
    else:
      self.interest_flow(event, leg, liability, value)
    liability.quantity += leg.quantity
    liability.cost += value
    liability.updated = event.time

  def reverse_interest(self, event: Event, leg: Leg):
    """Reverse evidenced noncash accrual against carried liability basis."""
    liability = self.liability(event, leg)
    reduction = -leg.quantity
    owed = liability.quantity
    released = liability.cost * reduction / owed if owed > 0 else Decimal(0)
    if reduction > owed:
      self.watch(
        ('liability', (liability.compartment, leg.asset)),
        Finding(
          'negative_liability',
          event.id,
          f'interest reversal {reduction} {leg.asset} in {liability.compartment} exceeds {owed} owed',
          {
            'asset': leg.asset,
            'compartment': liability.compartment,
            'owed': str(owed),
            'reversed': str(reduction),
          },
        ),
      )
    liability.quantity -= reduction
    liability.cost -= released
    liability.updated = event.time
    if released:
      self.interest_flow(event, leg, liability, -released)

  def repay(self, event: Event, leg: Leg):
    """
    Book a repay leg: the asset given is a disposal at market with its normal
    PnL, and the liability shrinks by the quantity repaid, releasing a
    proportional share of its basis. The difference between the basis released
    and the market value repaid is realized against the liability, which is
    carried at cost until then (policy 05 rule 9.4). Repaying more than is owed is
    booked anyway, and reported as `negative_liability` when the liability is
    still negative after the atomic group (rule 6.3).
    """
    try:
      value = self.market(event, leg)
    except PriceGap as e:
      self.gap(event, e)
      self.unbooked(event, leg, 'price gap')
      return
    self.book_leg(event, leg, value)
    liability = self.liability(event, leg)
    repaid = -leg.quantity
    owed = liability.quantity
    released = liability.cost * repaid / owed if owed > 0 else Decimal(0)
    if repaid > owed:
      self.watch(
        ('liability', (liability.compartment, leg.asset)),
        Finding(
          'negative_liability',
          event.id,
          f'repaid {repaid} {leg.asset} in {liability.compartment} against {owed} owed',
          {
            'asset': leg.asset,
            'compartment': liability.compartment,
            'owed': str(owed),
            'repaid': str(repaid),
          },
        ),
      )
    liability.quantity -= repaid
    liability.cost -= released
    liability.updated = event.time
    self.realize_liability(
      event, liability, quantity=repaid, given=value, released=released
    )
