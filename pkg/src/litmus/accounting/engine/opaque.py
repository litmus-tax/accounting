"""
Opaque positions (policy 05 rule 14): an opaque compartment is one position,
booked by value. Coins moved in carry their basis into it, coins taken out are
a redemption at market value that releases cost up to that value, and any value
beyond it is a `performance` result, one line per compartment. Its contents are
tracked per asset from its legs; when `contents` legs leave it empty, the cost
left is a `performance` loss.

Interim rule (specs#135, decision 23 open): results are recognised only at
redemptions and at an empty compartment. Positions still open are never
trued up to a value.
"""

from datetime import datetime
from decimal import Decimal

from litmus.accounting.engine.arithmetic import exact_sum
from litmus.accounting.engine.debt import Debt
from litmus.accounting.engine.totals import EXACT, ZERO, LotKey, crumb
from litmus.accounting.model import Event, Flow, Leg, Link, Move
from litmus.accounting.pricing import PriceGap

PREFIX = 'position:opaque:'
"""The asset of an opaque compartment's position is this prefix and the compartment."""


def position_asset(compartment: str) -> str:
  """The asset of an opaque compartment's position (policy 05 rule 14.1)."""
  return f'{PREFIX}{compartment}'


class Opaque(Debt):
  """Entries into, redemptions from and the emptying of opaque positions."""

  def position_key(self, compartment: str) -> LotKey:
    """The lot key of an opaque compartment's position under the policy's lot scope."""
    return (
      compartment if self.policy.lot_scope == 'compartment' else None,
      position_asset(compartment),
    )

  def misplaced(self, event: Event, leg: Leg) -> bool:
    """
    Report and say whether a leg cannot be booked where it is: a leg other than
    `transfer` or `contents` in an opaque compartment, which holds no lots (rule
    14.1), or a `contents` leg anywhere else.
    """
    if leg.compartment in self.opaque:
      if leg.tag in ('transfer', 'contents') and not leg.fee:
        return False
      self.unbooked(
        event, leg, 'an opaque compartment holds no lots (policy 05 rule 14.1)'
      )
      return True
    if leg.tag == 'contents':
      self.unbooked(event, leg, 'contents legs belong to an opaque compartment')
      return True
    return False

  def track(self, event: Event):
    """Add the event's legs in opaque compartments to their contents (rule 14.5)."""
    for leg in event.legs:
      if leg.compartment in self.opaque and leg.tag in ('transfer', 'contents'):
        held = self.contents.setdefault(leg.compartment, {})
        held[leg.asset] = EXACT.add(held.get(leg.asset, ZERO), leg.quantity)

  def emptied(self, event: Event):
    """
    After an event with `contents` legs, recognise the remaining cost of each
    compartment they leave empty in every asset as a `performance` loss (rule
    14.8; the interim rule of specs#135).
    """
    compartments = {
      leg.compartment
      for leg in event.legs
      if leg.tag == 'contents' and leg.compartment in self.opaque
    }
    for compartment in sorted(compartments):
      held = self.contents.get(compartment, {}).values()
      if any(q != 0 and not crumb(q) for q in held):
        continue
      key = self.position_key(compartment)
      units = self.book.position(key)
      if units <= 0:
        continue
      taken = self.book.take(key, units)
      cost = exact_sum(share for _, _, share in taken)
      self.journal.add(event, 'holding', -cost, compartment=key[0], asset=key[1])
      self.result(event, compartment, -cost)

  def result(self, event: Event, compartment: str, amount: Decimal):
    """A `performance` result of an opaque position: income when positive, expense when negative (rule 14.7)."""
    if not amount:
      return
    asset = position_asset(compartment)
    kind = 'income' if amount > 0 else 'expense'
    self.journal.add(
      event,
      kind,
      -amount,
      compartment=compartment,
      asset=asset,
      label='performance',
      ref=('flow', len(self.flows)),
    )
    self.flows.append(
      Flow(
        event=event.id,
        time=event.time,
        asset=asset,
        compartment=compartment,
        quantity=amount,
        value=abs(amount),
        kind=kind,
        fee=False,
        label='performance',
      )
    )

  def enter(
    self,
    event: Event,
    compartment: str,
    cost: Decimal,
    *,
    acquired: datetime,
    origin: str,
  ) -> list[str]:
    """
    Add `cost` to an opaque position, as units at a cost of 1, and return the
    ids of the lots it opened; the caller books the other side.
    """
    if cost <= 0:
      return []
    key = self.position_key(compartment)
    lot = self.book.open(
      key, quantity=cost, cost=cost, time=acquired, event=origin, exact_basis=True
    )
    self.journal.add(event, 'holding', cost, compartment=key[0], asset=key[1])
    return [lot.id]

  def redeem(self, event: Event, compartment: str, value: Decimal):
    """
    Release an opaque position's cost up to a redemption's `value` out, and
    recognise any value beyond it as `performance` income (cost recovery, rule
    14.4); the caller books what was received.
    """
    key = self.position_key(compartment)
    units = max(self.book.position(key), ZERO)
    released = ZERO
    if units and value > 0:
      taken = self.book.take(key, min(value, units))
      released = exact_sum(share for _, _, share in taken)
      self.journal.add(event, 'holding', -released, compartment=key[0], asset=key[1])
    self.result(event, compartment, value - released)

  def opaque_link(
    self, time: datetime, link: Link, src: Event, dst: Event, a: Leg, b: Leg
  ):
    """
    Book a linked pair with a side in an opaque compartment (rules 14.2, 14.3
    and 14.13): out of one, a redemption at market value at the source's time;
    into one from a compartment that holds lots, the coins' lots leave and their
    basis carries into the position, with their acquisition dates.
    """
    if a.compartment in self.opaque:
      try:
        value = self.market(src, b)
      except PriceGap as e:
        self.gap(src, e)
        self.unbooked(src, a, 'price gap')
        self.unbooked(dst, b, 'price gap')
        return
      self.redeem(src, a.compartment, value)
      if b.compartment in self.opaque:
        self.enter(src, b.compartment, value, acquired=dst.time, origin=src.id)
      else:
        self.book_leg(src, b, value, acquired=dst.time)
      return
    self.carry_in(time, link, src, a, b)

  def carry_in(self, time: datetime, link: Link, src: Event, a: Leg, b: Leg):
    """The coins of a linked transfer into an opaque compartment carry their basis into its position (rule 14.2)."""
    asset = a.asset
    source = self.key(a)
    if asset == self.fc:
      self.journal.add(src, 'holding', a.quantity, compartment=source[0], asset=asset)
      self.enter(src, b.compartment, b.quantity, acquired=time, origin=src.id)
      return
    scope = (a.compartment, asset)
    held = max(self.book.position(source), ZERO)
    taken = self.book.take(source, min(b.quantity, held)) if held else []
    moved = exact_sum(quantity for _, quantity, _ in taken)
    cost = exact_sum(share for _, _, share in taken)
    self.scope_positions[scope] = EXACT.subtract(
      self.scope_positions.get(scope, ZERO), moved
    )
    index = len(self.moves)
    self.journal.add(
      src, 'holding', -cost, compartment=source[0], asset=asset, ref=('move', index)
    )
    target = self.position_key(b.compartment)
    created = [
      self.book.open(
        target,
        quantity=share,
        cost=share,
        time=lot.acquired,
        event=lot.event,
        origins=lot.origins,
        exact_basis=True,
      ).id
      for lot, _, share in taken
      if share > 0
    ]
    self.journal.add(
      src, 'holding', cost, compartment=target[0], asset=target[1], ref=('move', index)
    )
    self.moves.append(
      Move(
        link=link,
        time=time,
        asset=asset,
        quantity=moved,
        cost=cost,
        lots=tuple(dict.fromkeys(created)),
      )
    )
    if moved < b.quantity:
      self.unbooked(src, a, f'only {moved} {asset} held in {a.compartment}')

  def opaque_external(self, event: Event, leg: Leg):
    """
    An unlinked transfer into or out of an opaque compartment, across the books'
    boundary: in, the position's cost grows by the leg's market value (or its
    cost when `carried`); out, a redemption at market value.
    """
    if leg.basis == 'unclassified' and leg.asset != self.fc:
      self.report(
        'unmatched_transfer',
        event.id,
        f'unlinked transfer of {leg.quantity} {leg.asset} in {leg.compartment} booked at market value',
        asset=leg.asset,
        compartment=leg.compartment,
        quantity=str(leg.quantity),
      )
    if leg.quantity > 0 and leg.basis == 'carried':
      value = leg.cost if leg.cost is not None else ZERO
    else:
      try:
        value = self.market(event, leg)
      except PriceGap as e:
        self.gap(event, e)
        self.unbooked(event, leg, 'price gap')
        return
    self.journal.add(
      event, 'external', -value, compartment=leg.compartment, asset=leg.asset
    )
    if leg.quantity > 0:
      self.enter(
        event,
        leg.compartment,
        value,
        acquired=leg.acquired or event.time,
        origin=event.id,
      )
    else:
      self.redeem(event, leg.compartment, -value)
