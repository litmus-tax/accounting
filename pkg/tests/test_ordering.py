"""The ordering rules of the model contract: within an event and across events with the same timestamp."""

from decimal import Decimal as D
from litmus.accounting import run, FixedPricing
from litmus.accounting.model import Result, Leg
from tests.conftest import leg, event, link, policy


def codes(result: Result) -> list[str]:
  """Exception codes of a result."""
  return [x.code for x in result.exceptions]


def test_same_timestamp_keeps_input_order():
  """Two events at the same time are processed in input order, so swapping them changes the books."""
  buy = event('buy', 1, leg('BTC', '1'), leg('EUR', '-10000'))
  sell = event('sell', 1, leg('BTC', '-1'), leg('EUR', '30000'))
  ok = run([buy, sell], policy=policy(), pricing=FixedPricing({}))
  assert codes(ok) == [] and ok.realized[0].pnl == D('20000')
  swapped = run([sell, buy], policy=policy(), pricing=FixedPricing({}))
  assert codes(swapped) == ['negative_position']
  assert (swapped.realized[0].event, swapped.realized[0].pnl) == ('buy', D('20000'))


def test_events_are_sorted_by_time_stably():
  """Out-of-order input is sorted by time; ties keep input order."""
  events = [
    event('sell', 2, leg('BTC', '-1'), leg('EUR', '30000')),
    event('buy', 1, leg('BTC', '1'), leg('EUR', '-10000')),
  ]
  r = run(events, policy=policy(), pricing=FixedPricing({}))
  assert codes(r) == [] and r.realized[0].event == 'sell'


def test_within_event_trades_before_transfers_before_flows_before_fees():
  """One event with every leg kind: the trade opens the lot the transfer moves, the flow and the fee then consume what is left."""
  pricing = FixedPricing({('BTC', 'EUR'): '10000'})
  legs: list[Leg] = [
    leg('BTC', '-0.001', 'A', fee=True),
    leg('BTC', '-0.1', 'A', tag='expense', label='burn'),
    leg('BTC', '-0.5', 'A', tag='transfer'),
    leg('BTC', '1', 'A'),
    leg('EUR', '-10000', 'A'),
  ]
  events = [
    event('all', 1, *legs),
    event('in', 1, leg('BTC', '0.5', 'B', tag='transfer'), hour=13),
  ]
  r = run(
    events,
    links=[link('all', 'in')],
    policy=policy('fifo', 'compartment'),
    pricing=pricing,
  )
  assert codes(r) == []
  (mv,) = r.moves
  assert mv.quantity == D('0.5')
  assert [(x.asset, x.quantity, x.event) for x in r.realized] == [
    ('BTC', D('-0.1'), 'all'),
    ('BTC', D('-0.001'), 'all'),
  ]
  assert [(f.label, f.fee) for f in r.flows] == [('burn', False), (None, True)]


def test_fee_in_the_transferred_asset_comes_after_the_move():
  """A fee paid in the same asset as a linked transfer is taken from what remains after the move (fees last)."""
  pricing = FixedPricing({('BTC', 'EUR'): '30000'})
  events = [
    event('b1', 1, leg('BTC', '1'), leg('EUR', '-10000')),
    event('out', 2, leg('BTC', '-0.001', fee=True), leg('BTC', '-1', tag='transfer')),
    event('in', 2, leg('BTC', '1', 'B', tag='transfer'), hour=13),
  ]
  r = run(
    events,
    links=[link('out', 'in')],
    policy=policy('fifo', 'compartment'),
    pricing=pricing,
  )
  assert codes(r) == ['negative_position']
  (mv,) = r.moves
  assert mv.quantity == D('1')
  assert [(l.compartment, l.quantity) for l in r.lots] == [
    ('A', D('-0.001')),
    ('B', D('1')),
  ]
