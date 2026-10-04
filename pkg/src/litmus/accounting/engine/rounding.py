"""
Rounding of money outputs to the functional currency's minor unit.

Quantities and prices are never rounded; only functional-currency amounts are.
The derived `pnl` is recomputed from the rounded parts so the identity
`pnl == proceeds - cost` holds on the rounded output.
"""

from dataclasses import replace
from decimal import Decimal, ROUND_HALF_UP
from litmus.accounting.model import (
  Result,
  Lot,
  Realized,
  Flow,
  Move,
  Liability,
  OpenRow,
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


def round_open_row(row: OpenRow, minor_unit: int) -> OpenRow:
  """Round an open-lot row's remaining cost; a position's entry basis is in its settlement asset and is not money."""
  if row.kind == 'position':
    return row
  return replace(row, cost=money(row.cost, minor_unit))


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
    open_rows=tuple(round_open_row(r, minor_unit) for r in result.open_rows),
  )
