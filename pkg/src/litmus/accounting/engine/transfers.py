"""
Transfers: linked pairs at carried basis, swap links as a swap (policy 05 rule
13.5), unlinked ones across the books' boundary by their basis (rule 13), and
income booked before transfers (rule 14.3).
"""

from dataclasses import replace
from datetime import datetime
from decimal import Decimal

from litmus.accounting.engine.arithmetic import exact_sum
from litmus.accounting.engine.checks import Linked, pairs, sides
from litmus.accounting.engine.core import Finding
from litmus.accounting.engine.debt import Debt
from litmus.accounting.engine.totals import EXACT
from litmus.accounting.model import (
  Event,
  Leg,
  Link,
  Move,
)
from litmus.accounting.pricing import PriceGap


class Transfers(Debt):
  """Linked and unlinked transfer legs."""

  def income(self, event: Event):
    """
    Book an event's income legs, once. They come before its transfers, so a
    withdrawal's `performance` leg is recognised before the transfer takes the
    units out (policy 05 rule 14.3).
    """
    if event.id in self.earned:
      return
    self.earned.add(event.id)
    for leg in event.legs:
      if leg.tag == 'income' and not leg.fee:
        self.flow(event, leg)

  def transfer(self, event: Event, legs: list[Leg], linked: Linked):
    """
    Route transfer legs: a linked pair is booked once, when the earlier of its
    two events is processed (ties in input order), at that event's time, after
    the income legs of both events.
    """
    if event.id in linked:
      link, src, dst = linked[event.id]
      if (link.src, link.dst) not in self.booked_links:
        self.booked_links.add((link.src, link.dst))
        self.income(src)
        self.income(dst)
        if link.kind == 'swap':
          self.swap(src, dst)
        else:
          self.internal(event.time, link, src, dst)
      return
    for leg in legs:
      self.external(event, leg)

  def external(self, event: Event, leg: Leg):
    """
    Book an unlinked transfer by its basis (policy 05 rules 13.2 to 13.4): at
    market value, reported when unclassified; or at carried cost. The functional
    currency needs no classification.
    """
    if leg.basis == 'carried' and leg.asset != self.fc:
      self.carried(event, leg)
      return
    if leg.basis == 'unclassified' and leg.asset != self.fc:
      self.report(
        'unmatched_transfer',
        event.id,
        f'unlinked transfer of {leg.quantity} {leg.asset} in {leg.compartment} booked at market value',
        asset=leg.asset,
        compartment=leg.compartment,
        quantity=str(leg.quantity),
      )
    try:
      value = self.market(event, leg)
    except PriceGap as e:
      self.gap(event, e)
      self.unbooked(event, leg, 'price gap')
      return
    self.journal.add(
      event, 'external', -value, compartment=leg.compartment, asset=leg.asset
    )
    self.book_leg(event, leg, value)

  def carried(self, event: Event, leg: Leg):
    """
    A boundary crossing at cost (rules 11.2.3 and 13.3.1): in, a lot opens at
    the leg's cost and acquisition time with no income; out, lots leave at cost
    with no P&L. The external account takes the cost.
    """
    key = self.key(leg)
    scope = (leg.compartment, leg.asset)
    if leg.quantity > 0:
      cost = leg.cost if leg.cost is not None else Decimal(0)
      self.journal.add(
        event, 'external', -cost, compartment=leg.compartment, asset=leg.asset
      )
      self.book_leg(event, leg, cost, acquired=leg.acquired)
      return
    taken = self.book.take(key, -leg.quantity)
    moved = exact_sum(quantity for _, quantity, _ in taken)
    cost = exact_sum(share for _, _, share in taken)
    self.journal.add(event, 'holding', -cost, compartment=key[0], asset=leg.asset)
    self.journal.add(
      event, 'external', cost, compartment=leg.compartment, asset=leg.asset
    )
    self.scope_positions[scope] = EXACT.subtract(
      self.scope_positions.get(scope, Decimal(0)), moved
    )
    if moved < -leg.quantity:
      self.unbooked(event, leg, f'only {moved} {leg.asset} held to carry out at cost')

  def swap(self, src: Event, dst: Event):
    """
    Book a swap link (policy 05 rule 13.5): the outflow is disposed of at market
    value at the source's time, and the inflow is acquired at that value, shared
    by market value when it has several legs. It is one entry on the source
    event, like a move; the inflow's lots are dated at the destination's time.
    A malformed swap was already reported by `check` and is skipped.
    """
    given, received = sides(src, dst)
    if not given or not received:
      for owner, legs in ((src, given), (dst, received)):
        for leg in legs:
          self.unbooked(owner, leg, 'swap link needs an outflow and an inflow')
      return
    try:
      values = [self.market(src, l) for l in given]
      weights = (
        [Decimal(1)]
        if len(received) == 1
        else [abs(self.market(dst, l)) for l in received]
      )
    except PriceGap as e:
      self.gap(src, e)
      for owner, legs in ((src, given), (dst, received)):
        for leg in legs:
          self.unbooked(owner, leg, 'price gap')
      return
    total = -exact_sum(values)
    wsum = exact_sum(weights)
    for leg, v in zip(given, values):
      self.book_leg(src, leg, v)
    for leg, w in zip(received, weights):
      self.book_leg(src, leg, total * w / wsum, acquired=dst.time)

  def internal(self, time: datetime, link: Link, src: Event, dst: Event):
    """
    Book a linked pair at `time`. Mismatched assets were already reported by
    `check` and are skipped. Under global lots the link changes nothing; under
    per-compartment lots the source's lots move to the destination at carried
    basis.
    """
    for asset, a, b in pairs(src, dst):
      if a is None or b is None or a.quantity + b.quantity != 0:
        for owner, leg in ((src, a), (dst, b)):
          if leg is not None:
            self.unbooked(owner, leg, 'link does not conserve quantity')
        continue
      for leg in (a, b):
        scope = (leg.compartment, leg.asset)
        self.scope_positions[scope] = EXACT.add(
          self.scope_positions.get(scope, Decimal(0)), leg.quantity
        )
      source_scope = (a.compartment, a.asset)
      if (
        self.notional_scopes
        and source_scope not in self.notional_scopes
        and asset not in self.positions
        and asset != self.fc
      ):
        self.watch(
          ('scope', source_scope),
          Finding(
            'negative_position',
            src.id,
            f'{asset} in {a.compartment} is net short ({{position}}) after linked transfer',
            {'asset': asset, 'compartment': a.compartment},
          ),
        )
      if asset == self.fc:
        if self.key(a) != self.key(b):
          for leg in (a, b):
            self.journal.add(
              src, 'holding', leg.quantity, compartment=self.key(leg)[0], asset=asset
            )
        continue
      owed = (
        self.owed((b.compartment, asset))
        if b.compartment in self.perp_compartments
        else Decimal(0)
      )
      if self.key(a) != self.key(b):
        qty = b.quantity * (1 if self.book.sign(self.key(a)) >= 0 else -1)
        taken, created = self.book.move(self.key(a), self.key(b), qty)
        for key, sign in ((self.key(a), -1), (self.key(b), 1)):
          self.journal.add(
            src,
            'holding',
            sign * taken.cost,
            compartment=key[0],
            asset=asset,
            ref=('move', len(self.moves)),
          )
        self.moves.append(
          Move(
            link=link,
            time=time,
            asset=asset,
            quantity=taken.quantity,
            cost=taken.cost,
            lots=tuple(dict.fromkeys(l.id for l in created)),
          )
        )
        short = abs(qty) - abs(taken.quantity)
        if short > 0:
          self.unbooked(
            src, a, f'only {abs(taken.quantity)} {asset} held in {a.compartment}'
          )
      if owed:
        self.clear_moved(dst, replace(b, quantity=min(b.quantity, owed)))

  def clear_moved(self, event: Event, leg: Leg):
    """
    Clear a settlement shortfall with units moved in by a link (policy 05 rule
    8.6): they are disposed of at market value, and the liability releases its
    basis against that value.
    """
    try:
      value = self.market(event, leg)
    except PriceGap as e:
      self.gap(event, e)
      self.unbooked(event, leg, 'price gap')
      return
    self.lots(event, replace(leg, quantity=-leg.quantity), -value)
    self.clear(event, leg, value)
