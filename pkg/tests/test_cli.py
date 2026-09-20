"""CLI: verbs, `--json` placement, exit codes."""

import json
from decimal import Decimal
from pathlib import Path
import pytest
from litmus.accounting.cli import main

EXAMPLES = Path(__file__).resolve().parents[2] / 'examples'
PERP = str(EXAMPLES / 'perp.json')
SPOT = str(EXAMPLES / 'spot.json')
BRIDGE = str(EXAMPLES / 'bridge.json')


def test_run_json_reports_exceptions(capsys):
  """The perp example has one unmatched deposit, the completed command reports it in JSON."""
  assert main(['run', PERP, '--json']) == 0
  out = json.loads(capsys.readouterr().out)
  assert [x['code'] for x in out['exceptions']] == ['unmatched_transfer']
  assert sum(
    Decimal(r['pnl']) for r in out['realized'] if r['asset'] == 'BTC-PERP'
  ) == Decimal('4500')
  assert out['complete']


def test_run_clean_exits_0(capsys):
  """A ledger with no exceptions exits 0."""
  assert main(['run', SPOT]) == 0
  assert 'realized pnl: -5055.00' in capsys.readouterr().out


def test_json_before_or_after_the_verb(capsys):
  """`--json` is accepted on either side of the verb."""
  assert main(['--json', 'run', SPOT]) == 0
  before = capsys.readouterr().out
  assert main(['run', SPOT, '--json']) == 0
  assert json.loads(before) == json.loads(capsys.readouterr().out)


def test_run_text(capsys):
  """Human output mentions the policy and the PnL."""
  assert main(['run', PERP]) == 0
  out = capsys.readouterr().out
  assert 'realized pnl: 4500' in out and 'fifo / compartment' in out


def test_missing_file(capsys):
  """Unreadable input exits 2."""
  assert main(['run', '/nonexistent.json']) == 2
  assert 'cannot read' in capsys.readouterr().err


def test_invalid_input(tmp_path, capsys):
  """Schema violations exit 2 with a message on stderr."""
  bad = tmp_path / 'bad.json'
  bad.write_text('{"events": [{"id": "x"}]}')
  assert main(['run', str(bad)]) == 2
  assert 'invalid input' in capsys.readouterr().err


def test_no_policy(tmp_path, capsys):
  """A ledger without a policy exits 2 unless `--policy` is given."""
  ledger = tmp_path / 'l.json'
  ledger.write_text('{"events": []}')
  assert main(['run', str(ledger)]) == 2
  pol = tmp_path / 'p.json'
  pol.write_text(
    '{"cost_method": "fifo", "lot_scope": "global", "functional_currency": "EUR"}'
  )
  assert main(['run', str(ledger), '--policy', str(pol)]) == 0


def test_usage_error(capsys):
  """Bad arguments exit 2; `--help` exits 0."""
  assert main(['run']) == 2
  assert main(['bogus']) == 2
  assert main(['--help']) == 0


def test_strict_stops_on_price_gap(tmp_path, capsys):
  """`--strict` stops at the first gap with exit 1 and the gap on stderr."""
  ledger = tmp_path / 'l.json'
  ledger.write_text(
    '{"policy": {"cost_method": "fifo", "lot_scope": "global", "functional_currency": "EUR"},'
    ' "events": [{"id": "y", "time": "2026-01-01T00:00:00Z", "legs": [{"asset": "DOGE", "quantity": "1", "compartment": "A", "tag": "income"}]}]}'
  )
  assert main(['run', str(ledger), '--strict']) == 1
  assert 'no price for DOGE/EUR' in capsys.readouterr().err
  assert main(['run', str(ledger), '--json']) == 0
  assert not json.loads(capsys.readouterr().out)['complete']


def test_validate(tmp_path, capsys):
  """`validate` exits 0 on a well-formed ledger and reports problems in JSON otherwise."""
  assert main(['validate', PERP]) == 0
  assert capsys.readouterr().out.startswith('valid')
  bad = tmp_path / 'bad.json'
  bad.write_text(
    '{"events": [{"id": "x", "time": "2026-01-01T00:00:00", "legs": [{"asset": "BTC", "quantity": "1", "compartment": "A", "tag": "expense"}]}],'
    ' "links": [{"src": "x", "dst": "nope"}]}'
  )
  assert main(['--json', 'validate', str(bad)]) == 0
  out = json.loads(capsys.readouterr().out)
  assert [x['code'] for x in out] == ['invalid_event', 'unknown_event']
  assert 'naive timestamp' in out[0]['message']


def test_value(capsys):
  """`value` prices the open lots at `--at`; gaps make it exit 1."""
  assert main(['value', BRIDGE, '--at', '2026-03-01T00:00:00Z', '--json']) == 0
  out = json.loads(capsys.readouterr().out)
  assets = {p['asset']: p for p in out['positions']}
  assert assets['HYPE']['value'] == '1980.00'
  assert main(['value', SPOT, '--at', '2026-03-01T00:00:00Z']) == 0
  assert '[price_gap]' in capsys.readouterr().out
  assert main(['value', SPOT, '--at', 'yesterday']) == 2
  assert main(['value', SPOT, '--at', '2026-03-01T00:00:00']) == 2


def test_schema_verbs(tmp_path, capsys):
  """`schema` prints one schema, writes all three, and checks a directory."""
  assert main(['schema', 'result']) == 0
  assert json.loads(capsys.readouterr().out)['title'] == 'Accounting result'
  assert main(['schema', '--out', str(tmp_path)]) == 0
  assert sorted(p.name for p in tmp_path.iterdir()) == [
    'ledger.schema.json',
    'result.schema.json',
    'valuation.schema.json',
  ]
  assert main(['schema', '--check', str(tmp_path)]) == 0
  (tmp_path / 'ledger.schema.json').write_text('{}')
  assert main(['schema', '--check', str(tmp_path)]) == 1
  assert 'stale' in capsys.readouterr().err


@pytest.mark.parametrize('name', ['perp', 'spot', 'bridge', 'pnl_settled', 'loan'])
def test_examples_validate(name):
  """Every example ledger is well formed."""
  assert main(['validate', str(EXAMPLES / f'{name}.json')]) == 0


LOAN = str(EXAMPLES / 'loan.json')


def test_loan_example(capsys):
  """The loan example books a borrow, an accrual, a partial repay and reports the open liability."""
  assert main(['run', LOAN, '--json']) == 0
  out = json.loads(capsys.readouterr().out)
  (owed,) = out['liabilities']
  assert (owed['quantity'], owed['cost'], owed['label']) == ('20', '18.00', 'aave-v3')
  assert sum(Decimal(r['pnl']) for r in out['realized']) == Decimal('248.00')
  assert main(['--json', 'value', LOAN, '--at', '2026-06-01T00:00:00Z']) == 0
  out = json.loads(capsys.readouterr().out)
  (owed,) = out['liabilities']
  assert (owed['value'], owed['unrealized']) == ('19.00', None)


def test_max_age_option(capsys):
  """`--max-age` overrides the ledger's; a stale table turns into gaps (exit 1); a bad duration is usage (exit 2)."""
  assert main(['run', LOAN, '--max-age', 'P1D', '--json']) == 0
  out = json.loads(capsys.readouterr().out)
  assert 'price_gap' in {x['code'] for x in out['exceptions']}
  assert main(['run', LOAN, '--max-age', '31536000']) == 0
  capsys.readouterr()
  assert main(['run', LOAN, '--max-age', 'soon']) == 2
  assert 'invalid duration' in capsys.readouterr().err
