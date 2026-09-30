"""The P&L series: points over a grid, valued at market, with the check of rule 39.5."""

import json
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal as D
from pathlib import Path
import pytest
from litmus.accounting import run, codec, FixedPricing
from litmus.accounting.model import Leg, Result
from litmus.accounting.pricing import TablePricing
from tests.conftest import leg, event, policy, t

ROOT = Path(__file__).resolve().parents[2]
LEDGERS = sorted((ROOT / 'examples').glob('*.json')) + sorted(
  (Path(__file__).parent / 'fixtures' / 'policy05').glob('*.json')
)


def codes(result: Result) -> list[str]:
  """Exception codes of a result."""
  return [x.code for x in result.exceptions]


class Prices:
  """ETH 3,000 until day 3, 3,500 after; BTC-PERP marks 61,000 in USDC; USDC and USDT at 1."""

  def price(self, asset, quote, time, *, source):
    """Step prices by day."""
    if asset in ('USDC', 'USDT'):
      return D('1')
    if asset == 'ETH':
      return D('3000') if time < t(3) else D('3500')
    if (asset, quote) == ('BTC-PERP', 'USDC'):
      return D('61000')
    return None


@pytest.mark.parametrize('path', LEDGERS, ids=lambda p: p.stem)
def test_the_check_holds_at_every_day_end(path: Path):
  """Over a daily grid, every complete point's total P&L equals net assets minus contributions."""
  # policy 05 rules 39.1 and 39.5
  document = json.loads(path.read_text())
  ledger = codec.parse_ledger(json.dumps(document.get('ledger', document)))
  assert ledger.policy is not None
  days = sorted({e.time.date() for e in ledger.events})
  grid = [
    datetime.combine(day, datetime.max.time(), tzinfo=ledger.events[0].time.tzinfo)
    for day in days
  ]
  r = run(
    ledger.events,
    links=ledger.links,
    policy=replace(ledger.policy, minor_unit=None),
    pricing=TablePricing(ledger.prices),
    grid=grid,
  )
  assert 'series_mismatch' not in codes(r)
  assert len(r.series) == len(grid)
  for point in r.series:
    if point.complete:
      assert point.total_pnl is not None and point.net_assets is not None
      assert abs(point.total_pnl - (point.net_assets - point.contributions)) < D(
        '1e-15'
      )


def test_a_point_follows_every_event_up_to_its_instant():
  """A point at an event's own time includes it; one just before does not."""
  # policy 05 rule 39.2
  events = [event('buy', 2, leg('ETH', '1'), leg('EUR', '-3000'))]
  r = run(
    events,
    policy=policy(fc='EUR'),
    pricing=FixedPricing({('ETH', 'EUR'): '3000'}),
    grid=[t(2), t(2) - timedelta(seconds=1)],
  )
  before, at = r.series
  assert before.at < at.at
  assert [h.asset for h in before.holdings] == []
  assert [(h.asset, h.quantity) for h in at.holdings] == [
    ('ETH', D('1')),
    ('EUR', D('-3000')),
  ]


def test_unrealized_pnl_of_holdings_and_cumulative_flows():
  """Holdings are valued at market; realized P&L, income and expenses accumulate by label."""
  # policy 05 rules 39.2.1, 39.2.4 and 39.2.5
  events = [
    event('deposit', 1, leg('USDC', '10000', tag='transfer')),
    event('buy', 1, leg('ETH', '2'), leg('USDC', '-6000'), hour=13),
    event('sell', 4, leg('ETH', '-1'), leg('USDC', '3500')),
    event('stake', 4, leg('ETH', '0.1', tag='income', label='yield'), hour=13),
    event('gas', 5, leg('ETH', '-0.01', tag='expense', fee=True, label='gas')),
  ]
  r = run(events, policy=policy(fc='USD'), pricing=Prices(), grid=[t(2), t(6)])
  first, last = r.series
  assert first.total_pnl == 0 and first.contributions == D('10000')
  assert last.realized == D('500') + D('5')
  assert [(f.kind, f.label, f.value) for f in last.flows] == [
    ('expense', 'gas', D('35')),
    ('income', 'yield', D('350')),
  ]
  eth = next(h for h in last.holdings if h.asset == 'ETH')
  assert (eth.quantity, eth.value) == (D('1.09'), D('3815'))
  assert last.unrealized is not None
  assert last.total_pnl == last.realized + D('350') - D('35') + last.unrealized


@pytest.mark.parametrize('valuation', ['cost', 'market'])
def test_liabilities_are_at_market_in_the_series_under_both_options(valuation):
  """The series shows a liability's unrealized P&L whatever `liability_valuation` is; total P&L is the same."""
  # policy 05 rule 39.4
  events = [
    event('borrow', 1, leg('ETH', '1', tag='borrow', liability='debt')),
    event('repay', 5, leg('ETH', '-1', tag='repay', liability='debt')),
  ]
  r = run(
    events,
    policy=policy(fc='USD', liability_valuation=valuation),
    pricing=Prices(),
    grid=[t(4), t(6)],
  )
  open_, closed = r.series
  (debt,) = open_.liabilities
  assert (debt.cost, debt.value, debt.unrealized) == (D('3000'), D('3500'), D('-500'))
  assert open_.total_pnl == closed.total_pnl == 0
  assert closed.realized == 0 and closed.liabilities == ()


def test_open_positions_are_valued_at_the_mark_and_converge_to_the_realized_pnl():
  """Unrealized P&L of an open position at the close price equals what the close realizes."""
  # policy 05 rules 39.2.3 and 39.3
  events = [
    event('deposit', 1, leg('USDC', '10000', 'P', tag='transfer')),
    event(
      'open',
      2,
      Leg('BTC-PERP', D('1'), 'P', 'position'),
      Leg('USDC', D('-60000'), 'P', 'notional'),
    ),
    event(
      'close',
      5,
      Leg('BTC-PERP', D('-1'), 'P', 'position'),
      Leg('USDC', D('61000'), 'P', 'notional'),
    ),
  ]
  r = run(events, policy=policy(fc='USD'), pricing=Prices(), grid=[t(4), t(6)])
  open_, closed = r.series
  (position,) = open_.positions
  assert (position.size, position.entry, position.mark, position.unrealized) == (
    D('1'),
    D('60000'),
    D('61000'),
    D('1000'),
  )
  assert open_.total_pnl == closed.total_pnl == D('1000')
  assert closed.positions == ()


def test_an_unpriced_asset_is_not_valued_and_the_point_is_incomplete():
  """A gap is never valued at zero: the point names it and has no total."""
  # policy 05 rule 39.6
  events = [event('buy', 1, leg('DOGE', '10'), leg('EUR', '-1'))]
  r = run(events, policy=policy(fc='EUR'), pricing=FixedPricing({}), grid=[t(2)])
  (point,) = r.series
  assert not point.complete and point.missing == ('DOGE',)
  doge = next(h for h in point.holdings if h.asset == 'DOGE')
  assert doge.value is None and doge.unrealized is None
  assert point.total_pnl is None and point.net_assets is None
  assert r.complete


def test_a_naive_grid_instant_is_reported_and_skipped():
  """Grid instants must carry a timezone."""
  # policy 05 rule 39.1
  r = run([], policy=policy(), pricing=FixedPricing({}), grid=[datetime(2026, 1, 1)])
  assert codes(r) == ['invalid_grid'] and r.series == ()


def test_series_money_is_rounded_row_by_row():
  """Rounded points recompute unrealized and totals from their rounded parts."""
  # policy 05 rule 20
  events = [event('buy', 1, leg('ETH', '1'), leg('EUR', '-1000.004'))]
  r = run(
    events,
    policy=policy(fc='EUR', minor_unit=2),
    pricing=FixedPricing({('ETH', 'EUR'): '1000.006'}),
    grid=[t(2)],
  )
  (point,) = r.series
  eth = next(h for h in point.holdings if h.asset == 'ETH')
  assert (eth.cost, eth.value, eth.unrealized) == (
    D('1000.00'),
    D('1000.01'),
    D('0.01'),
  )
  assert point.total_pnl == D('0.01')


def test_the_cli_reads_the_ledger_grid(tmp_path, capsys):
  """`accounting run` passes the ledger's `grid` to the run."""
  # policy 05 rule 39.1
  from litmus.accounting.cli import main
  from litmus.accounting.model import Ledger, PriceRecord

  ledger = Ledger(
    events=(event('buy', 1, leg('ETH', '1'), leg('EUR', '-3000')),),
    policy=policy(fc='EUR'),
    prices=(PriceRecord('ETH', 'EUR', t(1), 'market', D('3500')),),
    grid=(t(2),),
  )
  path = tmp_path / 'grid.json'
  path.write_bytes(codec.ledger_adapter.dump_json(ledger))
  assert main(['--json', 'run', str(path)]) == 0
  (point,) = json.loads(capsys.readouterr().out)['series']
  assert D(point['total_pnl']) == 500
