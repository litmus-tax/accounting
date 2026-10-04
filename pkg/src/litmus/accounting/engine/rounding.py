"""
Rounding of money outputs to the functional currency's minor unit (policy 05
rule 20.1).

Quantities and prices are never rounded; only functional-currency amounts are.
They are rounded at output by running totals: each journal account (an
`(account, compartment, asset, label)` stream) keeps its exact running total,
rounded half up after every line, and a line is the change of that rounded
total. Every line is then within one minor unit of its exact amount, and the
sum of an account's lines over any stretch is its exact total rounded at each
end, so many rows smaller than a minor unit never bias a total.

The result rows restate the journal: a disposal's cost, its P&L and a flow's
value are their lines, and a disposal's proceeds are its cost plus its P&L, so
P&L = proceeds − cost holds on every rounded row and a disposal at cost shows
no P&L. The open-lot
rows are the journal's balances, so what is held at cost is the sum of the rows
that built it and the cost identity holds on the rounded books (rule 39.5.1),
with the journal's `rounding` account holding each event's residue.
"""

from collections.abc import Sequence
from dataclasses import replace
from decimal import Decimal, ROUND_HALF_UP

from litmus.accounting.engine.journal import Line
from litmus.accounting.engine.totals import EXACT, ZERO
from litmus.accounting.model import (
  Flow,
  Lot,
  Move,
  OpenRow,
  Realized,
  Result,
)

Stream = tuple[str, str | None, str | None, str | None]
"""A running total: `(account, compartment, asset, label)`."""


def money(x: Decimal, minor_unit: int) -> Decimal:
  """Round half up to `minor_unit` decimal places."""
  return x.quantize(Decimal(1).scaleb(-minor_unit), rounding=ROUND_HALF_UP)


class Running:
  """Exact running totals per stream, each rounded after every amount."""

  def __init__(self, minor_unit: int):
    self.minor_unit = minor_unit
    self.totals: dict[Stream, Decimal] = {}
    """Exact running total per stream."""
    self.rounded: dict[Stream, Decimal] = {}
    """Sum of the rounded amounts per stream: its exact total, rounded."""

  def add(self, stream: Stream, amount: Decimal) -> Decimal:
    """Add `amount` to `stream` and return its rounded share: the change of the stream's rounded total."""
    before = self.totals.get(stream, ZERO)
    after = EXACT.add(before, amount)
    self.totals[stream] = after
    share = money(after, self.minor_unit) - money(before, self.minor_unit)
    self.rounded[stream] = self.rounded.get(stream, ZERO) + share
    return share


def carried(lines: Sequence[Line], minor_unit: int) -> tuple[list[Decimal], Running]:
  """
  Each journal line's rounded amount, by running total per account stream, and
  the running totals, whose rounded sums are the accounts' rounded balances.
  """
  running = Running(minor_unit)
  amounts = [
    running.add((line.account, line.compartment, line.asset, line.label), line.amount)
    for line in lines
  ]
  return amounts, running


def round_lots(lots: Sequence[Lot], minor_unit: int) -> tuple[Lot, ...]:
  """Round each lot's basis by running total per lot key, so a key's lots sum to its rounded basis."""
  running = Running(minor_unit)
  return tuple(
    replace(lot, cost=running.add(('lot', lot.compartment, lot.asset, None), lot.cost))
    for lot in lots
  )


def round_result(
  result: Result,
  minor_unit: int | None,
  lines: Sequence[Line] = (),
  amounts: Sequence[Decimal] = (),
  running: Running | None = None,
) -> Result:
  """
  Round every money field of a result to the journal's rounded lines
  (`carried`), or return it unchanged when `minor_unit` is unset.

  Args:
    result: The exact result.
    minor_unit: Decimal places of the functional currency.
    lines: The journal's exact lines, in booking order.
    amounts: Each line's rounded amount (`carried`).
    running: The journal's running totals (`carried`), whose rounded balances
      are the open-lot rows' remaining cost and the liabilities' carrying value.
  """
  if minor_unit is None:
    return result
  costs: dict[int, Decimal] = {}
  gains: dict[int, Decimal] = {}
  flows: dict[int, Decimal] = {}
  moves: dict[int, Decimal] = {}
  for line, amount in zip(lines, amounts):
    if line.ref is None:
      continue
    kind, index = line.ref
    if kind == 'realized_cost':
      costs[index] = -amount
    elif kind == 'realized_pnl':
      gains[index] = -amount
    elif kind == 'flow':
      flows[index] = abs(amount)
    elif kind == 'move' and index not in moves:
      moves[index] = abs(amount)
  realized: list[Realized] = []
  for index, row in enumerate(result.realized):
    cost = costs.get(index, money(row.cost, minor_unit))
    pnl = gains.get(index, money(row.proceeds, minor_unit) - cost)
    realized.append(
      replace(
        row, proceeds=cost + pnl, cost=cost, pnl=pnl, fees=money(row.fees, minor_unit)
      )
    )
  balances = running.rounded if running is not None else {}
  return replace(
    result,
    lots=round_lots(result.lots, minor_unit),
    liabilities=tuple(
      replace(
        liability,
        cost=-balances.get(
          ('liability', liability.compartment, liability.asset, None),
          -money(liability.cost, minor_unit),
        ),
      )
      for liability in result.liabilities
    ),
    realized=tuple(realized),
    flows=tuple(
      round_flow(flow, flows.get(index), minor_unit)
      for index, flow in enumerate(result.flows)
    ),
    moves=tuple(
      round_move(move, moves.get(index), minor_unit)
      for index, move in enumerate(result.moves)
    ),
    open_rows=tuple(
      round_open_row(row, balances, minor_unit) for row in result.open_rows
    ),
  )


def round_flow(flow: Flow, value: Decimal | None, minor_unit: int) -> Flow:
  """A flow at its line's rounded value; rounded on its own when it has no line."""
  return replace(flow, value=money(flow.value, minor_unit) if value is None else value)


def round_move(move: Move, cost: Decimal | None, minor_unit: int) -> Move:
  """A move at its source line's rounded basis; rounded on its own when it has no line."""
  return replace(move, cost=money(move.cost, minor_unit) if cost is None else cost)


def round_open_row(
  row: OpenRow, balances: dict[Stream, Decimal], minor_unit: int
) -> OpenRow:
  """
  An open-lot row at its journal balance: a holding's or opaque position's
  rounded `holding` balance, a liability's rounded carrying value. A position's
  entry basis is in its settlement asset and is not money.
  """
  if row.kind == 'position':
    return row
  if row.kind == 'liability':
    balance = balances.get(('liability', row.compartment, row.asset, None))
    cost = money(row.cost, minor_unit) if balance is None else -balance
  else:
    balance = balances.get(('holding', row.compartment, row.asset, None))
    cost = money(row.cost, minor_unit) if balance is None else balance
  return replace(row, cost=cost)
