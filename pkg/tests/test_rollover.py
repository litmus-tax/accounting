"""Synthetic lifecycle and conservation acceptance cases for generic rollover."""

from dataclasses import replace
from datetime import datetime, timezone, timedelta
from decimal import Decimal as D
import json
from pathlib import Path
from typing_extensions import Literal
import pytest
from pydantic import ValidationError
from litmus.accounting import run, FixedPricing
from litmus.accounting.codec import parse_ledger
from litmus.accounting.engine.arithmetic import exact_sum
from litmus.accounting.model import (
  Event,
  Leg,
  Policy,
  Rollover,
  RolloverInput,
  RolloverOutput,
  NotionalScope,
)

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).resolve().parent / 'fixtures'

T = datetime(2026, 1, 1, tzinfo=timezone.utc)
P = Policy('fifo', 'compartment', 'EUR')


def buy(
  event: str = 'buy',
  *,
  quantity: str = '10',
  cost: str = '1000',
  compartment: str = 'wallet',
  time: datetime = T,
) -> Event:
  """Create a priced ordinary purchase without fetching a price."""
  return Event(
    event,
    time,
    (
      Leg('A', D(quantity), compartment, 'trade'),
      Leg('EUR', -D(cost), compartment, 'trade'),
    ),
  )


def carry(
  event: str = 'carry',
  *,
  quantity: str = '10',
  outputs: tuple[tuple[str, str], ...] = (('B', '100'),),
  allocations: tuple[str, ...] = (),
  lots: tuple[str, ...] = (),
  costs: str = '0',
  date: Literal['carry', 'operation'] = 'carry',
) -> Event:
  """Build a synthetic generic operation using explicit leg references."""
  legs = (
    Leg('A', -D(quantity), 'wallet', 'rollover'),
    *(Leg(asset, D(amount), 'wallet', 'rollover') for asset, amount in outputs),
  )
  operation = Rollover(
    (RolloverInput(0, lots),),
    tuple(
      RolloverOutput(index + 1, D(allocations[index]) if allocations else None)
      for index in range(len(outputs))
    ),
    acquisition_date=date,
    capitalized_costs=D(costs),
    cost_reference='evidence:fee' if D(costs) else None,
  )
  return Event(event, T + timedelta(days=2), legs, rollover=operation)


def execute(events: tuple[Event, ...], policy: Policy = P):
  """Run an offline ledger with deliberately no market prices."""
  return run(events, policy=policy, pricing=FixedPricing({}))


@pytest.mark.parametrize('method', ['fifo', 'lifo', 'hifo', 'average'])
def test_multiple_lots_outputs_partial_and_lineage(method):
  """A partial transformation preserves basis and original acquisition events."""
  events = (
    buy(),
    buy('second', quantity='10', cost='2000', time=T + timedelta(days=1)),
    carry(
      quantity='15',
      outputs=(('B', '7'), ('C', '3')),
      allocations=('0.3333333333333333333333333333', '0.6666666666666666666666666667'),
    ),
  )
  result = execute(events, replace(P, cost_method=method))
  assert result.complete and not result.exceptions
  assert not result.realized and not result.prices
  record = result.rollovers[0]
  assert record.basis_in == record.basis_out
  assert exact_sum(lot.cost for lot in result.lots) == D('3000')
  assert all(
    exact_sum(origin.cost for origin in lot.origins) == lot.cost for lot in result.lots
  )
  for piece in record.consumed:
    for origin in piece.origins:
      assert (
        sum(
          o.cost
          for created in record.created
          for o in created.origins
          if o.event == origin.event
        )
        >= 0
      )
  assert sum(piece.quantity for piece in record.created if piece.leg == 1) == 7
  assert sum(piece.quantity for piece in record.created if piece.leg == 2) == 3


def test_capitalized_costs_are_explicit_and_do_not_imply_payment():
  """Capitalization adds basis with an evidence reference and no expense flow."""
  result = execute((buy(), carry(costs='17.32')))
  record = result.rollovers[0]
  assert record.basis_out == record.basis_in + D('17.32')
  assert result.lots[0].cost == D('1017.32')
  assert not result.flows
  assert result.lots[0].origins[-1].event == 'carry'


def test_selected_lots_and_effective_date_preserve_origins():
  """An explicit selection overrides FIFO without erasing original dates."""
  result = execute(
    (
      buy(),
      buy('second', cost='2000', time=T + timedelta(days=1)),
      carry(lots=('lot-2',), date='operation'),
    )
  )
  receipt = next(lot for lot in result.lots if lot.asset == 'B')
  assert receipt.cost == 2000
  assert receipt.acquired == T + timedelta(days=2)
  assert receipt.origins[0].acquired == T + timedelta(days=1)


@pytest.mark.parametrize(
  'operation',
  [
    carry(quantity='11'),
    carry(lots=('absent',)),
    carry(outputs=(('B', '1'), ('C', '1'))),
    carry(allocations=('0.9',)),
  ],
)
def test_impossible_inputs_or_allocations_are_atomic(operation):
  """An invalid operation neither consumes existing basis nor invents inventory."""
  result = execute((buy(), operation))
  assert not result.complete
  assert [(lot.asset, lot.quantity, lot.cost) for lot in result.lots] == [
    ('A', D('10'), D('1000'))
  ]
  assert not result.rollovers


def test_write_off_has_zero_proceeds_and_loss():
  """Explicit extinguishment releases all remaining basis as a loss."""
  event = carry(outputs=())
  assert event.rollover is not None
  event = replace(event, rollover=replace(event.rollover, write_off=True))
  result = execute((buy(), event))
  assert result.complete and not result.lots
  assert result.realized[0].pnl == -1000
  assert result.rollovers[0].written_off == 1000


def test_zero_basis_preserves_units_and_lineage():
  """Zero-basis acquisitions remain traceable across receipt construction."""
  result = run(
    (Event('free', T, (Leg('A', D(10), 'wallet', 'income'),)), carry()),
    policy=P,
    pricing=FixedPricing({('A', 'EUR'): '0'}),
  )
  assert result.complete
  assert result.lots[0].quantity == 100 and result.lots[0].cost == 0
  assert result.lots[0].origins[0].event == 'free'


def test_same_time_dependencies_and_runtime_failure():
  """Dependencies order nested conversions and block successors of missing lots."""
  first = replace(carry(), time=T, depends_on=('buy',))
  second = replace(
    first,
    id='nested',
    depends_on=('carry',),
    legs=(
      Leg('B', D(-100), 'wallet', 'rollover'),
      Leg('C', D(1), 'wallet', 'rollover'),
    ),
  )
  result = execute((second, first, buy()))
  assert result.complete and result.lots[0].asset == 'C'
  assert result.lots[0].origins[0].event == 'buy'
  assert first.rollover is not None
  bad = replace(
    first, rollover=replace(first.rollover, inputs=(RolloverInput(0, ('missing',)),))
  )
  result = execute((second, bad, buy()))
  assert not result.complete and not result.rollovers
  assert any(
    item.event == 'nested' and item.code == 'unbooked' for item in result.exceptions
  )


@pytest.mark.parametrize('dependencies', [('missing',), ('carry',), ('buy', 'buy')])
def test_invalid_dependencies(dependencies):
  """Unknown, cyclic and duplicate operation references are rejected."""
  result = execute((buy(), replace(carry(), depends_on=dependencies)))
  assert not result.complete and not result.rollovers


@pytest.mark.parametrize('scope', ['global', 'compartment'])
def test_notional_scopes_isolate_spot_and_margin_inventory(scope):
  """Eligible settlement negatives cannot use or conceal real CEX holdings."""
  policy = replace(
    P, lot_scope=scope, notional_scopes=(NotionalScope('hyperliquid', 'A'),)
  )
  events = (
    buy(compartment='cex'),
    Event(
      'short',
      T + timedelta(days=1),
      (
        Leg('A', D(-20), 'hyperliquid', 'trade'),
        Leg('EUR', D(2000), 'hyperliquid', 'trade'),
      ),
    ),
    Event(
      'cex-short',
      T + timedelta(days=2),
      (Leg('A', D(-11), 'cex', 'trade'), Leg('EUR', D(1100), 'cex', 'trade')),
    ),
  )
  result = execute(events, policy)
  assert [
    item.event for item in result.exceptions if item.code == 'negative_position'
  ] == ['cex-short']
  assert any(
    lot.compartment == 'hyperliquid' and lot.quantity == -20 for lot in result.lots
  )
  assert result.realized[0].event == 'cex-short'


def test_claim_lifecycle_fixture_and_exchange_entry():
  """Carry and exchange recognize €300 total with distinct €200/€100 timing."""
  ledger = parse_ledger((ROOT / 'examples/rollover.json').read_text())
  carried = execute(ledger.events)
  assert sum(row.pnl for row in carried.realized) == 300
  entry = ledger.events[1]
  exchanged = replace(
    entry, rollover=None, legs=tuple(replace(leg, tag='trade') for leg in entry.legs)
  )
  result = run(
    (ledger.events[0], exchanged, *ledger.events[2:]),
    policy=P,
    pricing=FixedPricing({('RECEIPT', 'EUR'): '12'}),
  )
  assert [row.pnl for row in result.realized] == [200, 100]
  assert result.complete


def test_receivable_recognition_and_settlement_do_not_duplicate_gain():
  """Crystallizing a €1300 claim realizes €300, later collection realizes zero."""
  ledger = parse_ledger((ROOT / 'examples/rollover.json').read_text())
  claim = ledger.events[2]
  claim = replace(
    claim, rollover=None, legs=tuple(replace(leg, tag='trade') for leg in claim.legs)
  )
  result = run(
    (*ledger.events[:2], claim, ledger.events[3]),
    policy=P,
    pricing=FixedPricing({('CLAIM', 'EUR'): '1300'}),
  )
  assert [row.pnl for row in result.realized] == [300, 0]
  assert not result.lots


def test_missing_exit_price_is_incomplete_without_destroying_claim():
  """An unavailable proceeds price leaves the claim's historical basis intact."""
  ledger = parse_ledger((ROOT / 'examples/rollover.json').read_text())
  exit_event = ledger.events[-1]
  exit_event = replace(
    exit_event, legs=(exit_event.legs[0], replace(exit_event.legs[1], asset='UNPRICED'))
  )
  result = execute((*ledger.events[:-1], exit_event))
  assert not result.complete
  assert result.lots[0].asset == 'CLAIM' and result.lots[0].cost == 1000
  assert not result.realized


def test_wire_boundary_rejects_unknown_fields():
  """A misspelled policy cannot silently disable accounting behavior."""
  document = json.loads((ROOT / 'examples/rollover.json').read_text())
  document['policy']['notional_scope'] = []
  with pytest.raises(ValidationError):
    parse_ledger(json.dumps(document))


def test_paid_capitalized_fee_is_disposed_once_without_expense():
  """A paid EUR fee adds basis and its cash leg appears exactly once."""
  event = carry(costs='10')
  assert event.rollover is not None
  event = replace(
    event,
    legs=(*event.legs, Leg('EUR', D(-10), 'wallet', 'expense', fee=True)),
    rollover=replace(event.rollover, capitalized_fee_legs=(2,)),
  )
  result = execute((buy(), event))
  assert result.complete and result.lots[0].cost == 1010
  assert not result.flows
  assert next(row.quantity for row in result.balances if row.asset == 'EUR') == -1010


def test_capitalized_fee_gap_leaves_entire_operation_unbooked():
  """No fee price means neither receipt acquisition nor paid fee booking."""
  event = carry(costs='10')
  assert event.rollover is not None
  event = replace(
    event,
    legs=(*event.legs, Leg('GAS', D(-1), 'wallet', 'expense', fee=True)),
    rollover=replace(event.rollover, capitalized_fee_legs=(2,)),
  )
  result = execute((buy(), event))
  assert not result.complete and not result.rollovers
  assert result.lots[0].asset == 'A' and result.lots[0].cost == 1000


def test_mixed_zero_basis_origins_survive():
  """The paid receipt retains ancestry from a zero-basis contribution too."""
  free = Event('free', T, (Leg('A', D(10), 'wallet', 'income'),))
  result = run(
    (free, buy(), carry(quantity='20')),
    policy=P,
    pricing=FixedPricing({('A', 'EUR'): '0'}),
  )
  assert result.complete
  assert {origin.event for lot in result.lots for origin in lot.origins} == {
    'free',
    'buy',
  }


def test_partial_claim_sale_releases_only_sold_units():
  """A claim can be sold before collection without awaiting any venue action."""
  ledger = parse_ledger((ROOT / 'examples/rollover.json').read_text())
  settlement = ledger.events[-1]
  settlement = replace(
    settlement,
    legs=(
      replace(settlement.legs[0], quantity=D('-0.4')),
      replace(settlement.legs[1], quantity=D(520)),
    ),
  )
  result = execute((*ledger.events[:-1], settlement))
  assert result.realized[0].pnl == 120
  assert result.lots[0].quantity == D('0.6') and result.lots[0].cost == 600
  assert exact_sum(origin.cost for origin in result.lots[0].origins) == 600


def test_rollover_lineage_totals_by_original_event():
  """Repeating allocations conserve each original acquisition exactly."""
  result = execute(
    (
      buy(),
      buy('second', cost='2000', time=T + timedelta(days=1)),
      carry(
        quantity='20',
        outputs=(('B', '3'), ('C', '7')),
        allocations=(
          '0.3333333333333333333333333333',
          '0.6666666666666666666666666667',
        ),
      ),
    )
  )
  for event, expected in [('buy', D(1000)), ('second', D(2000))]:
    assert (
      exact_sum(
        origin.cost
        for lot in result.lots
        for origin in lot.origins
        if origin.event == event
      )
      == expected
    )


@pytest.mark.parametrize('name', ['spot', 'perp', 'pnl_settled', 'bridge', 'loan'])
def test_retained_baseline_goldens(name):
  """Every ordinary example retains its financial output under additive schema fields."""
  from litmus.accounting.codec import dump_result
  from litmus.accounting import TablePricing

  ledger = parse_ledger((ROOT / f'examples/{name}.json').read_text())
  assert ledger.policy is not None
  result = run(
    ledger.events,
    links=ledger.links,
    policy=ledger.policy,
    pricing=TablePricing(ledger.prices, max_age=ledger.max_age),
  )
  actual = json.loads(dump_result(result))
  expected = json.loads((FIXTURES / f'baseline/{name}.json').read_text())
  actual.pop('schema_version')
  actual.pop('rollovers')
  actual['policy'].pop('notional_scopes')
  for lot in actual['lots']:
    lot.pop('origins')
  assert actual == expected


def test_multiple_inputs_and_rounding_keep_unrounded_audit_basis():
  """Different contributed assets keep separate origin histories after monetary rounding."""
  second = Event(
    'second',
    T,
    (Leg('X', D(2), 'wallet', 'trade'), Leg('EUR', D('-23.457'), 'wallet', 'trade')),
  )
  operation = Event(
    'combine',
    T + timedelta(days=1),
    (
      Leg('A', D(-10), 'wallet', 'rollover'),
      Leg('X', D(-2), 'wallet', 'rollover'),
      Leg('B', D(7), 'wallet', 'rollover'),
    ),
    rollover=Rollover((RolloverInput(0), RolloverInput(1)), (RolloverOutput(2),)),
  )
  result = execute((buy(cost='13.123'), second, operation), replace(P, minor_unit=2))
  assert result.complete
  assert result.rollovers[0].basis_in == result.rollovers[0].basis_out == D('36.580')
  assert {
    origin.event for piece in result.rollovers[0].created for origin in piece.origins
  } == {'buy', 'second'}


def test_duplicate_leg_ownership_and_lot_references_fail():
  """Neither a leg nor a selected input lot can be claimed twice."""
  event = carry()
  assert event.rollover is not None
  event = replace(
    event,
    rollover=replace(
      event.rollover,
      inputs=(RolloverInput(0, ('lot-1',)), RolloverInput(0, ('lot-1',))),
    ),
  )
  result = execute((buy(), event))
  assert not result.complete and not result.rollovers


def test_unknown_and_duplicate_scopes_are_invalid():
  """Eligibility declarations must name real, unique ledger identities."""
  for scopes in [
    (NotionalScope('missing', 'A'),),
    (NotionalScope('wallet', 'A'), NotionalScope('wallet', 'A')),
  ]:
    result = execute((buy(),), replace(P, notional_scopes=scopes))
    assert not result.complete
    assert any(item.code == 'invalid_event' for item in result.exceptions)


def test_global_linked_transfer_cannot_hide_spot_deficit():
  """Pooled inventory in another wallet does not certify a source's actual balance."""
  from litmus.accounting.model import Link

  policy = replace(
    P, lot_scope='global', notional_scopes=(NotionalScope('margin', 'A'),)
  )
  margin = buy('margin-buy', compartment='margin')
  outgoing = Event(
    'out', T + timedelta(days=1), (Leg('A', D(-4), 'empty-spot', 'transfer'),)
  )
  incoming = Event('in', T + timedelta(days=1), (Leg('A', D(4), 'dest', 'transfer'),))
  result = run(
    (buy(), margin, outgoing, incoming),
    policy=policy,
    pricing=FixedPricing({}),
    links=(Link('out', 'in'),),
  )
  assert [(item.event, item.code) for item in result.exceptions] == [
    ('out', 'negative_position')
  ]


def test_destination_first_link_has_no_spurious_scope_deficit():
  """Both linked scope deltas post together even when receipt is observed first."""
  from litmus.accounting.model import Link

  policy = replace(
    P, lot_scope='global', notional_scopes=(NotionalScope('margin', 'A'),)
  )
  outgoing = Event(
    'out', T + timedelta(days=2), (Leg('A', D(-4), 'wallet', 'transfer'),)
  )
  incoming = Event('in', T + timedelta(days=1), (Leg('A', D(4), 'dest', 'transfer'),))
  result = run(
    (buy(), buy('margin-buy', compartment='margin'), outgoing, incoming),
    policy=policy,
    pricing=FixedPricing({}),
    links=(Link('out', 'in'),),
  )
  assert result.complete and not result.exceptions


def test_functional_currency_contribution_has_known_basis():
  """EUR contributed to a receipt carries exact face-value basis without a lot."""
  event = carry(quantity='1000')
  event = replace(event, legs=(replace(event.legs[0], asset='EUR'), event.legs[1]))
  result = execute((event,))
  assert result.complete and result.lots[0].cost == 1000
  assert result.rollovers[0].consumed[0].lot == 'cash:carry:0'
  assert not result.prices


def test_tiny_final_input_basis_is_not_dropped():
  """Extreme weight differences retain positive quantities and every basis unit."""
  tiny = buy(
    'tiny',
    quantity='1',
    cost='0.000000000000000000000000000001',
    time=T + timedelta(days=1),
  )
  result = execute(
    (
      buy(quantity='1', cost='10000000000000000000000000000'),
      tiny,
      carry(quantity='2', outputs=(('B', '1'),)),
    )
  )
  assert result.complete
  record = result.rollovers[0]
  assert record.basis_in == record.basis_out
  assert len(record.created) == 2 and all(
    piece.quantity > 0 for piece in record.created
  )
  assert exact_sum(piece.quantity for piece in record.created) == 1
  assert exact_sum(
    origin.cost
    for lot in result.lots
    for origin in lot.origins
    if origin.event == 'tiny'
  ) == D('0.000000000000000000000000000001')


@pytest.mark.parametrize('scope', ['compartment', 'global'])
def test_synthetic_settlement_fragments(scope):
  """Synthetic margin settlement does not hide unrelated spot deficits."""
  from litmus.accounting import TablePricing

  fixture = json.loads((FIXTURES / 'synthetic-scoped-settlement.json').read_text())
  ledger = parse_ledger(json.dumps(fixture['ledger']))
  assert ledger.policy is not None
  policy = replace(ledger.policy, lot_scope=scope)
  result = run(ledger.events, policy=policy, pricing=TablePricing(ledger.prices))
  assert result.complete
  negatives = [item for item in result.exceptions if item.code == 'negative_position']
  assert {item.detail['compartment'] for item in negatives} == {
    'exchange-a:spot',
    'exchange-b:spot',
  }
  assert {scope.asset for scope in policy.notional_scopes} == {
    'USD-A',
    'USD-B',
    'USD-C',
  }
  assert not any(item.code == 'price_gap' for item in result.exceptions)


@pytest.mark.parametrize('transformed', [False, True])
def test_fractional_basis_remainders_preserve_ordinary_arithmetic(transformed):
  """Exact rollover ancestry must not move legacy ordinary half-cent thresholds."""
  from litmus.accounting.engine.lots import LotBook
  from litmus.accounting.engine.arithmetic import difference

  book = LotBook('fifo')
  cost = D('3.333333333333333333333333333')
  lot = book.open(
    ('wallet', 'A'),
    quantity=D(10),
    cost=cost,
    time=T,
    event='opening',
    exact_basis=transformed,
  )
  taken = book.take(('wallet', 'A'), D(1))
  share = cost / 10
  expected = difference(cost, share) if transformed else cost - share
  assert taken[0][2] == share
  assert lot.cost == expected
  assert exact_sum(origin.cost for origin in lot.origins) == expected
  assert (lot.cost == D('3.000000000000000000000000000')) is not transformed


def test_ordinary_average_pool_retains_legacy_addition_precision():
  """Average pools opt into exact basis only after an explicit rollover touches them."""
  from litmus.accounting.engine.lots import LotBook

  book = LotBook('average')
  first = D('1000000000000000000000000000')
  added = D('0.01')
  book.open(('wallet', 'A'), quantity=D(1), cost=first, time=T, event='first')
  lot = book.open(('wallet', 'A'), quantity=D(1), cost=added, time=T, event='second')
  assert lot.cost == first + added
  assert exact_sum(origin.cost for origin in lot.origins) == lot.cost
