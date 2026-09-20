"""Round-2 policy additions: fee treatment, position assets, rounding, strict mode, cost methods."""

from decimal import Decimal as D
import pytest
from litmus.accounting import run, FixedPricing, PriceGap
from litmus.accounting.model import Result
from tests.conftest import leg, event, link, policy, t


def codes(result: Result) -> list[str]:
  """Exception codes of a result."""
  return [x.code for x in result.exceptions]


def three_lots():
  """Three BTC lots at 10000, 30000, 20000 EUR, then one sold for 25000."""
  return [
    event('b1', 1, leg('BTC', '1'), leg('EUR', '-10000')),
    event('b2', 2, leg('BTC', '1'), leg('EUR', '-30000')),
    event('b3', 3, leg('BTC', '1'), leg('EUR', '-20000')),
    event('sell', 4, leg('BTC', '-1'), leg('EUR', '25000')),
  ]


@pytest.mark.parametrize(
  'method,cost,left',
  [
    ('fifo', '10000', ['b2', 'b3']),
    ('lifo', '20000', ['b1', 'b2']),
    ('hifo', '30000', ['b1', 'b3']),
    ('average', '20000', ['b1']),
  ],
)
def test_cost_methods_pick_different_lots(method, cost, left, scope):
  """FIFO oldest, LIFO newest, HIFO highest unit cost, average the pool."""
  r = run(three_lots(), policy=policy(method, scope), pricing=FixedPricing({}))
  (sale,) = r.realized
  assert (sale.cost, sale.proceeds, sale.pnl) == (
    D(cost),
    D('25000'),
    D('25000') - D(cost),
  )
  assert [l.event for l in r.lots] == left
  assert sum(l.cost for l in r.lots) == D('60000') - D(cost)


def test_hifo_ties_go_oldest_first():
  """Equal unit costs fall back to FIFO order."""
  events = [
    event('b1', 1, leg('BTC', '1'), leg('EUR', '-10000')),
    event('b2', 2, leg('BTC', '1'), leg('EUR', '-10000')),
    event('sell', 3, leg('BTC', '-1'), leg('EUR', '25000')),
  ]
  r = run(events, policy=policy('hifo'), pricing=FixedPricing({}))
  assert r.realized[0].lots == ('lot-1',)


def test_hifo_short_cover_takes_highest_sale_price():
  """For a short, HIFO covers the lot with the largest absolute unit basis first."""
  events = [
    event('s1', 1, leg('BTC-PERP', '-1'), leg('USDC', '100')),
    event('s2', 2, leg('BTC-PERP', '-1'), leg('USDC', '120')),
    event('cover', 3, leg('BTC-PERP', '1'), leg('USDC', '-110')),
  ]
  r = run(events, policy=policy('hifo', fc='USDC'), pricing=FixedPricing({}))
  (c,) = r.realized
  assert (c.cost, c.proceeds, c.pnl, c.lots) == (
    D('-120'),
    D('-110'),
    D('10'),
    ('lot-2',),
  )


def test_fee_treatment_expense_is_the_default(method, scope):
  """Under `expense` a fee is a flow and never touches basis or proceeds."""
  events = [
    event(
      'buy',
      1,
      leg('BTC', '1'),
      leg('EUR', '-10000'),
      leg('EUR', '-10', tag='expense', fee=True),
    ),
    event(
      'sell',
      2,
      leg('BTC', '-1'),
      leg('EUR', '30000'),
      leg('EUR', '-30', tag='expense', fee=True),
    ),
  ]
  r = run(events, policy=policy(method, scope), pricing=FixedPricing({}))
  assert [(f.value, f.fee) for f in r.flows] == [(D('10'), True), (D('30'), True)]
  (sale,) = r.realized
  assert (sale.cost, sale.proceeds, sale.fees, sale.pnl) == (
    D('10000'),
    D('30000'),
    D('0'),
    D('20000'),
  )


def test_fee_treatment_capitalize(method, scope):
  """Under `capitalize` an acquisition fee joins the basis and a disposal fee reduces proceeds; no flow is emitted."""
  events = [
    event(
      'buy',
      1,
      leg('BTC', '1'),
      leg('EUR', '-10000'),
      leg('EUR', '-10', tag='expense', fee=True),
    ),
    event(
      'sell',
      2,
      leg('BTC', '-1'),
      leg('EUR', '30000'),
      leg('EUR', '-30', tag='expense', fee=True),
    ),
  ]
  r = run(
    events,
    policy=policy(method, scope, fee_treatment='capitalize'),
    pricing=FixedPricing({}),
  )
  assert r.flows == ()
  (sale,) = r.realized
  assert (sale.cost, sale.proceeds, sale.fees, sale.pnl) == (
    D('10010'),
    D('29970'),
    D('30'),
    D('19960'),
  )
  assert codes(r) == []


def test_capitalize_fee_in_another_asset_still_leaves_its_lots(method, scope):
  """A perp fee in USDC is capitalized into the position and the USDC still leaves the cash lots."""
  pricing = FixedPricing({('USDC', 'EUR'): '0.9'})
  events = [
    event('dep', 1, leg('USDC', '100000', tag='transfer')),
    event(
      'open',
      2,
      leg('BTC-PERP', '1'),
      leg('USDC', '-50000'),
      leg('USDC', '-10', fee=True),
    ),
  ]
  r = run(
    events, policy=policy(method, scope, fee_treatment='capitalize'), pricing=pricing
  )
  perp = next(l for l in r.lots if l.asset == 'BTC-PERP')
  usdc = sum(l.quantity for l in r.lots if l.asset == 'USDC')
  assert (perp.cost, usdc) == (D('45009'), D('49990'))
  assert r.flows == ()
  assert [x.asset for x in r.realized] == ['USDC', 'USDC']


def test_capitalize_only_applies_to_trades(method, scope):
  """Fees on transfers and flows stay expenses under `capitalize`."""
  events = [
    event('buy', 1, leg('BTC', '1'), leg('EUR', '-10000')),
    event('out', 2, leg('BTC', '-0.5', tag='transfer'), leg('BTC', '-0.001', fee=True)),
    event('in', 2, leg('BTC', '0.5', 'B', tag='transfer'), hour=13),
  ]
  r = run(
    events,
    links=[link('out', 'in')],
    policy=policy(method, scope, fee_treatment='capitalize'),
    pricing=FixedPricing({('BTC', 'EUR'): '20000'}),
  )
  assert [(f.value, f.fee, f.asset) for f in r.flows] == [(D('20'), True, 'BTC')]


def test_capitalize_price_gap_unbooks_the_whole_trade():
  """A fee that cannot be valued leaves the trade and the fee unbooked."""
  events = [
    event('buy', 1, leg('BTC', '1'), leg('EUR', '-10000'), leg('DOGE', '-5', fee=True))
  ]
  r = run(events, policy=policy(fee_treatment='capitalize'), pricing=FixedPricing({}))
  assert codes(r) == ['price_gap', 'unbooked', 'unbooked', 'unbooked']
  assert r.lots == () and not r.complete


def test_negative_position_exception(method, scope):
  """Selling more than held opens a short lot and reports it unless the asset is a position asset."""
  events = [event('sell', 1, leg('BTC', '-1'), leg('EUR', '30000'))]
  r = run(events, policy=policy(method, scope), pricing=FixedPricing({}))
  assert codes(r) == ['negative_position']
  assert r.exceptions[0].detail == {
    'asset': 'BTC',
    'compartment': 'A',
    'position': '-1',
  }
  (lot,) = r.lots
  assert (lot.quantity, lot.cost) == (D('-1'), D('-30000'))
  assert r.complete
  r2 = run(
    events,
    policy=policy(method, scope, position_assets=('BTC',)),
    pricing=FixedPricing({}),
  )
  assert codes(r2) == []


def test_negative_position_is_per_lot_key():
  """Under per-compartment scope a short in one compartment is not offset by a long in another."""
  events = [
    event('buy', 1, leg('BTC', '1', 'A'), leg('EUR', '-10000', 'A')),
    event('sell', 2, leg('BTC', '-1', 'B'), leg('EUR', '30000', 'B')),
  ]
  assert (
    codes(run(events, policy=policy(scope='global'), pricing=FixedPricing({}))) == []
  )
  assert codes(
    run(events, policy=policy(scope='compartment'), pricing=FixedPricing({}))
  ) == ['negative_position']


def test_minor_unit_rounds_money_not_quantities(method, scope):
  """Money fields are rounded at output; quantities and prices are not; `pnl == proceeds - cost` holds."""
  pricing = FixedPricing({('BTC', 'EUR'): '30000.123456'})
  events = [
    event('buy', 1, leg('BTC', '3'), leg('EUR', '-10000')),
    event('sell', 2, leg('BTC', '-1'), leg('EUR', '30000')),
    event('fee', 3, leg('BTC', '-0.000001', tag='expense', fee=True)),
  ]
  r = run(events, policy=policy(method, scope, minor_unit=2), pricing=pricing)
  sale = next(x for x in r.realized if x.event == 'sell')
  assert (sale.cost, sale.proceeds, sale.pnl) == (
    D('3333.33'),
    D('30000.00'),
    D('26666.67'),
  )
  assert sale.pnl == sale.proceeds - sale.cost
  (lot,) = r.lots
  assert lot.quantity == D('1.999999')
  assert lot.cost == D('6666.66')
  assert r.flows[0].value == D('0.03')
  assert r.prices[0].price == D('30000.123456')
  raw = run(events, policy=policy(method, scope), pricing=pricing)
  assert raw.lots[0].cost != D('6666.66')


def test_strict_raises_on_first_gap(method, scope):
  """`strict=True` raises `PriceGap` instead of reporting it."""
  events = [event('y', 1, leg('DOGE', '100', tag='income'))]
  with pytest.raises(PriceGap) as e:
    run(events, policy=policy(method, scope), pricing=FixedPricing({}), strict=True)
  assert (e.value.asset, e.value.quote) == ('DOGE', 'EUR')


def test_pnl_settled_venue_is_cash_flows_only(method, scope):
  """A PnL-settled venue reports realized PnL as income or expense in cash; the position is never a lot."""
  pricing = FixedPricing({('USDT', 'EUR'): '0.9'})
  events = [
    event('dep', 1, leg('USDT', '1000', 'cex:futures', tag='transfer')),
    event(
      'pnl1', 2, leg('USDT', '50', 'cex:futures', tag='income', label='realized_pnl')
    ),
    event(
      'pnl2', 3, leg('USDT', '-20', 'cex:futures', tag='expense', label='realized_pnl')
    ),
    event('fee', 3, leg('USDT', '-1', 'cex:futures', tag='expense', fee=True), hour=13),
  ]
  r = run(events, policy=policy(method, scope), pricing=pricing)
  assert codes(r) == ['unmatched_transfer']
  assert [(f.kind, f.value, f.label) for f in r.flows] == [
    ('income', D('45'), 'realized_pnl'),
    ('expense', D('18'), 'realized_pnl'),
    ('expense', D('0.9'), None),
  ]
  assert {l.asset for l in r.lots} == {'USDT'}
  assert sum(l.quantity for l in r.lots) == D('1029')


def test_average_acquired_is_the_pool_opening():
  """Under `average` a lot's `acquired` and `event` are the pool's opening; a pool emptied and refilled starts over."""
  events = [
    event('b1', 1, leg('BTC', '1'), leg('EUR', '-10000')),
    event('b2', 2, leg('BTC', '1'), leg('EUR', '-30000')),
    event('sell', 3, leg('BTC', '-2'), leg('EUR', '50000')),
    event('b3', 4, leg('BTC', '1'), leg('EUR', '-20000')),
    event('b4', 5, leg('BTC', '1'), leg('EUR', '-40000')),
  ]
  r = run(events[:2], policy=policy('average'), pricing=FixedPricing({}))
  (pool,) = r.lots
  assert (pool.acquired, pool.event, pool.cost) == (t(1), 'b1', D('40000'))
  r = run(events, policy=policy('average'), pricing=FixedPricing({}))
  (pool,) = r.lots
  assert (pool.acquired, pool.event, pool.cost) == (t(4), 'b3', D('60000'))
