"""Liabilities: `borrow` and `repay` legs, `liability_valuation`, `Liability` output and valuation."""

from decimal import Decimal as D
import pytest
from litmus.accounting import run, value, FixedPricing
from litmus.accounting.model import Result
from tests.conftest import leg, event, policy, t


def codes(result: Result) -> list[str]:
  """Exception codes of a result."""
  return [x.code for x in result.exceptions]


PRICES = FixedPricing({('USDC', 'EUR'): '0.9', ('ETH', 'EUR'): '2000'})


def loan(*, repay: str = '-5020'):
  """Borrow 5000 USDC, accrue 20 of interest, buy 20 USDC with ETH, repay `repay`."""
  return [
    event('buy', 1, leg('ETH', '1'), leg('EUR', '-2000')),
    event('borrow', 2, leg('USDC', '5000', tag='borrow', label='aave-v3')),
    event('accrue', 3, leg('USDC', '20', tag='borrow', label='interest')),
    event('swap', 4, leg('ETH', '-0.01'), leg('USDC', '20')),
    event('repay', 5, leg('USDC', repay, tag='repay', label='aave-v3')),
  ]


def test_borrow_opens_a_lot_and_a_liability_without_income(method, scope):
  """The asset received is a lot at market value; the same quantity is owed; no flow, no PnL."""
  r = run(loan()[:2], policy=policy(method, scope), pricing=PRICES)
  assert codes(r) == []
  usdc = next(l for l in r.lots if l.asset == 'USDC')
  assert (usdc.quantity, usdc.cost, usdc.event) == (D('5000'), D('4500'), 'borrow')
  (owed,) = r.liabilities
  assert (owed.asset, owed.compartment, owed.quantity, owed.cost, owed.label) == (
    'USDC',
    'A',
    D('5000'),
    D('4500'),
    'aave-v3',
  )
  assert (owed.opened, owed.updated, owed.event) == (t(2), t(2), 'borrow')
  assert r.flows == () and r.realized == ()


def test_interest_accrual_grows_the_liability_only(method, scope):
  """A `borrow` labelled `interest` receives nothing: no lot, the liability grows."""
  r = run(loan()[:3], policy=policy(method, scope), pricing=PRICES)
  assert sum(l.quantity for l in r.lots if l.asset == 'USDC') == D('5000')
  (owed,) = r.liabilities
  assert (owed.quantity, owed.cost, owed.label, owed.updated) == (
    D('5020'),
    D('4518'),
    'aave-v3',
    t(3),
  )


def test_repay_is_a_disposal_at_market_and_closes_the_liability(method, scope):
  """Repaying disposes of the asset with its normal PnL; under `cost` the liability itself has none."""
  r = run(loan(), policy=policy(method, scope), pricing=PRICES)
  assert codes(r) == []
  assert r.liabilities == ()
  repaid = [x for x in r.realized if x.event == 'repay']
  assert [(x.asset, x.quantity, x.proceeds, x.cost, x.pnl) for x in repaid] == [
    ('USDC', D('-5020'), D('4518'), D('4518'), D('0'))
  ]
  assert [l.asset for l in r.lots] == ['ETH']


def test_partial_repay_releases_proportional_basis(method, scope):
  """Repaying part of the debt releases the same share of its basis."""
  r = run(loan(repay='-2510'), policy=policy(method, scope), pricing=PRICES)
  (owed,) = r.liabilities
  assert (owed.quantity, owed.cost, owed.updated) == (D('2510'), D('2259'), t(5))


def test_market_valuation_realizes_the_liability(method, scope):
  """Under `market` the repaid part is revalued and the difference realized against the liability."""
  pricing = FixedPricing({('USDC', 'EUR'): '0.9', ('ETH', 'EUR'): '2000'})
  events = [
    event('borrow', 1, leg('USDC', '1000', tag='borrow', label='x')),
    event('repay', 2, leg('USDC', '-400', tag='repay', label='x')),
  ]
  r = run(
    events, policy=policy(method, scope, liability_valuation='market'), pricing=pricing
  )
  assert codes(r) == []
  asset, debt = r.realized
  assert (asset.quantity, asset.pnl) == (D('-400'), D('0'))
  assert (debt.quantity, debt.proceeds, debt.cost, debt.pnl, debt.lots) == (
    D('400'),
    D('-360'),
    D('-360'),
    D('0'),
    ('liability-1',),
  )
  moved = run(
    events,
    policy=policy(method, scope, liability_valuation='market'),
    pricing=StepPricing(),
  )
  asset, debt = moved.realized
  assert (asset.proceeds, asset.cost, asset.pnl) == (D('380'), D('360'), D('20'))
  assert (debt.proceeds, debt.cost, debt.pnl) == (D('-380'), D('-360'), D('-20'))
  assert sum(x.pnl for x in moved.realized) == 0


class StepPricing:
  """USDC/EUR 0.9 on day 1, 0.95 from day 2."""

  def price(self, asset, quote, time, *, source):
    """Step price for USDC; nothing else."""
    if (asset, quote) != ('USDC', 'EUR'):
      return None
    return D('0.9') if time < t(2) else D('0.95')


def test_repaying_more_than_owed_is_reported(method, scope):
  """A repay beyond the debt books and reports `negative_liability`; the figure stays honest."""
  events = [
    event('borrow', 1, leg('USDC', '100', tag='borrow', label='x')),
    event('repay', 2, leg('USDC', '-150', tag='repay', label='x')),
  ]
  r = run(
    events, policy=policy(method, scope, position_assets=('USDC',)), pricing=PRICES
  )
  assert codes(r) == ['negative_liability']
  assert r.exceptions[0].detail == {
    'asset': 'USDC',
    'compartment': 'A',
    'owed': '100',
    'repaid': '150',
  }
  (owed,) = r.liabilities
  assert (owed.quantity, owed.cost) == (D('-50'), D('-45'))
  assert r.complete


def test_repay_without_a_loan_opens_a_negative_liability(method, scope):
  """Repaying where nothing was borrowed is the same exception with `owed: 0`."""
  events = [
    event('dep', 1, leg('USDC', '100', tag='transfer')),
    event('repay', 2, leg('USDC', '-50', tag='repay', label='x')),
  ]
  r = run(events, policy=policy(method, scope), pricing=PRICES)
  assert codes(r) == ['unmatched_transfer', 'negative_liability']
  assert r.exceptions[1].detail['owed'] == '0'
  (owed,) = r.liabilities
  assert (owed.quantity, owed.cost, owed.event, owed.label) == (
    D('-50'),
    D('0'),
    'repay',
    'x',
  )


def test_liabilities_are_per_compartment_under_global_lots():
  """Lots may pool globally; what is owed stays with the compartment that owes it."""
  events = [
    event('b1', 1, leg('USDC', '100', 'A', tag='borrow', label='x')),
    event('b2', 1, leg('USDC', '50', 'B', tag='borrow', label='y')),
    event('repay', 2, leg('USDC', '-100', 'B', tag='repay', label='y')),
  ]
  r = run(events, policy=policy('fifo', 'global'), pricing=PRICES)
  assert codes(r) == ['negative_liability']
  assert [(l.compartment, l.quantity, l.label) for l in r.liabilities] == [
    ('A', D('100'), 'x'),
    ('B', D('-50'), 'y'),
  ]
  assert all(l.compartment is None for l in r.lots)


def test_borrow_and_repay_ordering_within_an_event():
  """Borrows book before trades and repays after transfers, so a borrow-swap-repay event works in one go."""
  events = [
    event(
      'loop',
      1,
      leg('ETH', '-1', tag='repay', label='x'),
      leg('ETH', '1'),
      leg('USDC', '-2000'),
      leg('USDC', '2000', tag='borrow', label='x'),
    ),
  ]
  r = run(events, policy=policy(), pricing=PRICES)
  assert codes(r) == ['negative_liability']
  assert [(x.asset, x.quantity) for x in r.realized] == [
    ('USDC', D('-2000')),
    ('ETH', D('-1')),
  ]
  assert [(l.asset, l.quantity) for l in r.liabilities] == [
    ('ETH', D('-1')),
    ('USDC', D('2000')),
  ]


def test_borrow_price_gap_leaves_the_leg_unbooked(method, scope):
  """A borrow that cannot be valued is neither a lot nor a liability."""
  r = run(
    [event('b', 1, leg('DOGE', '5', tag='borrow'))],
    policy=policy(method, scope),
    pricing=FixedPricing({}),
  )
  assert codes(r) == ['price_gap', 'unbooked']
  assert r.lots == () and r.liabilities == () and not r.complete


def test_validate_signs():
  """`borrow` must be positive, `repay` negative."""
  from litmus.accounting import validate

  bad = [
    event('b', 1, leg('USDC', '-1', tag='borrow')),
    event('r', 2, leg('USDC', '1', tag='repay')),
  ]
  assert [x.message for x in validate(bad)] == [
    'leg 0: borrow must be positive',
    'leg 0: repay must be negative',
  ]


@pytest.mark.parametrize('valuation', ['cost', 'market'])
def test_value_reports_liabilities_at_market(valuation):
  """`value` prices open liabilities; unrealized only under `market`."""
  p = policy('fifo', 'compartment', liability_valuation=valuation, minor_unit=2)
  r = run(loan(repay='-5000'), policy=p, pricing=PRICES)
  v = value(
    r.lots,
    liabilities=r.liabilities,
    at=t(31),
    policy=p,
    pricing=FixedPricing({('USDC', 'EUR'): '0.95', ('ETH', 'EUR'): '2100'}),
  )
  assert v.complete
  (owed,) = v.liabilities
  assert (owed.liability, owed.asset, owed.compartment, owed.label) == (
    'liability-1',
    'USDC',
    'A',
    'aave-v3',
  )
  assert (owed.quantity, owed.cost, owed.value) == (D('20'), D('18.00'), D('19.00'))
  assert owed.unrealized == (D('-1.00') if valuation == 'market' else None)
  assert [p.asset for p in v.prices] == ['ETH', 'USDC']


def test_value_liability_price_gap():
  """A liability that cannot be priced is a `price_gap` naming the liability."""
  p = policy('fifo', 'compartment')
  r = run(loan(repay='-5000'), policy=p, pricing=PRICES)
  v = value(
    r.lots, liabilities=r.liabilities, at=t(31), policy=p, pricing=FixedPricing({})
  )
  assert not v.complete
  gaps = [x for x in v.exceptions if x.detail.get('liability')]
  assert [(x.event, x.detail['liability']) for x in gaps] == [('borrow', 'liability-1')]
  assert v.liabilities[0].value is None and v.liabilities[0].unrealized is None


@pytest.mark.parametrize('valuation', ['cost', 'market'])
def test_signed_interest_reversal_changes_only_debt_at_carried_basis(valuation):
  """Superseded noncash accrual releases proportional debt basis, never cash or PnL."""
  from litmus.accounting.pricing import TablePricing
  from litmus.accounting.model import PriceRecord
  from datetime import timedelta

  events = [
    event('principal', 1, leg('USDC', '100', tag='borrow', label='facility')),
    event('accrual', 1, leg('USDC', '20', tag='borrow', label='interest')),
    event('supersede', 2, leg('USDC', '-5', tag='borrow', label='interest')),
  ]
  pricing = TablePricing(
    (PriceRecord('USDC', 'EUR', t(1), 'market', D('0.9')),), max_age=timedelta(hours=1)
  )
  result = run(events, policy=policy(liability_valuation=valuation), pricing=pricing)
  assert result.complete and not result.exceptions
  assert [(row.quantity, row.cost) for row in result.liabilities] == [
    (D(115), D('103.5'))
  ]
  assert [(lot.quantity, lot.cost) for lot in result.lots] == [(D(100), D(90))]
  assert not result.flows and not result.realized
  assert len(result.prices) == 1


def test_reversal_prevents_double_crystallized_interest():
  """An accrual included in both checkpoint and event is reduced exactly once."""
  events = [
    event('principal', 1, leg('USDC', '100', tag='borrow')),
    event('checkpoint', 2, leg('USDC', '20', tag='borrow', label='interest')),
    event('crystallized', 3, leg('USDC', '5', tag='borrow', label='interest')),
    event('supersede', 4, leg('USDC', '-5', tag='borrow', label='interest')),
  ]
  result = run(events, policy=policy(), pricing=PRICES)
  assert result.complete and not result.exceptions
  assert result.liabilities[0].quantity == 120
  assert result.liabilities[0].cost == 108
  assert result.lots[0].quantity == 100


@pytest.mark.parametrize('opened', ['0', '100'])
def test_excess_interest_reversal_is_reported_without_cash(opened):
  """An impossible reversal retains its negative debt and reports a safeguard."""
  events = (
    [] if opened == '0' else [event('principal', 1, leg('USDC', opened, tag='borrow'))]
  )
  events.append(
    event('supersede', 2, leg('USDC', '-150', tag='borrow', label='interest'))
  )
  result = run(events, policy=policy(), pricing=PRICES)
  assert codes(result) == ['negative_liability']
  assert result.liabilities[0].quantity == D(opened) - 150
  assert not result.flows and not result.realized
  assert sum(lot.quantity for lot in result.lots) == D(opened)


def test_interest_reversal_cannot_be_relabelled_as_paid_fee():
  """Only explicit noncash interest qualifies for signed borrow validation."""
  from litmus.accounting import validate

  for label, fee in [(None, False), ('facility', False), ('interest', True)]:
    invalid = event('bad', 1, leg('USDC', '-5', tag='borrow', label=label, fee=fee))
    assert validate((invalid,))[0].code == 'invalid_event'


def test_synthetic_signed_adjustments_replay_snapshot_quantity():
  """Synthetic signed deltas match debt checkpoints without wallet changes."""
  import json
  from pathlib import Path
  from datetime import datetime
  from litmus.accounting.model import Event, Leg

  fixtures = json.loads(
    (
      Path(__file__).resolve().parent / 'fixtures/synthetic-signed-debt-accrual.json'
    ).read_text()
  )
  for fixture in fixtures:
    source = fixture['source']
    scale = D(10) ** source['asset']['decimals']
    before_adjustment = (
      D(source['balance_from_raw']) + D(source['movements_raw'])
    ) / scale
    # Every quantity is synthetic. Unit price 1 tests carried-basis
    # mechanics, not a market-price fallback.
    opening = Event(
      'synthetic-opening',
      datetime.fromisoformat(source['from_time']),
      (Leg('USDC', before_adjustment, 'A', 'borrow'),),
    )
    reversal = Event(
      source['id'],
      datetime.fromisoformat(source['time']),
      (Leg('USDC', D(source['accrual']), 'A', 'borrow', label='interest'),),
    )
    result = run(
      (opening, reversal), policy=policy(), pricing=FixedPricing({('USDC', 'EUR'): '1'})
    )
    assert result.complete and not result.exceptions
    assert result.liabilities[0].quantity == D(source['balance_to_raw']) / scale
    assert result.lots[0].quantity == before_adjustment
    assert not result.flows and not result.realized


def test_cli_books_signed_interest_with_no_cash_movement(tmp_path, capsys):
  """The public JSON CLI accepts the signed reversal contract and emits debt only."""
  import json
  from litmus.accounting.cli import main
  from litmus.accounting.codec import ledger_adapter
  from litmus.accounting.model import Ledger, PriceRecord

  ledger = Ledger(
    events=(
      event('principal', 1, leg('USDC', '100', tag='borrow')),
      event('accrue', 2, leg('USDC', '20', tag='borrow', label='interest')),
      event('reverse', 3, leg('USDC', '-5', tag='borrow', label='interest')),
    ),
    policy=policy(),
    prices=(PriceRecord('USDC', 'EUR', t(1), 'market', D('0.9')),),
  )
  path = tmp_path / 'signed-interest.json'
  path.write_bytes(ledger_adapter.dump_json(ledger))
  assert main(['--json', 'run', str(path)]) == 0
  output = json.loads(capsys.readouterr().out)
  assert D(output['liabilities'][0]['quantity']) == 115
  assert D(output['lots'][0]['quantity']) == 100
  assert output['flows'] == output['realized'] == output['exceptions'] == []
