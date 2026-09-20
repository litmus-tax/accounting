"""Linked pairs are booked when the earlier of the two events is processed."""

from decimal import Decimal as D
from litmus.accounting import run, FixedPricing
from litmus.accounting.model import Result
from tests.conftest import leg, event, link, policy, t


def codes(result: Result) -> list[str]:
  """Exception codes of a result."""
  return [x.code for x in result.exceptions]


def skewed():
  """The destination's clock is ahead: `in` at day 3 12:00, `out` at day 3 13:00; a sale in B between them."""
  return [
    event('buy', 1, leg('BTC', '1'), leg('EUR', '-10000')),
    event('out', 3, leg('BTC', '-1', tag='transfer'), hour=13),
    event('in', 3, leg('BTC', '1', 'B', tag='transfer'), hour=12),
    event(
      'sell',
      3,
      leg('BTC', '-1', 'B'),
      leg('EUR', '30000', 'B'),
      hour=12,
    ),
  ]


def test_link_books_at_the_earlier_event(method):
  """The move happens at the destination's time when that is earlier, so the sale in B finds its lot."""
  r = run(
    skewed(),
    links=[link('out', 'in')],
    policy=policy(method, 'compartment'),
    pricing=FixedPricing({}),
  )
  assert codes(r) == []
  (mv,) = r.moves
  assert (mv.time, mv.quantity, mv.cost) == (t(3, 12), D('1'), D('10000'))
  (sale,) = r.realized
  assert (sale.compartment, sale.cost, sale.pnl) == ('B', D('10000'), D('20000'))
  assert r.lots == ()


def test_link_books_at_the_source_when_it_is_earlier(method):
  """The usual case is unchanged: source first, move at the source's time."""
  events = [
    event('buy', 1, leg('BTC', '1'), leg('EUR', '-10000')),
    event('out', 2, leg('BTC', '-1', tag='transfer')),
    event('in', 3, leg('BTC', '1', 'B', tag='transfer')),
  ]
  r = run(
    events,
    links=[link('out', 'in')],
    policy=policy(method, 'compartment'),
    pricing=FixedPricing({}),
  )
  (mv,) = r.moves
  assert mv.time == t(2)


def test_same_time_pair_books_in_input_order():
  """With equal timestamps the pair is booked when the first of the two in input order is processed."""
  events = [
    event('buy', 1, leg('BTC', '1'), leg('EUR', '-10000')),
    event('in', 2, leg('BTC', '1', 'B', tag='transfer')),
    event('out', 2, leg('BTC', '-1', tag='transfer')),
  ]
  r = run(
    events,
    links=[link('out', 'in')],
    policy=policy('fifo', 'compartment'),
    pricing=FixedPricing({}),
  )
  assert codes(r) == []
  (mv,) = r.moves
  assert mv.time == t(2)
  assert [(l.compartment, l.quantity) for l in r.lots] == [('B', D('1'))]


def test_global_scope_link_is_still_booked_once():
  """Under global lots nothing moves, but the pair is consumed once and neither side is external."""
  r = run(
    skewed()[:3],
    links=[link('out', 'in')],
    policy=policy('fifo', 'global'),
    pricing=FixedPricing({}),
  )
  assert codes(r) == [] and r.moves == ()
