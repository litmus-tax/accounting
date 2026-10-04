"""The cost identity after every group without prices, and the open-lot rows at `as_of` (policy 05 term 14 and rule 39.5.1)."""

from decimal import Decimal as D
import pytest
from litmus.accounting import run, FixedPricing
from litmus.accounting.engine import journal
from litmus.accounting.model import Result
from tests.conftest import leg, event, policy


def codes(result: Result) -> list[str]:
  """Exception codes of a result."""
  return [x.code for x in result.exceptions]


def test_the_identity_holds_without_any_price_for_holdings(method, scope):
  """A trade, a fee, income and a disposal: the identity holds after every group, and the engine asks only for the prices its events need (rule 1.5)."""
  events = [
    event('buy', 1, leg('BTC', '2'), leg('EUR', '-60000')),
    event('fee', 2, leg('BTC', '-0.01', tag='expense', fee=True)),
    event('yield', 3, leg('ETH', '1', tag='income', label='staking')),
    event('sell', 4, leg('BTC', '-1'), leg('EUR', '35000')),
  ]
  r = run(
    events,
    policy=policy(method, scope, minor_unit=2),
    pricing=FixedPricing({('BTC', 'EUR'): '30000', ('ETH', 'EUR'): '2000'}),
  )
  assert codes(r) == [] and r.complete
  assert {(p.asset, p.time.day) for p in r.prices} == {('BTC', 2), ('ETH', 3)}


def test_a_price_gap_leaves_the_identity_checked():
  """A day the price source misses leaves its leg unbooked, never the identity unchecked: the rest still balances."""
  events = [
    event('buy', 1, leg('BTC', '1'), leg('EUR', '-30000')),
    event('yield', 2, leg('ETH', '1', tag='income')),
  ]
  r = run(events, policy=policy(), pricing=FixedPricing({}))
  assert codes(r) == ['price_gap', 'unbooked']
  assert [(row.asset, row.cost) for row in r.open_rows] == [
    ('BTC', D('30000')),
    ('EUR', D('-30000')),
  ]


def test_a_broken_identity_is_reported_where_it_opens(monkeypatch: pytest.MonkeyPatch):
  """A booking that loses a contribution's journal line breaks the identity at its group, once, and the books are incomplete."""
  real = journal.Journal.add

  def lossy(self, event, account, amount, **kwargs):
    """Drop the `external` line of the event `in`."""
    if event.id == 'in' and account == 'external':
      return
    real(self, event, account, amount, **kwargs)

  monkeypatch.setattr(journal.Journal, 'add', lossy)
  events = [
    event('in', 1, leg('BTC', '1', tag='transfer')),
    event('sell', 2, leg('BTC', '-1'), leg('EUR', '30000')),
    event('buy', 3, leg('BTC', '1'), leg('EUR', '-30000')),
  ]
  r = run(events, policy=policy(), pricing=FixedPricing({('BTC', 'EUR'): '30000'}))
  broken = [x for x in r.exceptions if x.code == 'cost_identity']
  assert [(x.event, x.detail['difference']) for x in broken] == [('in', '30000')]
  assert not r.complete


def test_open_rows_cover_every_kind():
  """Holdings per lot key (the functional currency from its journal lines), a liability at carrying value and an open position at its entry basis."""
  events = [
    event('deposit', 1, leg('EUR', '10000', tag='transfer', label='deposit')),
    event('borrow', 2, leg('USDC', '1000', tag='borrow', label='aave')),
    event(
      'open',
      3,
      leg('BTC-PERP', '0.1', 'perp', tag='position'),
      leg('USDC', '-3000', 'perp', tag='notional'),
    ),
  ]
  r = run(
    events,
    policy=policy('fifo', 'compartment', minor_unit=2),
    pricing=FixedPricing({('USDC', 'EUR'): '0.9'}),
  )
  rows = {(row.kind, row.compartment, row.asset): row for row in r.open_rows}
  assert (rows['holding', 'A', 'EUR'].quantity, rows['holding', 'A', 'EUR'].cost) == (
    D('10000'),
    D('10000.00'),
  )
  assert rows['holding', 'A', 'USDC'].cost == D('900.00')
  assert (
    rows['liability', 'A', 'USDC'].quantity,
    rows['liability', 'A', 'USDC'].cost,
  ) == (
    D('1000'),
    D('900.00'),
  )
  position = rows['position', 'perp', 'BTC-PERP']
  assert (position.quantity, position.cost, position.settles_in) == (
    D('0.1'),
    D('3000'),
    'USDC',
  )
