"""
The double-entry journal the engine derives as it books (policy 05 rule 29).

Every booking primitive records signed lines (positive a debit, negative a
credit) on named accounts: holdings at cost per lot key, liabilities, realized
gains and losses, income and expense by label, and the external account for
movements across the books' boundary. Lines that restate a result row carry a
reference to it, so that after rounding they state the row's rounded figure.
Each event must balance before rounding; after rounding a residue within the
rounding bound goes to a `rounding` line.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing_extensions import Literal
from litmus.accounting.model import (
  Event,
  ExceptionItem,
  JournalAccount,
  JournalLine,
  Result,
)
from litmus.accounting.engine.rounding import money

RefKind = Literal['realized_cost', 'realized_pnl', 'flow', 'move']
"""Which rounded figure of which result row a line restates."""
TOLERANCE = Decimal('1e-18')
"""Relative imbalance tolerated before rounding: decimal division residue only."""
ZERO = Decimal(0)
"""Shared zero for the empty side of each line."""


@dataclass(frozen=True, slots=True)
class Line:
  """One unrounded journal line, positive a debit."""

  event: str
  time: datetime
  account: JournalAccount
  compartment: str | None
  asset: str | None
  label: str | None
  amount: Decimal
  ref: tuple[RefKind, int] | None


class Journal:
  """Lines in booking order."""

  def __init__(self):
    self.lines: list[Line] = []
    self.balances: dict[tuple[JournalAccount, str | None, str | None], Decimal] = {}
    """Running debits minus credits per `(account, compartment, asset)`."""

  def add(
    self,
    event: Event,
    account: JournalAccount,
    amount: Decimal,
    *,
    compartment: str | None = None,
    asset: str | None = None,
    label: str | None = None,
    ref: tuple[RefKind, int] | None = None,
  ):
    """Record a line; `amount` is positive for a debit and negative for a credit."""
    if amount == 0 and ref is None:
      return
    self.lines.append(
      Line(event.id, event.time, account, compartment, asset, label, amount, ref)
    )
    key = (account, compartment, asset)
    self.balances[key] = self.balances.get(key, Decimal(0)) + amount

  def rounded(self, line: Line, result: Result, minor_unit: int | None) -> Decimal:
    """The line's amount after rounding: the referenced row's rounded figure, else its own."""
    sign = Decimal(1) if line.amount >= 0 else Decimal(-1)
    if line.ref is None:
      return line.amount if minor_unit is None else money(line.amount, minor_unit)
    kind, index = line.ref
    if kind == 'realized_cost':
      return -result.realized[index].cost
    if kind == 'realized_pnl':
      return -result.realized[index].pnl
    if kind == 'flow':
      return sign * result.flows[index].value
    return sign * result.moves[index].cost

  def finish(
    self, result: Result, minor_unit: int | None
  ) -> tuple[tuple[JournalLine, ...], list[ExceptionItem]]:
    """
    The rounded journal of a rounded result, and an `unbalanced` exception for
    each event whose lines do not balance (policy 05 rule 29.2).
    """
    events: dict[str, list[Line]] = {}
    for line in self.lines:
      events.setdefault(line.event, []).append(line)
    out: list[JournalLine] = []
    problems: list[ExceptionItem] = []
    for event, lines in events.items():
      raw = sum((line.amount for line in lines), Decimal(0))
      scale = max((abs(line.amount) for line in lines), default=Decimal(0))
      balanced = abs(raw) <= TOLERANCE * max(scale, Decimal(1))
      if not balanced:
        problems.append(
          ExceptionItem(
            'unbalanced',
            event,
            f'journal of {event} does not balance: debits minus credits {raw}',
            {'difference': str(raw)},
          )
        )
      amounts = [self.rounded(line, result, minor_unit) for line in lines]
      for line, amount in zip(lines, amounts):
        if amount:
          out.append(journal_line(line, amount))
      residue = sum(amounts, Decimal(0))
      if residue and minor_unit is not None and balanced:
        out.append(
          JournalLine(
            event=event,
            time=lines[0].time,
            account='rounding',
            compartment=None,
            asset=None,
            label=None,
            debit=-residue if residue < 0 else ZERO,
            credit=residue if residue > 0 else ZERO,
          )
        )
    return tuple(out), problems


def journal_line(line: Line, amount: Decimal) -> JournalLine:
  """The output form of a line with its rounded amount."""
  return JournalLine(
    event=line.event,
    time=line.time,
    account=line.account,
    compartment=line.compartment,
    asset=line.asset,
    label=line.label,
    debit=amount if amount > 0 else ZERO,
    credit=-amount if amount < 0 else ZERO,
  )
