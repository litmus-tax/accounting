"""
The engine: a pure function from events, links, policy and a pricing source to
lots, realized PnL, income/expense flows, internal moves, prices used and an
exceptions report.

Ordering is part of the contract: events are processed by time, ties in input
order; within an event borrow legs, then trades, then perpetual fills, then
income legs, then transfers, then repay legs, then expense legs, then fee legs.
A linked pair is booked when the earlier of its two events is processed, after
the income legs of both.
"""

from dataclasses import replace
from datetime import datetime
from decimal import Decimal

from litmus.accounting.engine import checks, series
from litmus.accounting.engine.arithmetic import fixed_context
from litmus.accounting.engine.checks import Linked, check
from litmus.accounting.engine.rollovers import Rollovers
from litmus.accounting.engine.rounding import round_result
from litmus.accounting.model import (
  Balance,
  Event,
  Link,
  Policy,
  Result,
  SeriesPoint,
)
from litmus.accounting.pricing import Pricing
from typing_extensions import Sequence

validate = checks.validate
"""Structural problems of a ledger, without prices (see `checks.validate`)."""


class Engine(Rollovers):
  """One run's mutable state. Use `run` unless you need the pieces."""

  def process(self, event: Event, linked: Linked):
    """Book one event: borrows, trades, fills, income, transfers, repays, expenses, then fees."""
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
    expenses = [l for l in event.legs if l.tag == 'expense' and not l.fee]
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
    self.fill(event)
    self.income(event)
    if transfers:
      self.transfer(event, transfers, linked)
    for leg in repays:
      self.repay(event, leg)
    for leg in expenses:
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


@fixed_context
def run(
  events: Sequence[Event],
  *,
  links: Sequence[Link] = (),
  policy: Policy,
  pricing: Pricing,
  strict: bool = False,
  grid: Sequence[datetime] = (),
) -> Result:
  """
  Run the books.

  Args:
    events: Any order; processed by time, ties in input order.
    links: Pairs of event ids that are one internal movement.
    policy: Cost method, lot scope, functional currency, valuation rules.
    pricing: Caller-implemented price source.
    strict: Raise `PriceGap` on the first missing price instead of reporting it.
    grid: Instants to emit a series point at, after every event up to and
      including each (policy 05 rule 39). Any order; duplicates are dropped.

  Returns:
    Lots, liabilities, realized PnL, flows, internal moves, balances, every
    price asked for, and the exceptions report. `complete` is false when anything was left unbooked.
    Money fields are rounded to `policy.minor_unit` when it is set. The run uses
    the engine's own decimal context, never the caller's (policy 05 rule 2.2).

  Raises:
    PriceGap: In strict mode, on the first missing price.
  """
  engine = Engine(policy, pricing, strict=strict)
  checked = check(events, links)
  engine.perp_compartments = {
    leg.compartment
    for event in checked.events
    for leg in event.legs
    if leg.tag == 'position'
  }
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
  instants = sorted({at for at in grid if at.tzinfo is not None})
  for at in grid:
    if at.tzinfo is None:
      engine.report(
        'invalid_grid', None, f'grid instant {at.isoformat()} has no timezone'
      )
  points: list[SeriesPoint] = []
  running = series.Running()

  def snapshot(until: datetime | None):
    """Emit the series points of every pending instant before `until`."""
    while instants and (until is None or instants[0] < until):
      at = instants.pop(0)
      running.advance(engine.realized, engine.flows)
      found, problem = series.point(
        at,
        valuer=engine.valuer,
        book=engine.book,
        journal=engine.journal,
        liabilities=[
          engine.liabilities[k].freeze() for k in sorted(engine.liabilities)
        ],
        perps=engine.perps,
        running=running,
        strict=strict,
      )
      points.append(found)
      if problem is not None:
        engine.exceptions.append(problem)
        engine.complete = False

  for e in checked.events:
    snapshot(e.time)
    if any(dependency in failed for dependency in e.depends_on):
      engine.complete = False
      engine.report('unbooked', e.id, 'required predecessor was not completely booked')
      failed.add(e.id)
      continue
    before = len(engine.exceptions)
    engine.process(e, checked.linked)
    if any(item.code == 'unbooked' for item in engine.exceptions[before:]):
      failed.add(e.id)
  snapshot(None)
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
    positions=tuple(engine.perps.open_positions()),
    series=tuple(points),
  )
  result = round_result(result, policy.minor_unit)
  journal, unbalanced = engine.journal.finish(result, policy.minor_unit)
  return replace(
    result,
    journal=journal,
    exceptions=(*result.exceptions, *unbalanced),
    complete=result.complete and not unbalanced,
  )
