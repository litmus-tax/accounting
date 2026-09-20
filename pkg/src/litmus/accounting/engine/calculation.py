"""
The engine: a pure function from events, links, policy and a pricing source to
lots, realized PnL, income/expense flows, internal moves, prices used and an
exceptions report.

Ordering is part of the contract: events are processed by time, ties in input
order; within an event borrow legs, then trades, then transfers, then repay
legs, then income and expense legs, then fee legs. A linked pair is booked when
the earlier of its two events is processed.
"""

from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from typing_extensions import Literal, Sequence
from litmus.accounting.model import (
  Event,
  Leg,
  Link,
  Policy,
  Result,
  Realized,
  Flow,
  Move,
  Balance,
  Liability,
  ExceptionItem,
  ExceptionCode,
  Origin,
  RolloverSlice,
  RolloverRecord,
)
from litmus.accounting.engine.lots import LotBook, LotKey, Applied, split_origins
from litmus.accounting.engine.operations import rollover_problems, ordered_events
from litmus.accounting.engine.arithmetic import exact_sum, difference
from litmus.accounting.pricing import Pricing, Valuer, PriceGap
from litmus.accounting.engine.rounding import round_result

Linked = dict[str, tuple[Link, Event, Event]]
"""Event id to the link it takes part in, with the resolved source and destination."""


@dataclass(frozen=True)
class Checked:
  """Outcome of the structural checks that need no prices."""

  events: tuple[Event, ...]
  """Valid events, in input order."""
  linked: Linked
  exceptions: tuple[ExceptionItem, ...]
  complete: bool


def problems(event: Event) -> list[str]:
  """Reasons an event is invalid, empty when fine."""
  out: list[str] = rollover_problems(event)
  if len(event.depends_on) != len(set(event.depends_on)):
    out.append('duplicate dependency reference')
  if event.time.tzinfo is None:
    out.append('naive timestamp')
  if not event.legs:
    out.append('no legs')
  for i, leg in enumerate(event.legs):
    if leg.quantity == 0:
      out.append(f'leg {i}: zero quantity')
    elif leg.fee and leg.quantity > 0:
      out.append(f'leg {i}: fee must be negative')
    elif leg.tag == 'income' and leg.quantity < 0:
      out.append(f'leg {i}: income must be positive')
    elif leg.tag == 'expense' and leg.quantity > 0:
      out.append(f'leg {i}: expense must be negative')
    elif (
      leg.tag == 'borrow' and leg.quantity < 0 and (leg.label != 'interest' or leg.fee)
    ):
      out.append(f'leg {i}: borrow must be positive')
    elif leg.tag == 'repay' and leg.quantity > 0:
      out.append(f'leg {i}: repay must be negative')
  return out


def check(events: Sequence[Event], links: Sequence[Link] = ()) -> Checked:
  """
  Structural validation: duplicate ids, invalid events, links to unknown events
  and links that do not conserve quantity per asset. No prices are needed.
  """
  exceptions: list[ExceptionItem] = []
  complete = True
  by_id: dict[str, Event] = {}
  valid: list[Event] = []
  for e in events:
    if e.id in by_id:
      complete = False
      exceptions.append(
        ExceptionItem(
          'duplicate_id', e.id, f'duplicate event id {e.id!r}; later one skipped'
        )
      )
      continue
    by_id[e.id] = e
    found = problems(e)
    if found:
      complete = False
      exceptions.append(ExceptionItem('invalid_event', e.id, '; '.join(found)))
    else:
      valid.append(e)
  for event in tuple(valid):
    for dependency in event.depends_on:
      parent = by_id.get(dependency)
      if parent is None or parent.time > event.time:
        complete = False
        exceptions.append(
          ExceptionItem(
            'invalid_event', event.id, f'unknown or later dependency {dependency!r}'
          )
        )
        valid.remove(event)
        break
  ordered, blocked = ordered_events(tuple(valid))
  for event in blocked:
    complete = False
    exceptions.append(
      ExceptionItem(
        'invalid_event', event.id, 'cyclic or invalid dependency; operation not booked'
      )
    )
  valid = list(ordered)
  linked: Linked = {}
  for link in links:
    src, dst = by_id.get(link.src), by_id.get(link.dst)
    if src is None or dst is None:
      exceptions.append(
        ExceptionItem(
          'unknown_event',
          None,
          f'link references unknown event: {link.src} -> {link.dst}',
          {'src': link.src, 'dst': link.dst},
        )
      )
      continue
    linked[src.id] = linked[dst.id] = (link, src, dst)
    for asset, a, b in pairs(src, dst):
      if a is None or b is None or a.quantity + b.quantity != 0:
        exceptions.append(
          ExceptionItem(
            'link_mismatch',
            src.id,
            f'link {src.id} -> {dst.id} does not conserve {asset}: out {a and a.quantity}, in {b and b.quantity}',
            {'src': src.id, 'dst': dst.id, 'asset': asset},
          )
        )
  return Checked(tuple(valid), linked, tuple(exceptions), complete)


def pairs(src: Event, dst: Event) -> list[tuple[str, Leg | None, Leg | None]]:
  """Transfer legs of a linked pair matched by asset: outflow from `src`, inflow into `dst`."""
  out = {
    l.asset: l for l in src.legs if l.tag == 'transfer' and not l.fee and l.quantity < 0
  }
  inn = {
    l.asset: l for l in dst.legs if l.tag == 'transfer' and not l.fee and l.quantity > 0
  }
  return [
    (asset, out.get(asset), inn.get(asset)) for asset in sorted(out.keys() | inn.keys())
  ]


def validate(
  events: Sequence[Event], links: Sequence[Link] = ()
) -> list[ExceptionItem]:
  """Structural problems of a ledger, without prices. Empty when the ledger is well formed."""
  return list(check(events, links).exceptions)


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


class Engine:
  """One run's mutable state. Use `run` unless you need the pieces."""

  def __init__(self, policy: Policy, pricing: Pricing, *, strict: bool = False):
    self.policy = policy
    self.strict = strict
    self.fc = policy.functional_currency
    self.valuer = Valuer(pricing, policy)
    self.book = LotBook(policy.cost_method)
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

  def book_leg(
    self, event: Event, leg: Leg, value: Decimal, *, fees: Decimal = Decimal(0)
  ) -> Applied | None:
    """
    Book a leg worth `value` (signed like its quantity) into lots, recording the
    realized part. `fees` is the fee value already netted out of `value` under
    `fee_treatment: capitalize`, reported on the realized row. Functional-currency
    legs carry no lots.
    """
    if leg.asset == self.fc:
      return None
    applied = self.book.apply(
      self.key(leg), quantity=leg.quantity, value=value, time=event.time, event=event.id
    )
    closed = applied.closed
    if closed.quantity != 0:
      share = closed.quantity / leg.quantity
      proceeds = -value * share
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
    scope = (leg.compartment, leg.asset)
    self.scope_positions[scope] = (
      self.scope_positions.get(scope, Decimal(0)) + leg.quantity
    )
    position = self.scope_positions[scope] if self.notional_scopes else applied.position
    if (
      position < 0
      and leg.asset not in self.positions
      and scope not in self.notional_scopes
    ):
      self.report(
        'negative_position',
        event.id,
        f'{leg.asset} in {leg.compartment} is net short ({position}) and '
        + (
          'not an eligible notional scope'
          if self.notional_scopes
          else 'not a position asset'
        ),
        asset=leg.asset,
        compartment=leg.compartment,
        position=str(position),
      )
    return applied

  def market(self, event: Event, leg: Leg) -> Decimal:
    """Market value of a leg, signed like its quantity."""
    return self.valuer.value(leg.asset, leg.quantity, event.time)

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

  def flow(self, event: Event, leg: Leg):
    """Book an income or expense leg at market value."""
    try:
      value = self.market(event, leg)
    except PriceGap as e:
      self.gap(event, e)
      self.unbooked(event, leg, 'price gap')
      return
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
      )
    )
    self.book_leg(event, leg, value)

  def external(self, event: Event, leg: Leg):
    """Book an unmatched transfer at market value and report it."""
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
    self.book_leg(event, leg, value)

  def liability(self, event: Event, leg: Leg) -> OpenLiability:
    """The liability under the leg's (compartment, asset), opened now if there is none."""
    key = (leg.compartment, leg.asset)
    if key not in self.liabilities:
      self.liabilities[key] = OpenLiability(
        id=f'liability-{len(self.liabilities) + 1}',
        asset=leg.asset,
        compartment=leg.compartment,
        quantity=Decimal(0),
        cost=Decimal(0),
        label=leg.label,
        opened=event.time,
        updated=event.time,
        event=event.id,
      )
    return self.liabilities[key]

  def borrow(self, event: Event, leg: Leg):
    """
    Book a borrow leg: the asset received opens a lot at market value and the
    liability grows by the same quantity and value. Labelled `interest` nothing
    is received: a positive accrual grows debt, a negative correction releases
    proportional debt basis without cash movement or market realization.
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
    if leg.label != 'interest':
      self.book_leg(event, leg, value)
    liability = self.liability(event, leg)
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
      self.report(
        'negative_liability',
        event.id,
        f'interest reversal {reduction} {leg.asset} in {leg.compartment} exceeds {owed} owed',
        asset=leg.asset,
        compartment=leg.compartment,
        owed=str(owed),
        reversed=str(reduction),
      )
    liability.quantity -= reduction
    liability.cost -= released
    liability.updated = event.time

  def repay(self, event: Event, leg: Leg):
    """
    Book a repay leg: the asset given is a disposal at market with its normal
    PnL, and the liability shrinks by the quantity repaid, releasing a
    proportional share of its basis. Under `liability_valuation: market` the
    difference between the basis released and the market value repaid is
    realized against the liability. Repaying more than is owed is reported as
    `negative_liability` and booked anyway.
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
      self.report(
        'negative_liability',
        event.id,
        f'repaid {repaid} {leg.asset} in {leg.compartment} against {owed} owed',
        asset=leg.asset,
        compartment=leg.compartment,
        owed=str(owed),
        repaid=str(repaid),
      )
    liability.quantity -= repaid
    liability.cost -= released
    liability.updated = event.time
    if self.policy.liability_valuation == 'market':
      self.realized.append(
        Realized(
          event=event.id,
          time=event.time,
          asset=leg.asset,
          compartment=leg.compartment,
          quantity=repaid,
          proceeds=value,
          cost=-released,
          pnl=value + released,
          lots=(liability.id,),
        )
      )

  def internal(self, time: datetime, link: Link, src: Event, dst: Event):
    """
    Book a linked pair at `time`. Mismatched assets were already reported by
    `check` and are skipped. Under global lots the link changes nothing; under
    per-compartment lots the source's lots move to the destination at carried
    basis.
    """
    for asset, a, b in pairs(src, dst):
      if a is None or b is None or a.quantity + b.quantity != 0:
        continue
      for leg in (a, b):
        scope = (leg.compartment, leg.asset)
        self.scope_positions[scope] = (
          self.scope_positions.get(scope, Decimal(0)) + leg.quantity
        )
      source_scope = (a.compartment, a.asset)
      source_position = self.scope_positions[source_scope]
      if (
        self.notional_scopes
        and source_position < 0
        and source_scope not in self.notional_scopes
        and asset not in self.positions
        and asset != self.fc
      ):
        self.report(
          'negative_position',
          src.id,
          f'{asset} in {a.compartment} is net short ({source_position}) after linked transfer',
          asset=asset,
          compartment=a.compartment,
          position=str(source_position),
        )
      if self.key(a) == self.key(b) or asset == self.fc:
        continue
      qty = b.quantity * (1 if self.book.position(self.key(a)) >= 0 else -1)
      taken, created = self.book.move(self.key(a), self.key(b), qty)
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

  def transfer(self, event: Event, legs: list[Leg], linked: Linked):
    """
    Route transfer legs: a linked pair is booked once, when the earlier of its
    two events is processed (ties in input order), at that event's time.
    """
    if event.id in linked:
      link, src, dst = linked[event.id]
      if (link.src, link.dst) not in self.booked_links:
        self.booked_links.add((link.src, link.dst))
        self.internal(event.time, link, src, dst)
      return
    for leg in legs:
      self.external(event, leg)

  def rollover(self, event: Event):
    """Atomically carry selected basis, retaining a slice for every acquisition."""
    operation = event.rollover
    if operation is None:
      return
    from copy import deepcopy

    fee_values: list[tuple[Leg, Decimal]] = []
    try:
      for index in operation.capitalized_fee_legs:
        leg = event.legs[index]
        fee_values.append((leg, self.market(event, leg)))
    except PriceGap as error:
      self.gap(event, error)
      for leg in event.legs:
        self.unbooked(event, leg, 'capitalized fee price gap')
      return
    if exact_sum(-value for _, value in fee_values) > operation.capitalized_costs:
      for leg in event.legs:
        self.unbooked(event, leg, 'capitalized_costs does not cover paid fee value')
      return
    book = deepcopy(self.book)
    consumed: list[RolloverSlice] = []
    created: list[RolloverSlice] = []
    reason: str | None = None
    for item in operation.inputs:
      leg = event.legs[item.leg]
      key = self.key(leg)
      candidates = book.lots.get(key, [])
      if leg.asset == self.fc:
        if item.lots:
          reason = 'functional currency has no selectable lots'
          break
        cost = -leg.quantity
        consumed.append(
          RolloverSlice(
            item.leg,
            f'cash:{event.id}:{item.leg}',
            cost,
            cost,
            event.time,
            (Origin(event.id, event.time, cost),),
          )
        )
        continue
      if item.lots and any(
        lot_id not in {lot.id for lot in candidates} for lot_id in item.lots
      ):
        reason = 'selected lot does not exist under the input identity'
        break
      available = sum(
        (lot.quantity for lot in candidates if not item.lots or lot.id in item.lots),
        Decimal(0),
      )
      if available < -leg.quantity or any(lot.quantity < 0 for lot in candidates):
        reason = 'insufficient positive input lots'
        break
      for lot, quantity, cost in book.take(
        key, -leg.quantity, selected=item.lots, exact_basis=True
      ):
        consumed.append(
          RolloverSlice(item.leg, lot.id, quantity, cost, lot.acquired, lot.origins)
        )
    for item in operation.outputs:
      leg = event.legs[item.leg]
      if leg.asset == self.fc or book.position(self.key(leg)) < 0:
        reason = 'output requires a non-functional-currency, non-short holding'
    if reason:
      for leg in event.legs:
        if leg.tag == 'rollover':
          self.unbooked(event, leg, reason)
      return
    basis_in = exact_sum(piece.cost for piece in consumed)
    allocations = tuple(
      item.allocation if item.allocation is not None else Decimal(1)
      for item in operation.outputs
    )
    inherited_remaining = [piece.cost for piece in consumed]
    origin_remaining = [piece.origins for piece in consumed]
    capital_remaining = operation.capitalized_costs
    for output_index, item in enumerate(operation.outputs):
      leg = event.legs[item.leg]
      allocation = allocations[output_index]
      final_output = output_index == len(operation.outputs) - 1
      capital = (
        capital_remaining if final_output else operation.capitalized_costs * allocation
      )
      capital_remaining = difference(capital_remaining, capital)
      capital_piece_remaining = capital
      quantity_remaining = leg.quantity
      # The largest basis share receives quantity residue, so a tiny final
      # source slice can never lose its nonzero basis through a zero quantity.
      largest = max(range(len(consumed)), key=lambda index: consumed[index].cost)
      indices = [index for index in range(len(consumed)) if index != largest] + [
        largest
      ]
      for part_index, index in enumerate(indices):
        piece = consumed[index]
        # Basis-weighted receipt units retain each acquisition's date and basis.
        # With all-zero basis every consumed slice gets an equal unit share.
        fraction = piece.cost / basis_in if basis_in else Decimal(1) / len(consumed)
        final_piece = part_index == len(consumed) - 1
        quantity = quantity_remaining if final_piece else leg.quantity * fraction
        addition = capital_piece_remaining if final_piece else capital * fraction
        inherited = (
          inherited_remaining[index] if final_output else piece.cost * allocation
        )
        origins = (
          origin_remaining[index]
          if final_output
          else split_origins(piece.origins, allocation, inherited)
        )
        origin_remaining[index] = tuple(
          replace(origin, cost=difference(origin.cost, used.cost))
          for origin, used in zip(origin_remaining[index], origins)
        )
        inherited_remaining[index] = difference(inherited_remaining[index], inherited)
        quantity_remaining = difference(quantity_remaining, quantity)
        capital_piece_remaining = difference(capital_piece_remaining, addition)
        cost = exact_sum((inherited, addition))
        if addition:
          origins += (Origin(event.id, event.time, addition),)
        acquired = (
          piece.acquired if operation.acquisition_date == 'carry' else event.time
        )
        if quantity == 0:
          continue
        lot = book.open(
          self.key(leg),
          quantity=quantity,
          cost=cost,
          time=acquired,
          event=event.id,
          origins=origins,
          exact_basis=True,
        )
        created.append(
          RolloverSlice(item.leg, lot.id, quantity, cost, acquired, origins)
        )
    if operation.write_off:
      for item in operation.inputs:
        leg = event.legs[item.leg]
        pieces = [piece for piece in consumed if piece.leg == item.leg]
        cost = sum((piece.cost for piece in pieces), Decimal(0))
        self.realized.append(
          Realized(
            event.id,
            event.time,
            leg.asset,
            leg.compartment,
            leg.quantity,
            Decimal(0),
            cost,
            -cost,
            tuple(piece.lot for piece in pieces),
          )
        )
    # Zero-basis consumed slices have no basis weight but their provenance must
    # survive mixed-basis transformations as well as all-zero transformations.
    zero_origins = tuple(
      origin for piece in consumed if piece.cost == 0 for origin in piece.origins
    )
    if basis_in and zero_origins and created:
      first = created[0]
      created[0] = replace(first, origins=first.origins + zero_origins)
      for lots in book.lots.values():
        for lot in lots:
          if lot.id == first.lot:
            lot.origins += zero_origins
    self.book = book
    for leg, value in fee_values:
      self.book_leg(event, leg, value)
    for leg in event.legs:
      if leg.tag == 'rollover':
        scope = (leg.compartment, leg.asset)
        self.scope_positions[scope] = (
          self.scope_positions.get(scope, Decimal(0)) + leg.quantity
        )
    self.rollovers.append(
      RolloverRecord(
        event.id,
        tuple(consumed),
        tuple(created),
        allocations,
        basis_in,
        operation.capitalized_costs,
        exact_sum(piece.cost for piece in created),
        basis_in if operation.write_off else Decimal(0),
        operation.cost_reference,
      )
    )

  def process(self, event: Event, linked: Linked):
    """Book one event: borrows, trades, transfers, repays, income and expenses, then fees."""
    before = len(self.exceptions)
    self.rollover(event)
    if event.rollover and any(
      item.code == 'unbooked' for item in self.exceptions[before:]
    ):
      return
    borrows = [l for l in event.legs if l.tag == 'borrow' and not l.fee]
    trades = [l for l in event.legs if l.tag == 'trade' and not l.fee]
    transfers = [l for l in event.legs if l.tag == 'transfer' and not l.fee]
    repays = [l for l in event.legs if l.tag == 'repay' and not l.fee]
    flows = [l for l in event.legs if l.tag in ('income', 'expense') and not l.fee]
    rollover_fees: set[int] = (
      set(event.rollover.capitalized_fee_legs) if event.rollover else set()
    )
    fees = [
      leg
      for index, leg in enumerate(event.legs)
      if leg.fee and index not in rollover_fees
    ]
    capitalized = bool(trades) and self.policy.fee_treatment == 'capitalize'
    for leg in borrows:
      self.borrow(event, leg)
    if trades:
      self.trade(event, trades, fees if capitalized else [])
    if transfers:
      self.transfer(event, transfers, linked)
    for leg in repays:
      self.repay(event, leg)
    for leg in flows:
      self.flow(event, leg)
    if not capitalized:
      for leg in fees:
        self.flow(event, leg)


def balances(events: Sequence[Event]) -> list[Balance]:
  """Net quantity per (compartment, asset) over every leg of every event."""
  totals: dict[tuple[str, str], Decimal] = {}
  for e in events:
    for l in e.legs:
      k = (l.compartment, l.asset)
      totals[k] = totals.get(k, Decimal(0)) + l.quantity
  return [
    Balance(compartment=c, asset=a, quantity=q) for (c, a), q in sorted(totals.items())
  ]


def run(
  events: Sequence[Event],
  *,
  links: Sequence[Link] = (),
  policy: Policy,
  pricing: Pricing,
  strict: bool = False,
) -> Result:
  """
  Run the books.

  Args:
    events: Any order; processed by time, ties in input order.
    links: Pairs of event ids that are one internal movement.
    policy: Cost method, lot scope, functional currency, valuation rules.
    pricing: Caller-implemented price source.
    strict: Raise `PriceGap` on the first missing price instead of reporting it.

  Returns:
    Lots, liabilities, realized PnL, flows, internal moves, balances, every
    price asked for, and the exceptions report. `complete` is false when anything was left unbooked.
    Money fields are rounded to `policy.minor_unit` when it is set.

  Raises:
    PriceGap: In strict mode, on the first missing price.
  """
  engine = Engine(policy, pricing, strict=strict)
  checked = check(events, links)
  engine.exceptions.extend(checked.exceptions)
  engine.complete = checked.complete
  identities = {(leg.compartment, leg.asset) for event in events for leg in event.legs}
  scopes = [(scope.compartment, scope.asset) for scope in policy.notional_scopes]
  if len(scopes) != len(set(scopes)) or any(
    scope not in identities for scope in scopes
  ):
    engine.report(
      'invalid_event',
      None,
      'notional scopes must be unique and identify input compartment/assets',
    )
    engine.complete = False
  failed: set[str] = set()
  for e in checked.events:
    if any(dependency in failed for dependency in e.depends_on):
      engine.complete = False
      engine.report('unbooked', e.id, 'required predecessor was not completely booked')
      failed.add(e.id)
      continue
    before = len(engine.exceptions)
    engine.process(e, checked.linked)
    if any(item.code == 'unbooked' for item in engine.exceptions[before:]):
      failed.add(e.id)
  result = Result(
    policy=policy,
    lots=tuple(engine.book.open_lots()),
    liabilities=tuple(
      engine.liabilities[k].freeze()
      for k in sorted(engine.liabilities)
      if engine.liabilities[k].quantity != 0
    ),
    realized=tuple(engine.realized),
    flows=tuple(engine.flows),
    moves=tuple(engine.moves),
    balances=tuple(balances(events)),
    prices=tuple(engine.valuer.records),
    exceptions=tuple(engine.exceptions),
    complete=engine.complete,
    rollovers=tuple(engine.rollovers),
  )
  return round_result(result, policy.minor_unit)
