"""
The P&L series (policy 05 rule 39): what the books hold and have earned at each
grid instant, valued at market as the engine processes events in time order,
with the check that total P&L equals net assets at market minus net
contributions.
"""

from datetime import datetime
from decimal import Decimal
from typing_extensions import Mapping, Sequence
from litmus.accounting.model import (
  ExceptionItem,
  Flow,
  Liability,
  Realized,
  SeriesFlow,
  SeriesHolding,
  SeriesLiability,
  SeriesPoint,
  SeriesPosition,
)
from litmus.accounting.pricing import PriceGap, Valuer, effective_time
from litmus.accounting.engine.journal import Journal, TOLERANCE
from litmus.accounting.engine.lots import LotBook
from litmus.accounting.engine.perps import PositionBook
from litmus.accounting.engine.totals import LotKey


class Missing:
  """Collects the assets an instant cannot price, and prices what it can."""

  def __init__(self, valuer: Valuer, at: datetime, *, strict: bool):
    self.valuer = valuer
    self.at = at
    self.strict = strict
    self.assets: list[str] = []

  def value(self, asset: str, quantity: Decimal) -> Decimal | None:
    """Market value of `quantity` of `asset` at the instant, `None` on a gap."""
    try:
      return self.valuer.value(asset, quantity, self.at)
    except PriceGap:
      return self.missing(asset)

  def mark(self, instrument: str, settles_in: str) -> Decimal | None:
    """Price of one `instrument` in `settles_in` at the instant, `None` on a gap."""
    try:
      return self.valuer.ask(
        instrument, settles_in, effective_time(self.at, self.valuer.policy)
      )
    except PriceGap:
      return self.missing(instrument)

  def missing(self, asset: str) -> None:
    """Record a gap; in strict mode raise it."""
    if self.strict:
      raise PriceGap(asset, self.valuer.policy.functional_currency, self.at)
    if asset not in self.assets:
      self.assets.append(asset)
    return None


def holdings(
  book: LotBook,
  journal: Journal,
  fc: str,
  opaque: Mapping[LotKey, Mapping[str, Decimal]] = {},
) -> list[tuple[str | None, str, Decimal, Decimal]]:
  """
  Quantity and cost per lot key; the functional currency's from its journal
  lines; and every opaque position whose contents are not empty, even with no
  cost left.
  """
  out: dict[tuple[str | None, str], tuple[Decimal, Decimal]] = {}
  for key, quantity in book.totals.items():
    cost = book.costs[key]
    if quantity or cost:
      out[key] = (quantity, cost)
  for key, contents in opaque.items():
    if key not in out and any(contents.values()):
      out[key] = (Decimal(0), Decimal(0))
  for (account, compartment, asset), amount in journal.balances.items():
    if account == 'holding' and asset == fc and amount:
      out[(compartment, fc)] = (amount, amount)
  return [(c, a, q, cost) for (c, a), (q, cost) in sorted(out.items(), key=str)]


def contents_value(prices: Missing, contents: Mapping[str, Decimal]) -> Decimal | None:
  """An opaque position's value: its contents at market, at least zero (rule 14.6); `None` on a gap."""
  values = [prices.value(asset, q) for asset, q in sorted(contents.items()) if q]
  if any(value is None for value in values):
    return None
  return max(sum((v for v in values if v is not None), Decimal(0)), Decimal(0))


class Running:
  """Cumulative realized P&L and flows, advanced incrementally over the engine's rows."""

  def __init__(self):
    self.realized = 0
    self.flows = 0
    self.gains = Decimal(0)
    self.totals: dict[tuple[str, str | None], Decimal] = {}

  def advance(self, realized: Sequence[Realized], flows: Sequence[Flow]):
    """Add the rows appended since the last call."""
    for row in realized[self.realized :]:
      self.gains += row.pnl
    self.realized = len(realized)
    for flow in flows[self.flows :]:
      key = (flow.kind, flow.label)
      self.totals[key] = self.totals.get(key, Decimal(0)) + flow.value
    self.flows = len(flows)

  def flow_totals(self) -> list[SeriesFlow]:
    """Cumulative flows by kind and label."""
    return [
      SeriesFlow(
        kind='income' if kind == 'income' else 'expense', label=label, value=value
      )
      for (kind, label), value in sorted(self.totals.items(), key=str)
    ]


def point(
  at: datetime,
  *,
  valuer: Valuer,
  book: LotBook,
  journal: Journal,
  liabilities: Sequence[Liability],
  perps: PositionBook,
  running: Running,
  strict: bool,
  opaque: Mapping[LotKey, Mapping[str, Decimal]] = {},
) -> tuple[SeriesPoint, ExceptionItem | None]:
  """
  The series point at `at` from the engine's state after every event up to it,
  and a `series_mismatch` exception when its check fails (rule 39.5). An opaque
  position (`opaque`: its lot key to its contents) is valued from its
  event-implied contents at the instant's prices, never below zero (rules 14.6
  and 14.12).
  """
  fc = valuer.policy.functional_currency
  prices = Missing(valuer, at, strict=strict)
  held: list[SeriesHolding] = []
  for compartment, asset, quantity, cost in holdings(book, journal, fc, opaque):
    contents = opaque.get((compartment, asset))
    value = (
      prices.value(asset, quantity)
      if contents is None
      else contents_value(prices, contents)
    )
    held.append(
      SeriesHolding(
        compartment=compartment,
        asset=asset,
        quantity=quantity,
        cost=cost,
        value=value,
        unrealized=None if value is None else value - cost,
      )
    )
  owed: list[SeriesLiability] = []
  for liability in liabilities:
    if liability.quantity == 0:
      continue
    value = prices.value(liability.asset, liability.quantity)
    owed.append(
      SeriesLiability(
        liability=liability.id,
        compartment=liability.compartment,
        asset=liability.asset,
        quantity=liability.quantity,
        cost=liability.cost,
        value=value,
        unrealized=None if value is None else liability.cost - value,
      )
    )
  open_: list[SeriesPosition] = []
  for position in perps.open_positions():
    mark = prices.mark(position.instrument, position.settles_in)
    result = None if mark is None else position.size * mark - position.entry
    unrealized = None if result is None else prices.value(position.settles_in, result)
    open_.append(
      SeriesPosition(
        compartment=position.compartment,
        instrument=position.instrument,
        settles_in=position.settles_in,
        size=position.size,
        entry=position.entry,
        mark=mark,
        unrealized=unrealized,
      )
    )
  gains = running.gains
  totals = running.flow_totals()
  earned = sum(
    (f.value if f.kind == 'income' else -f.value for f in totals), Decimal(0)
  )
  contributions = -sum(
    (
      amount
      for (account, _, _), amount in journal.balances.items()
      if account == 'external'
    ),
    Decimal(0),
  )
  parts = [
    *(h.unrealized for h in held),
    *(l.unrealized for l in owed),
    *(p.unrealized for p in open_),
  ]
  complete = not prices.assets and all(part is not None for part in parts)
  unrealized = total = net = None
  problem = None
  if complete:
    unrealized = sum((part for part in parts if part is not None), Decimal(0))
    total = gains + earned + unrealized
    net = (
      sum((h.value for h in held if h.value is not None), Decimal(0))
      - sum((l.value for l in owed if l.value is not None), Decimal(0))
      + sum((p.unrealized for p in open_ if p.unrealized is not None), Decimal(0))
    )
    difference = total - (net - contributions)
    scale = max(abs(total), abs(net), Decimal(1))
    if abs(difference) > TOLERANCE * scale:
      problem = ExceptionItem(
        'series_mismatch',
        None,
        f'series point at {at.isoformat()}: total P&L {total} differs from net '
        f'assets {net} minus contributions {contributions} by {difference}',
        {'at': at.isoformat(), 'difference': str(difference)},
      )
  return (
    SeriesPoint(
      at=at,
      holdings=tuple(held),
      liabilities=tuple(owed),
      positions=tuple(open_),
      realized=gains,
      flows=tuple(totals),
      unrealized=unrealized,
      total_pnl=total,
      net_assets=net,
      contributions=contributions,
      complete=complete,
      missing=tuple(prices.assets),
    ),
    problem,
  )
