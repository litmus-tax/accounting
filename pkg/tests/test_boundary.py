"""Unlinked transfers across the books' boundary: classified, unclassified and at carried cost."""

from decimal import Decimal as D
import pytest
from litmus.accounting import run, validate, FixedPricing
from litmus.accounting.model import Leg, Result
from tests.conftest import leg, event, policy, t

PRICES = FixedPricing({('ETH', 'EUR'): '3500'})


def codes(result: Result) -> list[str]:
  """Exception codes of a result."""
  return [x.code for x in result.exceptions]


def transfer(qty: str, comp: str = 'A', **fields: object) -> Leg:
  """An unlinked ETH transfer leg with boundary fields."""
  from dataclasses import replace

  return replace(leg('ETH', qty, comp, 'transfer'), **fields)  # type: ignore[arg-type]


def test_functional_currency_transfers_need_no_classification():
  """Money in or out in the reporting currency carries no lots and is never `unmatched_transfer`."""
  # policy 05 rule 13.2
  r = run(
    [
      event('in', 1, leg('EUR', '1000', tag='transfer')),
      event('out', 2, leg('EUR', '-100', tag='transfer')),
    ],
    policy=policy(),
    pricing=PRICES,
  )
  assert codes(r) == [] and r.complete
  assert r.lots == ()


def test_an_unclassified_transfer_is_booked_at_market_and_reported():
  """The default: acquisition at market value plus `unmatched_transfer`, an open issue."""
  # policy 05 rule 13.4
  r = run([event('in', 1, transfer('1'))], policy=policy(), pricing=PRICES)
  assert codes(r) == ['unmatched_transfer'] and r.complete
  assert [(l.quantity, l.cost) for l in r.lots] == [(D('1'), D('3500'))]


@pytest.mark.parametrize(('qty', 'cost'), [('1', '3500'), ('-1', '3500')])
def test_not_retained_is_at_market_and_not_reported(qty: str, cost: str):
  """Classified as a payment, sale, purchase or gift: a disposal or acquisition at market value."""
  # policy 05 rule 13.3.2
  events = (
    [event('buy', 1, leg('ETH', '1'), leg('EUR', '-3000'))]
    if qty.startswith('-')
    else []
  )
  events.append(event('x', 2, transfer(qty, basis='market')))
  r = run(events, policy=policy(), pricing=PRICES)
  assert codes(r) == []
  if qty.startswith('-'):
    ((row),) = r.realized
    assert (row.proceeds, row.cost, row.pnl) == (D('3500'), D('3000'), D('500'))
  else:
    assert [l.cost for l in r.lots] == [D(cost)]


def test_ownership_retained_in_opens_a_lot_at_the_given_cost_and_time():
  """The correction's acquisition price and time become the lot's cost and acquisition date; no income."""
  # policy 05 rule 13.3.1
  r = run(
    [event('in', 5, transfer('2', basis='carried', cost=D('4000'), acquired=t(1)))],
    policy=policy(),
    pricing=PRICES,
  )
  assert codes(r) == [] and r.flows == () and r.realized == ()
  (lot,) = r.lots
  assert (lot.quantity, lot.cost, lot.acquired) == (D('2'), D('4000'), t(1))
  external = [x for x in r.journal if x.account == 'external']
  assert [(x.credit) for x in external] == [D('4000')]


def test_ownership_retained_out_leaves_at_cost_without_pnl():
  """Lots leave at cost to the external account; nothing is realized."""
  # policy 05 rule 13.3.1
  events = [
    event('buy', 1, leg('ETH', '2'), leg('EUR', '-6000')),
    event('out', 2, transfer('-1.5', basis='carried')),
  ]
  r = run(events, policy=policy(minor_unit=2), pricing=PRICES)
  assert codes(r) == [] and r.realized == () and r.flows == ()
  assert [(l.quantity, l.cost) for l in r.lots] == [(D('0.5'), D('1500.00'))]
  out = [(x.account, x.debit, x.credit) for x in r.journal if x.event == 'out']
  assert out == [('holding', D('0'), D('4500.00')), ('external', D('4500.00'), D('0'))]


def test_carrying_out_more_than_is_held_is_unbooked():
  """Only what is held can leave at cost; the rest is unbooked and the result incomplete."""
  # policy 05 rule 13.3.1
  events = [
    event('buy', 1, leg('ETH', '1'), leg('EUR', '-3000')),
    event('out', 2, transfer('-2', basis='carried')),
  ]
  r = run(events, policy=policy(), pricing=PRICES)
  assert codes(r) == ['unbooked'] and not r.complete
  assert r.lots == ()


def test_zero_cost_unsolicited_receipt_realizes_everything_on_disposal():
  """An airdrop at zero cost books no income; its sale realizes the full proceeds."""
  # policy 05 rule 11.2.3 (decision 8)
  events = [
    event('airdrop', 1, transfer('1', basis='carried', cost=D('0'))),
    event('sell', 2, leg('ETH', '-1'), leg('EUR', '3500')),
  ]
  r = run(events, policy=policy(), pricing=PRICES)
  assert codes(r) == [] and r.flows == ()
  ((row),) = r.realized
  assert (row.proceeds, row.cost, row.pnl) == (D('3500'), D('0'), D('3500'))


def test_carried_transfers_keep_the_cost_identity():
  """Contributions follow the carried cost, so assets at cost still equal net contributions, with no price."""
  # policy 05 rules 13.3.1 and 39.5.1
  r = run(
    [event('in', 1, transfer('1', basis='carried', cost=D('1000')))],
    policy=policy(),
    pricing=FixedPricing({}),
  )
  assert 'cost_identity' not in codes(r) and r.complete
  assert [(row.kind, row.asset, row.quantity, row.cost) for row in r.open_rows] == [
    ('holding', 'ETH', D('1'), D('1000'))
  ]
  assert r.prices == ()


@pytest.mark.parametrize(
  ('bad', 'message'),
  [
    (
      transfer('1', tag='trade', basis='market'),
      'leg 0: only transfer legs have a basis',
    ),
    (
      transfer('1', basis='carried'),
      'leg 0: an inbound carried transfer needs its cost',
    ),
    (
      transfer('-1', basis='carried', cost=D('1')),
      'leg 0: cost and acquired go on inbound carried transfers only',
    ),
    (
      transfer('1', cost=D('1')),
      'leg 0: cost and acquired go on inbound carried transfers only',
    ),
    (transfer('1', basis='carried', cost=D('-1')), 'leg 0: cost must not be negative'),
    (
      transfer('1', basis='carried', cost=D('1'), acquired=t(1).replace(tzinfo=None)),
      'leg 0: naive acquisition time',
    ),
  ],
)
def test_validate_boundary_fields(bad: Leg, message: str):
  """`basis`, `cost` and `acquired` are checked without prices."""
  # policy 05 rules 11 and 13.3
  assert [x.message for x in validate([event('x', 1, bad)])] == [message]


def test_a_linked_transfer_ignores_its_basis():
  """A link wins: lots move at carried basis whatever the legs' basis says."""
  # policy 05 rule 13.1
  from tests.conftest import link

  events = [
    event('buy', 1, leg('ETH', '1'), leg('EUR', '-3000')),
    event('out', 2, transfer('-1', basis='market')),
    event('in', 2, transfer('1', 'B', basis='market'), hour=13),
  ]
  r = run(
    events,
    links=[link('out', 'in')],
    policy=policy('fifo', 'compartment'),
    pricing=PRICES,
  )
  assert codes(r) == [] and r.realized == ()
  assert [(l.compartment, l.cost) for l in r.lots] == [('B', D('3000'))]
