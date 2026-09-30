"""Perpetuals on a settled basis: open positions, engine-realized P&L on notional compartments, settlement shortfalls."""

from decimal import Decimal as D
import pytest
from litmus.accounting import run, validate, FixedPricing
from litmus.accounting.model import CostMethod, Leg, Result
from tests.conftest import leg, event, link, policy, t


def codes(result: Result) -> list[str]:
  """Exception codes of a result."""
  return [x.code for x in result.exceptions]


def size(
  qty: str, comp: str = 'P', *, price: str | None = None, settles_in: str | None = None
) -> Leg:
  """A `position` size leg; `price` and `settles_in` make it a `pnl` fill."""
  return Leg(
    'BTC-PERP',
    D(qty),
    comp,
    'position',
    price=None if price is None else D(price),
    settles_in=settles_in,
  )


def notional(qty: str, comp: str = 'P', asset: str = 'USDC') -> Leg:
  """The notional cash leg of a fill on a `notional` compartment."""
  return Leg(asset, D(qty), comp, 'notional')


FLAT = FixedPricing({('USDC', 'EUR'): '1', ('USDT', 'EUR'): '1'})


def collateral(comp: str = 'P', asset: str = 'USDC', qty: str = '10000'):
  """Collateral bought with EUR in a perp compartment."""
  return event(
    'collateral', 1, leg(asset, qty, comp), leg('EUR', f'-{qty}', comp), hour=1
  )


def realized_pnl(result: Result) -> list[tuple[str, D, str | None]]:
  """The `realized_pnl` flows: kind, value and instrument."""
  return [
    (f.kind, f.value, f.instrument) for f in result.flows if f.label == 'realized_pnl'
  ]


@pytest.mark.parametrize(
  ('method', 'expected'),
  [('average', '3000'), ('fifo', '4000'), ('lifo', '2000'), ('hifo', '2000')],
)
def test_notional_reduction_realizes_under_perp_cost_method(
  method: CostMethod, expected: str
):
  """Built in steps and reduced once, the realized P&L follows `perp_cost_method`; the book's `cost_method` is irrelevant."""
  # policy 05 rules 8.2 and 17 (perp_cost_method), 18.1
  events = [
    collateral(),
    event('buy-1', 2, size('1'), notional('-60000')),
    event('buy-2', 3, size('1'), notional('-62000')),
    event('sell', 4, size('-1'), notional('64000')),
  ]
  r = run(events, policy=policy('fifo', perp_cost_method=method), pricing=FLAT)
  assert codes(r) == [] and r.complete
  assert realized_pnl(r) == [('income', D(expected), 'BTC-PERP')]
  (open_,) = r.positions
  assert (open_.size, open_.settlement, open_.settles_in) == (
    D('1'),
    'notional',
    'USDC',
  )


def test_perp_cost_method_defaults_to_average():
  """`average` is the default, the method venues settle by."""
  # policy 05 rule 17
  assert policy().perp_cost_method == 'average'


def test_a_short_is_the_mirror_image():
  """Selling first opens a negative position; covering lower realizes a gain."""
  # policy 05 rule 8.2; guide §4.2
  events = [
    collateral(),
    event('sell', 2, size('-1'), notional('60000')),
    event('cover', 3, size('1'), notional('-58000')),
  ]
  r = run(events, policy=policy(), pricing=FLAT)
  assert realized_pnl(r) == [('income', D('2000'), 'BTC-PERP')]
  assert r.positions == ()


def test_a_fill_crossing_zero_closes_then_opens():
  """A sell larger than the long closes it and opens a short at the fill price."""
  # policy 05 rule 8.2
  events = [
    collateral(),
    event('buy', 2, size('1'), notional('-60000')),
    event('flip', 3, size('-2'), notional('122000')),
  ]
  r = run(events, policy=policy(), pricing=FLAT)
  assert realized_pnl(r) == [('income', D('1000'), 'BTC-PERP')]
  (short,) = r.positions
  assert (short.size, short.entry) == (D('-1'), D('-61000'))


def test_positions_never_net_across_accounts():
  """A long on one account and a short on another are two open positions."""
  # policy 05 rule 8.1; guide §4.6
  events = [
    collateral('A'),
    collateral('B'),
    event('long', 2, size('1', 'A'), notional('-60000', 'A')),
    event('short', 2, size('-1', 'B'), notional('61000', 'B')),
  ]
  r = run(events, policy=policy(), pricing=FLAT)
  assert [(p.compartment, p.size, p.entry) for p in r.positions] == [
    ('A', D('1'), D('60000')),
    ('B', D('-1'), D('-61000')),
  ]
  assert realized_pnl(r) == []


def test_collateral_is_untouched_by_fills_in_a_eur_book():
  """Probe 7: realized P&L is booked in USDC at its rate at the close; collateral lots keep their cost."""
  # policy 05 rules 8.2, 8.4 and 8.5 (probe 7)

  class Usdc:
    """USDC/EUR 0.90 at purchase, 0.92 at the open, 0.93 at the close."""

    def price(self, asset, quote, time, *, source):
      """Step price for USDC."""
      return D('0.90') if time < t(2) else D('0.92') if time < t(3) else D('0.93')

  events = [
    event('buy', 1, leg('USDC', '10000', 'P'), leg('EUR', '-9000', 'P')),
    event('open', 2, size('1'), notional('-60000')),
    event('close', 3, size('-1'), notional('62000')),
  ]
  r = run(events, policy=policy(minor_unit=2), pricing=Usdc())
  assert codes(r) == []
  assert realized_pnl(r) == [('income', D('1860.00'), 'BTC-PERP')]
  assert r.realized == ()
  assert [(l.quantity, l.cost) for l in r.lots] == [
    (D('10000'), D('9000.00')),
    (D('2000'), D('1860.00')),
  ]


def test_pnl_positions_keep_average_entry_and_book_only_venue_legs():
  """On a `pnl` compartment the engine realizes nothing; the venue's `realized_pnl` leg is the result."""
  # policy 05 rules 8.3 and 7 (row 5)
  events = [
    collateral('F', 'USDT'),
    event('buy-1', 2, size('1', 'F', price='60000', settles_in='USDT')),
    event('buy-2', 3, size('1', 'F', price='62000', settles_in='USDT')),
    event(
      'sell',
      4,
      size('-1', 'F', price='64000', settles_in='USDT'),
      leg('USDT', '3000', 'F', 'income', label='realized_pnl'),
    ),
  ]
  r = run(events, policy=policy(perp_cost_method='fifo'), pricing=FLAT)
  assert codes(r) == []
  assert realized_pnl(r) == [('income', D('3000'), None)]
  (open_,) = r.positions
  assert (open_.settlement, open_.size, open_.entry) == ('pnl', D('1'), D('61000'))


def test_a_negative_settlement_balance_is_a_liability_cleared_by_later_inflows():
  """Fees settled in USDT against USDC collateral owe USDT; a later USDT gain clears it first."""
  # policy 05 rule 8.6 (decision 14); guide §4.6

  class Usdt:
    """USDT at 1.00, then 1.01 from day 4."""

    def price(self, asset, quote, time, *, source):
      """Step price for USDT; USDC at 1."""
      if asset == 'USDC':
        return D('1')
      return D('1') if time < t(4) else D('1.01')

  events = [
    collateral('F', 'USDC', '400'),
    event(
      'open',
      2,
      size('10', 'F', price='2', settles_in='USDT'),
      leg('USDT', '-3', 'F', 'expense', fee=True, label='fee'),
    ),
    event('gain', 4, leg('USDT', '5', 'F', 'income', label='realized_pnl')),
  ]
  owing = run(events[:2], policy=policy(), pricing=Usdt())
  assert codes(owing) == []
  (debt,) = owing.liabilities
  assert (debt.compartment, debt.asset, debt.quantity, debt.cost, debt.label) == (
    'F',
    'USDT',
    D('3'),
    D('3'),
    'settlement',
  )
  assert [(l.asset, l.quantity) for l in owing.lots] == [('USDC', D('400'))]
  cleared = run(events, policy=policy(), pricing=Usdt())
  assert cleared.liabilities == ()
  assert [(x.quantity, x.proceeds, x.cost, x.pnl) for x in cleared.realized] == [
    (D('3'), D('-3.03'), D('-3'), D('-0.03'))
  ]
  assert [(l.asset, l.quantity, l.cost) for l in cleared.lots] == [
    ('USDC', D('400'), D('400')),
    ('USDT', D('2'), D('2.02')),
  ]


@pytest.mark.parametrize('scope', ['global', 'compartment'])
def test_a_linked_deposit_clears_the_shortfall(scope):
  """USDT moved in by a link is disposed of at market to clear what the perp compartment owes."""
  # policy 05 rule 8.6
  events = [
    event('buy', 1, leg('USDT', '10', 'W'), leg('EUR', '-10', 'W')),
    event(
      'open',
      2,
      size('1', 'F', price='2', settles_in='USDT'),
      leg('USDT', '-3', 'F', 'expense', fee=True, label='fee'),
    ),
    event('out', 3, leg('USDT', '-4', 'W', 'transfer')),
    event('in', 3, leg('USDT', '4', 'F', 'transfer')),
  ]
  r = run(
    events, links=[link('out', 'in')], policy=policy(lot_scope=scope), pricing=FLAT
  )
  assert codes(r) == [] and r.liabilities == ()
  assert sum(l.quantity for l in r.lots) == D('7')
  assert [x.lots for x in r.realized][-1] == ('liability-1',)


def test_global_lots_do_not_cover_a_perp_compartment_shortfall():
  """Under global lots, USDT held elsewhere does not pay what the perp compartment owes."""
  # policy 05 rule 8.6: the liability is (account, perp compartment, settlement asset)
  events = [
    event('buy', 1, leg('USDT', '10', 'W'), leg('EUR', '-10', 'W')),
    event(
      'open',
      2,
      size('1', 'F', price='2', settles_in='USDT'),
      leg('USDT', '-3', 'F', 'expense', fee=True, label='fee'),
    ),
  ]
  r = run(events, policy=policy('fifo', 'global'), pricing=FLAT)
  assert [(l.compartment, l.quantity) for l in r.liabilities] == [('F', D('3'))]
  assert [(l.asset, l.quantity) for l in r.lots] == [('USDT', D('10'))]


@pytest.mark.parametrize(
  'legs',
  [
    (size('1'),),
    (notional('-60000'),),
    (size('1'), size('1'), notional('-120000')),
    (size('1'), notional('60000')),
  ],
  ids=['no-price', 'no-size', 'two-sizes', 'same-sign'],
)
def test_a_fill_that_cannot_be_read_is_unbooked(legs: tuple[Leg, ...]):
  """A missing or ambiguous size or cash is unbooked with an item, never priced at zero."""
  # policy 05 rule 8.7
  r = run([collateral(), event('fill', 2, *legs)], policy=policy(), pricing=FLAT)
  assert not r.complete
  assert set(codes(r)) == {'unbooked'} and len(codes(r)) == len(legs)
  assert r.positions == ()


def test_a_fill_with_another_convention_is_unbooked():
  """A position opened on a notional basis cannot be continued by a `pnl` fill."""
  # policy 05 rules 8.2 and 8.3
  events = [
    collateral(),
    event('open', 2, size('1'), notional('-60000')),
    event('odd', 3, size('1', price='61000', settles_in='USDC')),
  ]
  r = run(events, policy=policy(), pricing=FLAT)
  assert codes(r) == ['unbooked'] and not r.complete
  assert [p.size for p in r.positions] == [D('1')]


def test_validate_fill_fields():
  """`price` and `settles_in` go together, on position legs only; perp legs are never fees."""
  # policy 05 rule 8
  bad = [
    event('a', 1, Leg('BTC-PERP', D('1'), 'P', 'position', price=D('1'))),
    event('b', 1, Leg('USDC', D('1'), 'P', 'trade', price=D('1'), settles_in='USDC')),
    event(
      'c', 1, Leg('BTC-PERP', D('1'), 'P', 'position', price=D('0'), settles_in='USDC')
    ),
    event('d', 1, Leg('USDC', D('-1'), 'P', 'notional', fee=True)),
  ]
  assert [x.message for x in validate(bad)] == [
    'leg 0: price and settles_in go together, on position legs only',
    'leg 0: price and settles_in go together, on position legs only',
    'leg 0: price must be positive',
    'leg 0: a notional leg is never a fee',
  ]


def test_balances_keep_the_notional_basis():
  """`balances` sums every leg, so the perp cash balance stays the unit's notional basis."""
  # policy 05 rule 8.5; policy 03 rule 46.1
  events = [collateral(), event('open', 2, size('1'), notional('-60000'))]
  r = run(events, policy=policy(), pricing=FLAT)
  usdc = next(b for b in r.balances if b.asset == 'USDC')
  assert usdc.quantity == D('-50000')
  assert sum(l.quantity for l in r.lots) == D('10000')
