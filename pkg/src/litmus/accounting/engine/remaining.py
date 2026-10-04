"""
What the books hold at cost, without prices (policy 05 term 14 and rule
39.5.1): the cost identity checked after every atomic group, and the open-lot
rows at the end of the run, its `as_of`.

The identity is the journal's trial balance read from the engine's own state:
assets at cost (the lot book's basis, opaque positions included, and the
functional currency's holding lines) minus liabilities at carrying value equal
net contributions (the `external` account) plus realized P&L plus income minus
expenses (the result rows). Open positions book nothing, so they are not in it.
"""

from collections.abc import Iterable, Mapping, Sequence
from decimal import Decimal

from litmus.accounting.engine.journal import TOLERANCE, Journal
from litmus.accounting.engine.lots import LotBook
from litmus.accounting.engine.opaque import PREFIX
from litmus.accounting.engine.totals import ZERO, LotKey
from litmus.accounting.model import (
  ExceptionItem,
  Flow,
  Liability,
  OpenRow,
  PerpPosition,
  Realized,
)


class Identity:
  """The cost identity's running figures, advanced over the engine's rows, and the difference last reported."""

  def __init__(self):
    self.realized = 0
    self.flows = 0
    self.gains = ZERO
    """Realized P&L to date."""
    self.earned = ZERO
    """Income minus expenses to date."""
    self.reported = ZERO
    """The difference the last `cost_identity` item stated; zero while the identity holds."""

  def check(
    self,
    event: str,
    *,
    book: LotBook,
    journal: Journal,
    liabilities: Iterable[Decimal],
    realized: Sequence[Realized],
    flows: Sequence[Flow],
  ) -> ExceptionItem | None:
    """
    Check the identity after the group whose last event is `event`; a
    `cost_identity` item when the difference opens or changes there.

    Args:
      event: The group's last event, which the item names.
      book: The lot book, whose running basis is the assets at cost.
      journal: The journal, whose functional-currency holding lines and
        `external` account it reads.
      liabilities: Each open liability's carrying value.
      realized: Every realized row so far.
      flows: Every flow so far.
    """
    for row in realized[self.realized :]:
      self.gains += row.pnl
    self.realized = len(realized)
    for flow in flows[self.flows :]:
      self.earned += flow.value if flow.kind == 'income' else -flow.value
    self.flows = len(flows)
    assets = book.cost + journal.held_cash
    owed = sum(liabilities, ZERO)
    contributions = -journal.totals.get('external', ZERO)
    difference = assets - owed - (contributions + self.gains + self.earned)
    scale = max(
      abs(assets), abs(owed), abs(contributions), abs(self.gains), abs(self.earned)
    )
    bound = TOLERANCE * max(scale, Decimal(1))
    if abs(difference - self.reported) <= bound:
      return None
    change = difference - self.reported
    self.reported = ZERO if abs(difference) <= bound else difference
    return ExceptionItem(
      'cost_identity',
      event,
      f'after {event}: assets at cost {assets} minus liabilities {owed} differ from '
      f'contributions {contributions} plus realized {self.gains} plus income less '
      f'expenses {self.earned} by {difference} (policy 05 rule 39.5.1)',
      {'difference': str(difference), 'change': str(change)},
    )


def open_rows(
  book: LotBook,
  journal: Journal,
  *,
  liabilities: Sequence[Liability],
  positions: Sequence[PerpPosition],
  contents: Mapping[LotKey, Mapping[str, Decimal]],
) -> list[OpenRow]:
  """
  The open-lot rows at the end of the run (term 14): quantity and basis per lot
  key, the functional currency's from its journal lines, every opaque position
  whose contents are not empty even with no cost left, each liability at its
  carrying value, and each open position at its entry basis.

  Args:
    book: The lot book.
    journal: The journal (the functional currency's holdings).
    liabilities: The open liabilities.
    positions: The open perpetual positions.
    contents: Each opaque position's lot key and its event-implied contents.
  """
  fc = journal.cash
  held: dict[LotKey, tuple[Decimal, Decimal]] = {}
  for key, quantity in book.totals.items():
    cost = book.costs[key]
    if quantity or cost:
      held[key] = (quantity, cost)
  for key, inside in contents.items():
    if key not in held and any(inside.values()):
      held[key] = (ZERO, ZERO)
  for (account, compartment, asset), amount in journal.balances.items():
    if account == 'holding' and asset == fc and amount:
      held[(compartment, fc)] = (amount, amount)
  rows = [
    OpenRow(
      kind='opaque' if asset.startswith(PREFIX) else 'holding',
      compartment=compartment,
      asset=asset,
      quantity=quantity,
      cost=cost,
    )
    for (compartment, asset), (quantity, cost) in sorted(held.items(), key=str)
  ]
  rows += [
    OpenRow(
      kind='liability',
      compartment=liability.compartment,
      asset=liability.asset,
      quantity=liability.quantity,
      cost=liability.cost,
    )
    for liability in liabilities
  ]
  rows += [
    OpenRow(
      kind='position',
      compartment=position.compartment,
      asset=position.instrument,
      quantity=position.size,
      cost=position.entry,
      settles_in=position.settles_in,
    )
    for position in positions
  ]
  return rows
