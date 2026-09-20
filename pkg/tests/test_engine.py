"""Engine behaviour on synthetic ledgers under every cost method and lot scope (round-1 coverage, extended)."""

from decimal import Decimal as D
from litmus.accounting import run, FixedPricing
from litmus.accounting.model import PriceRecord, Balance, Result
from litmus.accounting.pricing import effective_time
from tests.conftest import leg, event, link, policy, t


def codes(result: Result) -> list[str]:
  """Exception codes of a result."""
  return [x.code for x in result.exceptions]


def pnl(result: Result, asset: str) -> D:
  """Total realized PnL on one asset."""
  return sum((r.pnl for r in result.realized if r.asset == asset), D(0))


def test_perp_notional_exchange(method, scope):
  """A fill moves cash by -size*price-fee; the position is an asset with basis; PnL is an output."""
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
    event(
      'close',
      3,
      leg('BTC-PERP', '-1'),
      leg('USDC', '55000'),
      leg('USDC', '-11', fee=True),
    ),
  ]
  r = run(events, policy=policy(method, scope), pricing=pricing)
  assert pnl(r, 'BTC-PERP') == D('4500')
  assert pnl(r, 'USDC') == 0
  assert sum(f.value for f in r.flows if f.fee) == D('18.9')
  assert codes(r) == ['unmatched_transfer']
  assert {l.asset for l in r.lots} == {'USDC'}
  assert sum(l.quantity for l in r.lots) == D('104979')
  assert r.complete


def test_short_perp_partial_cover(method, scope):
  """Short lots carry negative quantity and basis; a cover realizes against them."""
  events = [
    event('short', 1, leg('BTC-PERP', '-2'), leg('USDC', '200')),
    event('cover', 2, leg('BTC-PERP', '1'), leg('USDC', '-90')),
  ]
  r = run(events, policy=policy(method, scope, fc='USDC'), pricing=FixedPricing({}))
  assert pnl(r, 'BTC-PERP') == D('10')
  (lot,) = r.lots
  assert (lot.quantity, lot.cost) == (D('-1'), D('-100'))
  assert r.prices == ()
  assert codes(r) == []


def test_funding_and_fees_are_flows(method, scope):
  """Funding is income or expense by sign; crypto income is valued in the functional currency and opens a lot."""
  pricing = FixedPricing({('USDC', 'EUR'): '0.9', ('ETH', 'EUR'): '2000'})
  events = [
    event('f1', 1, leg('USDC', '5', tag='income', label='funding')),
    event('f2', 2, leg('USDC', '-3', tag='expense', label='funding')),
    event('y', 3, leg('ETH', '0.01', tag='income', label='staking')),
  ]
  r = run(events, policy=policy(method, scope), pricing=pricing)
  assert [(f.kind, f.value, f.label) for f in r.flows] == [
    ('income', D('4.5'), 'funding'),
    ('expense', D('2.7'), 'funding'),
    ('income', D('20'), 'staking'),
  ]
  eth = next(l for l in r.lots if l.asset == 'ETH')
  assert (eth.quantity, eth.cost) == (D('0.01'), D('20'))
  assert pnl(r, 'USDC') == 0


def btc_ledger():
  """Two BTC lots in A, a linked transfer of 1.5 BTC to B with a BTC fee, a sale in B."""
  events = [
    event('b1', 1, leg('BTC', '1'), leg('EUR', '-10000')),
    event('b2', 2, leg('BTC', '1'), leg('EUR', '-20000')),
    event('out', 3, leg('BTC', '-1.5', tag='transfer'), leg('BTC', '-0.001', fee=True)),
    event('in', 3, leg('BTC', '1.5', 'B', tag='transfer'), hour=13),
    event('sell', 4, leg('BTC', '-1', 'B'), leg('EUR', '30000', 'B')),
  ]
  return events, [link('out', 'in')], FixedPricing({('BTC', 'EUR'): '30000'})


def test_linked_transfer_carries_basis(method, scope):
  """Basis carries across a link; the fee is an expense and a disposal at market."""
  events, links, pricing = btc_ledger()
  r = run(events, links=links, policy=policy(method, scope), pricing=pricing)
  assert codes(r) == []
  assert [(f.value, f.asset) for f in r.flows if f.fee] == [(D('30'), 'BTC')]
  sale = next(x for x in r.realized if x.event == 'sell')
  fee_pnl = next(x for x in r.realized if x.event == 'out')
  expected = {
    ('fifo', 'global'): (D('19990'), D('20')),
    ('lifo', 'global'): (D('10010'), D('10')),
    ('hifo', 'global'): (D('10010'), D('10')),
    ('average', 'global'): (D('15000'), D('15')),
    ('fifo', 'compartment'): (D('20000'), D('10')),
    ('lifo', 'compartment'): (D('10000'), D('20')),
    ('hifo', 'compartment'): (D('10000'), D('20')),
    ('average', 'compartment'): (D('15000'), D('15')),
  }
  assert (sale.pnl, fee_pnl.pnl) == expected[(method, scope)]
  assert sum(l.quantity for l in r.lots) == D('0.999')
  if scope == 'global':
    assert r.moves == ()
    assert all(l.compartment is None for l in r.lots)
  else:
    (mv,) = r.moves
    assert (mv.asset, mv.quantity) == ('BTC', D('1.5'))
    assert (
      mv.cost
      == {
        'fifo': D('20000'),
        'lifo': D('25000'),
        'hifo': D('25000'),
        'average': D('22500'),
      }[method]
    )
    by_comp: dict[str | None, D] = {}
    for l in r.lots:
      by_comp[l.compartment] = by_comp.get(l.compartment, D(0)) + l.quantity
    assert by_comp == {'A': D('0.499'), 'B': D('0.5')}


def test_fifo_move_keeps_acquisition_time():
  """Per-compartment FIFO recreates lots in the target with the original acquisition time."""
  events, links, pricing = btc_ledger()
  r = run(
    events[:4], links=links, policy=policy('fifo', 'compartment'), pricing=pricing
  )
  b = [l for l in r.lots if l.compartment == 'B']
  assert [(l.quantity, l.cost, l.acquired, l.event) for l in b] == [
    (D('1'), D('10000'), t(1), 'b1'),
    (D('0.5'), D('10000'), t(2), 'b2'),
  ]


def test_lifo_after_move_consumes_newest():
  """After a move the destination's lots are still consumed by acquisition time, not by move order."""
  events, links, pricing = btc_ledger()
  r = run(events, links=links, policy=policy('lifo', 'compartment'), pricing=pricing)
  sale = next(x for x in r.realized if x.event == 'sell')
  assert sale.cost == D('20000')
  b = [l for l in r.lots if l.compartment == 'B']
  assert [(l.quantity, l.acquired) for l in b] == [(D('0.5'), t(1))]


def test_unmatched_transfer_is_external(method, scope):
  """An unlinked outflow is a disposal at market value and an exception."""
  events = [
    event('buy', 1, leg('BTC', '1'), leg('EUR', '-10000')),
    event('wd', 2, leg('BTC', '-1', tag='transfer', label='withdrawal')),
  ]
  r = run(
    events,
    policy=policy(method, scope),
    pricing=FixedPricing({('BTC', 'EUR'): '30000'}),
  )
  assert codes(r) == ['unmatched_transfer']
  assert pnl(r, 'BTC') == D('20000')
  assert r.lots == ()
  assert r.complete


def test_price_gap_is_an_exception_not_zero(method, scope):
  """A missing price leaves the leg unbooked, reports both the gap and the unbooked leg, and records the gap."""
  events = [event('y', 1, leg('DOGE', '100', tag='income'))]
  r = run(events, policy=policy(method, scope), pricing=FixedPricing({}))
  assert codes(r) == ['price_gap', 'unbooked']
  assert not r.complete
  assert r.lots == () and r.flows == ()
  assert r.prices == (
    PriceRecord(asset='DOGE', quote='EUR', time=t(1), source='market', price=None),
  )
  assert r.balances == (Balance(compartment='A', asset='DOGE', quantity=D('100')),)


def test_quote_path_and_source_class():
  """BTC -> USD -> EUR asks two prices; the fiat leg is `official`."""
  pricing = FixedPricing({('BTC', 'USD'): '50000', ('USD', 'EUR'): '0.9'})
  events = [event('y', 1, leg('BTC', '1', tag='income'))]
  r = run(events, policy=policy(quote_path=('USD',), fiat=('USD',)), pricing=pricing)
  assert r.flows[0].value == D('45000')
  assert [(p.asset, p.quote, p.source, p.price) for p in r.prices] == [
    ('BTC', 'USD', 'market', D('50000')),
    ('USD', 'EUR', 'official', D('0.9')),
  ]


def test_daily_close_timestamp():
  """`daily_close` asks for the end of the event's UTC day."""
  p = policy(price_time='daily_close')
  assert effective_time(t(5, 3), p) == t(5).replace(
    hour=23, minute=59, second=59, microsecond=999999
  )
  r = run(
    [event('y', 5, leg('BTC', '1', tag='income'), hour=3)],
    policy=p,
    pricing=FixedPricing({('BTC', 'EUR'): '1'}),
  )
  assert r.prices[0].time.hour == 23


def test_link_mismatch(method, scope):
  """A link whose quantities do not conserve is reported and not moved."""
  events = [
    event('buy', 1, leg('BTC', '2'), leg('EUR', '-20000')),
    event('out', 2, leg('BTC', '-1', tag='transfer')),
    event('in', 2, leg('BTC', '0.9', 'B', tag='transfer'), hour=13),
  ]
  r = run(
    events,
    links=[link('out', 'in')],
    policy=policy(method, scope),
    pricing=FixedPricing({('BTC', 'EUR'): '1'}),
  )
  assert codes(r) == ['link_mismatch']
  assert r.moves == ()


def test_crypto_to_crypto_trade_valued_by_received_side(method, scope):
  """Without cash on either side the received side fixes the value by default."""
  pricing = FixedPricing({('ETH', 'EUR'): '2000', ('BTC', 'EUR'): '30000'})
  events = [
    event('buy', 1, leg('ETH', '10'), leg('EUR', '-10000')),
    event('swap', 2, leg('ETH', '-10'), leg('BTC', '1')),
  ]
  r = run(events, policy=policy(method, scope), pricing=pricing)
  assert pnl(r, 'ETH') == D('20000')
  btc = next(l for l in r.lots if l.asset == 'BTC')
  assert btc.cost == D('30000')
  r2 = run(
    events, policy=policy(method, scope, trade_valuation='given'), pricing=pricing
  )
  assert pnl(r2, 'ETH') == D('10000')


def test_cross_compartment_bridge_is_one_trade(method, scope):
  """An asset-changing bridge is a two-leg trade whose legs sit in different compartments."""
  pricing = FixedPricing({('ETH', 'EUR'): '2000', ('HYPE', 'EUR'): '20'})
  events = [
    event('buy', 1, leg('ETH', '1', 'evm:0xabc'), leg('EUR', '-1500', 'evm:0xabc')),
    event('bridge', 2, leg('ETH', '-1', 'evm:0xabc'), leg('HYPE', '99', 'hl:0xabc')),
  ]
  r = run(events, policy=policy(method, scope), pricing=pricing)
  assert codes(r) == []
  assert pnl(r, 'ETH') == D('480')
  (hype,) = r.lots
  assert (hype.asset, hype.quantity, hype.cost) == ('HYPE', D('99'), D('1980'))
  assert hype.compartment == ('hl:0xabc' if scope == 'compartment' else None)


def test_unknown_link_and_invalid_event():
  """Structural problems are exceptions, not crashes."""
  events = [event('x', 1, leg('BTC', '1', tag='expense'))]
  r = run(events, links=[link('x', 'nope')], policy=policy(), pricing=FixedPricing({}))
  assert codes(r) == ['invalid_event', 'unknown_event']
  assert not r.complete


def test_duplicate_ids_skip_the_later_event():
  """Duplicate ids are reported and the later event is skipped."""
  events = [
    event('a', 1, leg('BTC', '1'), leg('EUR', '-10')),
    event('a', 2, leg('BTC', '1'), leg('EUR', '-10')),
  ]
  r = run(events, policy=policy(), pricing=FixedPricing({}))
  assert codes(r) == ['duplicate_id']
  assert sum(l.quantity for l in r.lots) == D('1')
  assert not r.complete
