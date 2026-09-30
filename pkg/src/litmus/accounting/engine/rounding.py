"""
Rounding of money outputs to the functional currency's minor unit.

Quantities and prices are never rounded; only functional-currency amounts are.
Derived amounts (`pnl`, `unrealized`) are recomputed from the rounded parts so
the identities `pnl == proceeds - cost` and `unrealized == value - cost` hold
on the rounded output.
"""

from dataclasses import replace
from decimal import Decimal, ROUND_HALF_UP
from litmus.accounting.model import (
  Result,
  Valuation,
  Lot,
  Realized,
  Flow,
  Move,
  Position,
  Liability,
  LiabilityPosition,
  SeriesFlow,
  SeriesHolding,
  SeriesLiability,
  SeriesPoint,
  SeriesPosition,
)


def money(x: Decimal, minor_unit: int) -> Decimal:
  """Round half up to `minor_unit` decimal places."""
  return x.quantize(Decimal(1).scaleb(-minor_unit), rounding=ROUND_HALF_UP)


def round_lot(lot: Lot, minor_unit: int) -> Lot:
  """Round a lot's basis."""
  return replace(lot, cost=money(lot.cost, minor_unit))


def round_realized(r: Realized, minor_unit: int) -> Realized:
  """Round proceeds, cost and fees; recompute pnl."""
  proceeds = money(r.proceeds, minor_unit)
  cost = money(r.cost, minor_unit)
  return replace(
    r, proceeds=proceeds, cost=cost, pnl=proceeds - cost, fees=money(r.fees, minor_unit)
  )


def round_flow(f: Flow, minor_unit: int) -> Flow:
  """Round a flow's value."""
  return replace(f, value=money(f.value, minor_unit))


def round_move(m: Move, minor_unit: int) -> Move:
  """Round a move's carried basis."""
  return replace(m, cost=money(m.cost, minor_unit))


def round_liability(l: Liability, minor_unit: int) -> Liability:
  """Round a liability's basis."""
  return replace(l, cost=money(l.cost, minor_unit))


def round_liability_position(
  p: LiabilityPosition, minor_unit: int
) -> LiabilityPosition:
  """Round cost and value; recompute unrealized where it is reported."""
  cost = money(p.cost, minor_unit)
  value = None if p.value is None else money(p.value, minor_unit)
  unrealized = None if p.unrealized is None or value is None else cost - value
  return replace(p, cost=cost, value=value, unrealized=unrealized)


def round_position(p: Position, minor_unit: int) -> Position:
  """Round cost and value; recompute unrealized."""
  cost = money(p.cost, minor_unit)
  value = None if p.value is None else money(p.value, minor_unit)
  return replace(
    p, cost=cost, value=value, unrealized=None if value is None else value - cost
  )


def optional(x: Decimal | None, minor_unit: int) -> Decimal | None:
  """Round a money amount that may be missing."""
  return None if x is None else money(x, minor_unit)


def round_point(p: SeriesPoint, minor_unit: int) -> SeriesPoint:
  """
  Round a series point row by row; unrealized figures, total P&L and net
  assets are recomputed from the rounded parts (policy 05 rule 20).
  """
  held: list[SeriesHolding] = []
  for h in p.holdings:
    cost, value = money(h.cost, minor_unit), optional(h.value, minor_unit)
    held.append(
      replace(
        h, cost=cost, value=value, unrealized=None if value is None else value - cost
      )
    )
  owed: list[SeriesLiability] = []
  for l in p.liabilities:
    cost, value = money(l.cost, minor_unit), optional(l.value, minor_unit)
    owed.append(
      replace(
        l, cost=cost, value=value, unrealized=None if value is None else cost - value
      )
    )
  positions: list[SeriesPosition] = [
    replace(x, unrealized=optional(x.unrealized, minor_unit)) for x in p.positions
  ]
  flows: list[SeriesFlow] = [
    replace(f, value=money(f.value, minor_unit)) for f in p.flows
  ]
  realized = money(p.realized, minor_unit)
  contributions = money(p.contributions, minor_unit)
  if not p.complete:
    return replace(
      p,
      holdings=tuple(held),
      liabilities=tuple(owed),
      positions=tuple(positions),
      flows=tuple(flows),
      realized=realized,
      contributions=contributions,
    )
  parts = [
    *(h.unrealized for h in held),
    *(l.unrealized for l in owed),
    *(x.unrealized for x in positions),
  ]
  unrealized = sum((x for x in parts if x is not None), Decimal(0))
  earned = sum((f.value if f.kind == 'income' else -f.value for f in flows), Decimal(0))
  net = (
    sum((h.value for h in held if h.value is not None), Decimal(0))
    - sum((l.value for l in owed if l.value is not None), Decimal(0))
    + sum((x.unrealized for x in positions if x.unrealized is not None), Decimal(0))
  )
  return replace(
    p,
    holdings=tuple(held),
    liabilities=tuple(owed),
    positions=tuple(positions),
    flows=tuple(flows),
    realized=realized,
    contributions=contributions,
    unrealized=unrealized,
    total_pnl=realized + earned + unrealized,
    net_assets=net,
  )


def round_result(result: Result, minor_unit: int | None) -> Result:
  """Round every money field of a result, or return it unchanged when `minor_unit` is unset."""
  if minor_unit is None:
    return result
  return replace(
    result,
    lots=tuple(round_lot(l, minor_unit) for l in result.lots),
    liabilities=tuple(round_liability(l, minor_unit) for l in result.liabilities),
    realized=tuple(round_realized(r, minor_unit) for r in result.realized),
    flows=tuple(round_flow(f, minor_unit) for f in result.flows),
    moves=tuple(round_move(m, minor_unit) for m in result.moves),
    series=tuple(round_point(p, minor_unit) for p in result.series),
  )


def round_valuation(valuation: Valuation, minor_unit: int | None) -> Valuation:
  """Round every money field of a valuation, or return it unchanged when `minor_unit` is unset."""
  if minor_unit is None:
    return valuation
  return replace(
    valuation,
    positions=tuple(round_position(p, minor_unit) for p in valuation.positions),
    liabilities=tuple(
      round_liability_position(p, minor_unit) for p in valuation.liabilities
    ),
  )
