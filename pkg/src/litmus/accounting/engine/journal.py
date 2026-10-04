"""
The double-entry journal the engine derives as it books (policy 05 rule 29).

Every booking primitive records signed lines (positive a debit, negative a
credit) on named accounts: holdings at cost per lot key, liabilities, realized
gains and losses, income and expense by label, and the external account for
movements across the books' boundary. A line that a result row restates
carries a reference to it. Each event must balance before rounding. Lines are
rounded by running total per account (`rounding.carried`), the result rows
restate their rounded lines, and a residue left after rounding goes to a
`rounding` line.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing_extensions import Literal, Sequence
from litmus.accounting.model import (
  Event,
  ExceptionItem,
  JournalAccount,
  JournalLine,
)

RefKind = Literal['realized_cost', 'realized_pnl', 'flow', 'move']
"""Which figure of which result row restates a line."""
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

  def __init__(self, cash: str):
    self.cash = cash
    """The functional currency: held without lots, its holding is its journal lines."""
    self.lines: list[Line] = []
    self.balances: dict[tuple[JournalAccount, str | None, str | None], Decimal] = {}
    """Running debits minus credits per `(account, compartment, asset)`."""
    self.totals: dict[JournalAccount, Decimal] = {}
    """Running debits minus credits per account kind."""
    self.held_cash = Decimal(0)
    """Running debits minus credits of the functional currency's holding lines."""

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
    self.totals[account] = self.totals.get(account, Decimal(0)) + amount
    if account == 'holding' and asset == self.cash:
      self.held_cash += amount

  def finish(
    self, amounts: Sequence[Decimal] | None = None
  ) -> tuple[tuple[JournalLine, ...], list[ExceptionItem]]:
    """
    The rounded journal, and an `unbalanced` exception for each event whose
    lines do not balance (policy 05 rule 29.2).

    Args:
      amounts: Each line's rounded amount, in line order (`rounding.carried`);
        None when nothing is rounded.
    """
    events: dict[str, list[int]] = {}
    for i, line in enumerate(self.lines):
      events.setdefault(line.event, []).append(i)
    out: list[JournalLine] = []
    problems: list[ExceptionItem] = []
    for event, indices in events.items():
      lines = [self.lines[i] for i in indices]
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
      rounded = [
        self.lines[i].amount if amounts is None else amounts[i] for i in indices
      ]
      for line, amount in zip(lines, rounded):
        if amount:
          out.append(journal_line(line, amount))
      residue = sum(rounded, Decimal(0))
      if residue and amounts is not None and balanced:
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
