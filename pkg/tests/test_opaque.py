"""Opaque positions (policy 05 rule 14), beyond the worked examples: lot scopes and cost methods, basis carried in, a book in EUR, unlinked transfers, moves between opaque compartments, and what an opaque compartment does not take."""

from dataclasses import replace
from decimal import Decimal
from litmus.accounting import FixedPricing, run
from litmus.accounting.model import (
  CostMethod,
  Event,
  Link,
  LotScope,
  Policy,
  PriceRecord,
)
from litmus.accounting.pricing import TablePricing
from tests.conftest import event, leg, link, policy, t

BOT = 'bot'
POSITION = 'position:opaque:bot'


def opaque(**extra: object) -> Policy:
  """A EUR policy with `bot` opaque."""
  return replace(policy(**extra), opaque_compartments=(BOT,))  # type: ignore[arg-type]


def move(id: str, day: int, asset: str, qty: str, src: str, dst: str) -> list[Event]:
  """The two linked events of a move between compartments."""
  return [
    event(f'{id}:out', day, leg(asset, f'-{qty}', src, 'transfer')),
    event(f'{id}:in', day, leg(asset, qty, dst, 'transfer')),
  ]


def links(*ids: str) -> list[Link]:
  """The links of `move`s."""
  return [link(f'{id}:out', f'{id}:in') for id in ids]


def performance(result) -> list[tuple[str, str, Decimal]]:  # noqa: ANN001
  """The `performance` flows as (event, kind, value)."""
  return [(f.event, f.kind, f.value) for f in result.flows if f.label == 'performance']


def test_cost_recovery_across_methods_and_scopes(method: CostMethod, scope: LotScope):
  """In at cost, out at market: the excess over the cost is `performance`, whatever the cost method and lot scope (rules 14.2-14.4)."""
  events = [
    event('buy', 1, leg('USDC', '100'), leg('EUR', '-90')),
    *move('in', 2, 'USDC', '100', 'A', BOT),
    *move('out', 3, 'USDC', '60', BOT, 'A'),
    *move('out2', 4, 'USDC', '60', BOT, 'A'),
  ]
  result = run(
    events,
    links=links('in', 'out', 'out2'),
    policy=opaque(method=method, scope=scope),
    pricing=FixedPricing({('USDC', 'EUR'): '0.9'}),
  )
  assert result.complete, result.exceptions
  assert performance(result) == [('out2:out', 'income', Decimal('18.0'))]
  usdc = [lot for lot in result.lots if lot.asset == 'USDC']
  assert sum(lot.quantity for lot in usdc) == 120
  assert sum(lot.cost for lot in usdc) == Decimal('108.0')
  assert not [lot for lot in result.lots if lot.asset == POSITION]


def test_basis_carries_with_its_acquisition_dates():
  """The coins' lots leave and their basis enters the position with their acquisition dates and origins (rule 14.2); nothing is realized."""
  events = [
    event('buy-1', 1, leg('USDC', '100'), leg('EUR', '-90')),
    event('buy-2', 2, leg('USDC', '100'), leg('EUR', '-95')),
    *move('in', 3, 'USDC', '150', 'A', BOT),
  ]
  result = run(
    events,
    links=links('in'),
    policy=opaque(),
    pricing=FixedPricing({('USDC', 'EUR'): '1'}),
  )
  assert result.complete, result.exceptions
  assert not result.realized and not result.flows
  lots = [lot for lot in result.lots if lot.asset == POSITION]
  assert [(lot.acquired, lot.event, lot.cost) for lot in lots] == [
    (t(1), 'buy-1', Decimal('90')),
    (t(2), 'buy-2', Decimal('47.5')),
  ]
  assert all(lot.quantity == lot.cost for lot in lots)
  (moved,) = result.moves
  assert (moved.asset, moved.quantity, moved.cost) == ('USDC', 150, Decimal('137.5'))


def test_a_book_in_eur_holds_the_dollar_move():
  """Each coin carries its EUR basis in and is valued at that day's EUR price out, so the result holds the stablecoin's move against the euro (positions example 7.7); a converting bot's shortfall waits for the empty snapshot (rule 14.8)."""
  events = [
    event('buy', 1, leg('USDC', '1000'), leg('EUR', '-950')),
    *move('in', 2, 'USDC', '1000', 'A', BOT),
    *move('usdc', 3, 'USDC', '400', BOT, 'A'),
    *move('usdt', 3, 'USDT', '600', BOT, 'A'),
    event(
      'empty',
      5,
      leg('USDC', '-600', BOT, 'contents'),
      leg('USDT', '600', BOT, 'contents'),
    ),
  ]
  result = run(
    events,
    links=links('in', 'usdc', 'usdt'),
    policy=opaque(),
    pricing=FixedPricing({('USDC', 'EUR'): '0.9', ('USDT', 'EUR'): '0.9'}),
  )
  assert result.complete, result.exceptions
  assert performance(result) == [('empty', 'expense', Decimal('50.0'))]


def test_unlinked_transfers_cross_the_boundary_at_market():
  """An unlinked transfer into an opaque compartment enters at market value, one out of it is a redemption at market value; both are reported unclassified (rule 13.4)."""
  events = [
    event('deposit', 1, leg('USDC', '100', BOT, 'transfer')),
    event('withdraw', 2, leg('USDC', '-130', BOT, 'transfer')),
  ]
  result = run(events, policy=opaque(), pricing=FixedPricing({('USDC', 'EUR'): '1'}))
  assert performance(result) == [('withdraw', 'income', Decimal('30'))]
  assert [item.code for item in result.exceptions] == ['unmatched_transfer'] * 2
  assert not result.lots
  assert not [line for line in result.journal if line.account == 'rounding']


def test_a_move_between_opaque_compartments():
  """A move between two opaque compartments is a redemption from one and an entry into the other at market value (rule 14.13)."""
  events = [
    event('buy', 1, leg('USDC', '100'), leg('EUR', '-100')),
    *move('in', 2, 'USDC', '100', 'A', BOT),
    *move('across', 3, 'USDC', '110', BOT, 'vault'),
  ]
  result = run(
    events,
    links=links('in', 'across'),
    policy=replace(opaque(), opaque_compartments=(BOT, 'vault')),
    pricing=FixedPricing({('USDC', 'EUR'): '1'}),
  )
  assert result.complete, result.exceptions
  assert performance(result) == [('across:out', 'income', Decimal('10'))]
  (lot,) = result.lots
  assert (lot.asset, lot.cost) == ('position:opaque:vault', Decimal('110'))


def test_a_fee_after_the_cost_is_recovered_is_a_gain():
  """A vault withdrawal's commission, a fee leg in the opaque compartment, is a fee expense; booked after the redemption took the cost to zero, its whole value is a `performance` gain at its time (rule 14.14.1). The total result is unchanged."""
  events = [
    event('buy', 1, leg('USDC', '10000'), leg('EUR', '-10000')),
    *move('deposit', 2, 'USDC', '10000', 'A', BOT),
    event('equity', 3, leg('USDC', '400', BOT, 'contents')),
    event(
      'withdraw:out',
      4,
      leg('USDC', '-10300', BOT, 'transfer'),
      leg('USDC', '-100', BOT, 'expense', fee=True, label='fee'),
    ),
    event('withdraw:in', 4, leg('USDC', '10300', 'A', 'transfer')),
  ]
  result = run(
    events,
    links=links('deposit', 'withdraw'),
    policy=opaque(),
    pricing=FixedPricing({('USDC', 'EUR'): '1'}),
  )
  assert result.complete, result.exceptions
  assert [(f.event, f.asset, f.kind, f.label, f.value) for f in result.flows] == [
    ('withdraw:out', POSITION, 'income', 'performance', Decimal('300')),
    ('withdraw:out', 'USDC', 'expense', 'fee', Decimal('100')),
    ('withdraw:out', POSITION, 'income', 'performance', Decimal('100')),
  ]
  assert not [lot for lot in result.lots if lot.asset == POSITION]


def test_a_fee_beyond_the_remaining_cost_takes_it_to_zero():
  """Where the remaining cost is below the fee's value, the cost goes to zero and the rest is a `performance` gain at the fee's time (rule 14.14.1)."""
  events = [
    event('buy', 1, leg('USDC', '100'), leg('EUR', '-100')),
    *move('in', 2, 'USDC', '100', 'A', BOT),
    *move('out', 3, 'USDC', '95', BOT, 'A'),
    event('fee', 4, leg('USDC', '-8', BOT, 'expense', fee=True, label='fee')),
  ]
  result = run(
    events,
    links=links('in', 'out'),
    policy=opaque(),
    pricing=FixedPricing({('USDC', 'EUR'): '1'}),
  )
  assert result.complete, result.exceptions
  assert [(f.event, f.label, f.kind, f.value) for f in result.flows] == [
    ('fee', 'fee', 'expense', Decimal('8')),
    ('fee', 'performance', 'income', Decimal('3')),
  ]
  assert not [lot for lot in result.lots if lot.asset == POSITION]
  (row,) = [row for row in result.open_rows if row.kind == 'opaque']
  assert (row.quantity, row.cost) == (0, 0)


def test_a_fee_without_a_price_stays_in_the_result():
  """A reported fee that cannot be priced books nothing and reduces no cost; it still lowers the contents, so it lands in `performance`, with an informational item naming the record, compartment, asset and quantity (rule 14.14.3). The run stays complete, in strict mode too."""
  events = [
    event('buy', 1, leg('USDC', '100'), leg('EUR', '-100')),
    *move('in', 2, 'USDC', '100', 'A', BOT),
    event('fee', 3, leg('USDC', '-1', BOT, 'expense', fee=True, label='fee')),
    event('snapshot', 4, leg('USDC', '11', BOT, 'contents')),
    *move('out', 5, 'USDC', '110', BOT, 'A'),
  ]
  prices = [
    PriceRecord(
      asset='USDC', quote='EUR', time=t(day), source='market', price=Decimal(1)
    )
    for day in (2, 5)
  ]
  for strict in (False, True):
    result = run(
      events,
      links=links('in', 'out'),
      policy=opaque(),
      pricing=TablePricing(prices),
      strict=strict,
    )
    assert result.complete, result.exceptions
    (item,) = result.exceptions
    assert (item.code, item.event, item.detail) == (
      'unpriced_opaque_fee',
      'fee',
      {'asset': 'USDC', 'compartment': BOT, 'quantity': '-1'},
    )
    assert [(f.event, f.label, f.value) for f in result.flows] == [
      ('out:out', 'performance', Decimal(10))
    ]


def test_a_fee_outside_the_compartment_is_an_ordinary_fee():
  """A withdrawal fee charged in spot is booked by rule 7 and touches neither the position's cost nor its contents (rule 14.14.4)."""
  events = [
    event('buy', 1, leg('USDC', '100'), leg('EUR', '-100')),
    *move('in', 2, 'USDC', '90', 'A', BOT),
    event('fee', 3, leg('USDC', '-1', 'A', 'expense', fee=True, label='fee')),
  ]
  result = run(
    events,
    links=links('in'),
    policy=opaque(),
    pricing=FixedPricing({('USDC', 'EUR'): '1'}),
  )
  assert result.complete, result.exceptions
  assert [(f.compartment, f.label, f.value) for f in result.flows] == [
    ('A', 'fee', Decimal('1'))
  ]
  (row,) = [row for row in result.open_rows if row.kind == 'opaque']
  assert row.cost == 90


def test_a_redemption_without_a_price_is_unbooked():
  """A redemption needs the market value of what comes out: without a price both sides are unbooked and the run is incomplete."""
  events = [
    event('buy', 1, leg('USDC', '100'), leg('EUR', '-100')),
    *move('in', 2, 'USDC', '100', 'A', BOT),
    *move('out', 3, 'USDE', '100', BOT, 'A'),
  ]
  result = run(
    events,
    links=links('in', 'out'),
    policy=opaque(),
    pricing=FixedPricing({('USDC', 'EUR'): '1'}),
  )
  assert not result.complete
  assert [item.code for item in result.exceptions] == [
    'price_gap',
    'unbooked',
    'unbooked',
  ]


def test_what_an_opaque_compartment_does_not_take():
  """Any leg other than `transfer` or `contents` in an opaque compartment is unbooked, as is a `contents` leg elsewhere (rules 14.1 and 14.8); a fee `contents` leg is invalid."""
  result = run(
    [
      event('result', 1, leg('USDC', '5', BOT, 'income', label='performance')),
      event('stray', 2, leg('USDC', '5', 'A', 'contents')),
      event('fee', 3, leg('USDC', '-1', BOT, 'contents', fee=True)),
    ],
    policy=opaque(),
    pricing=FixedPricing({('USDC', 'EUR'): '1'}),
  )
  assert not result.complete
  assert [(item.code, item.event) for item in result.exceptions] == [
    ('invalid_event', 'fee'),
    ('unbooked', 'result'),
    ('unbooked', 'stray'),
  ]
  assert not result.flows and not result.lots


def test_contents_that_do_not_empty_recognise_nothing():
  """Under the interim rule (specs#135) an opaque result that leaves the compartment holding something is no true-up: nothing is recognised until a redemption or an empty snapshot."""
  events = [
    event('buy', 1, leg('USDC', '100'), leg('EUR', '-100')),
    *move('in', 2, 'USDC', '100', 'A', BOT),
    event('snapshot', 3, leg('USDC', '-40', BOT, 'contents')),
  ]
  result = run(
    events,
    links=links('in'),
    policy=opaque(),
    pricing=FixedPricing({('USDC', 'EUR'): '1'}),
  )
  assert result.complete, result.exceptions
  assert not result.flows
  (held,) = [row for row in result.open_rows if row.asset == POSITION]
  assert (held.kind, held.quantity, held.cost) == ('opaque', 100, 100)


def test_a_compartment_not_declared_opaque_books_as_before():
  """Without `opaque_compartments` nothing changes: a `performance` leg is income in its asset."""
  events = [
    event('buy', 1, leg('USDC', '100'), leg('EUR', '-100')),
    *move('in', 2, 'USDC', '100', 'A', BOT),
    event('snapshot', 3, leg('USDC', '5', BOT, 'income', label='performance')),
  ]
  result = run(
    events,
    links=links('in'),
    policy=policy(),
    pricing=FixedPricing({('USDC', 'EUR'): '1'}),
  )
  assert result.complete, result.exceptions
  assert [(f.asset, f.value) for f in result.flows] == [('USDC', Decimal('5'))]
