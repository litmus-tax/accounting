"""
Policy 05's worked examples (its guide and positions pages) as engine fixtures.

Each file in `fixtures/policy05/` holds one example: the ledger (events, links,
policy, prices) and one or more cases. A case may override policy fields, names
the rules it checks, and gives the figures the example states, rounded as shown.
A case with `as_of` books the events up to it, as a revision's cut-off, and
checks the open-lot rows there (policy 05 term 14). A case the engine cannot
reproduce yet carries `xfail` with the gap it waits for
(policy 05 rule 42.3); the mark is strict, so closing the gap flips the test.
"""

import json
from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from pathlib import Path
import pydantic
import pytest
from typing_extensions import Any
from litmus.accounting import run, codec
from litmus.accounting.model import Result
from litmus.accounting.pricing import TablePricing

FIXTURES = Path(__file__).parent / 'fixtures' / 'policy05'

Row = dict[str, Any]


def cases() -> list[Any]:
  """One pytest param per (example, case), strictly xfailed where the case says so."""
  out: list[Any] = []
  for path in sorted(FIXTURES.glob('*.json')):
    document = json.loads(path.read_text())
    for case in document['cases']:
      marks = (
        [pytest.mark.xfail(reason=case['xfail'], strict=True)]
        if 'xfail' in case
        else []
      )
      out.append(
        pytest.param(document, case, id=f'{path.stem}/{case["name"]}', marks=marks)
      )
  return out


def number(value: Any) -> Any:
  """Decimal for numeric strings so `1000` equals `1000.00`; anything else unchanged."""
  if isinstance(value, str):
    try:
      return Decimal(value)
    except ArithmeticError:
      return value
  return value


def project(rows: list[Row], expected: list[Row]) -> list[Row]:
  """Each actual row reduced to the keys its expected counterpart names."""
  return [
    {key: number(row.get(key)) for key in want}
    for row, want in zip(rows, expected, strict=False)
  ]


def normalized(expected: list[Row]) -> list[Row]:
  """Expected rows with numeric strings as decimals."""
  return [{key: number(value) for key, value in row.items()} for row in expected]


def holdings(result: Row) -> list[Row]:
  """Open lots summed per (compartment, asset): quantity and rounded cost."""
  totals: dict[tuple[str, str], Row] = {}
  for lot in result['lots']:
    key = (str(lot['compartment']), lot['asset'])
    row = totals.setdefault(
      key,
      {
        'compartment': lot['compartment'],
        'asset': lot['asset'],
        'quantity': Decimal(0),
        'cost': Decimal(0),
      },
    )
    row['quantity'] += Decimal(lot['quantity'])
    row['cost'] += Decimal(lot['cost'])
  return [totals[key] for key in sorted(totals)]


def totals(result: Result) -> dict[str, Decimal]:
  """Realized P&L, income and expenses summed from the rounded rows (rule 20.2)."""
  return {
    'realized': sum((r.pnl for r in result.realized), Decimal(0)),
    'income': sum((f.value for f in result.flows if f.kind == 'income'), Decimal(0)),
    'expenses': sum((f.value for f in result.flows if f.kind == 'expense'), Decimal(0)),
  }


def book(document: Row, case: Row) -> Result:
  """Run the example's ledger under the case's policy, up to the case's `as_of` when it gives one (a books cut-off: the open-lot rows are those of that instant, policy 05 term 14)."""
  ledger = codec.parse_ledger(json.dumps(document['ledger']))
  assert ledger.policy is not None
  policy = pydantic.TypeAdapter(type(ledger.policy)).validate_python(
    {**document['ledger']['policy'], **case.get('policy', {})}
  )
  ledger = replace(ledger, policy=policy)
  events = ledger.events
  if 'as_of' in case:
    cutoff = datetime.fromisoformat(case['as_of'].replace('Z', '+00:00'))
    events = tuple(
      item
      for item in events
      if all(e.time <= cutoff for e in (item if isinstance(item, tuple) else (item,)))
    )
  return run(
    events,
    links=ledger.links,
    policy=policy,
    pricing=TablePricing(ledger.prices),
  )


@pytest.mark.parametrize(('document', 'case'), cases())
def test_worked_example(document: Row, case: Row):
  """The engine reproduces the example's figures, as rounded on the policy page."""
  # policy 05 rule 42; the rules each case checks are listed in its `rules`.
  assert case['rules'], 'every case names the rules it checks (rule 42.2)'
  result = book(document, case)
  data: Row = json.loads(codec.dump_result(result))
  expected: Row = case['expected']
  if 'complete' in expected:
    assert result.complete == expected['complete'], result.exceptions
  for key in ('realized', 'flows', 'moves', 'liabilities', 'lots', 'positions'):
    if key in expected:
      assert len(data[key]) == len(expected[key]), data[key]
      assert project(data[key], expected[key]) == normalized(expected[key])
  if 'holdings' in expected:
    actual = holdings(data)
    assert len(actual) == len(expected['holdings']), actual
    assert project(actual, expected['holdings']) == normalized(expected['holdings'])
  for want in expected.get('balances', []):
    assert any(
      project([row], [want]) == normalized([want]) for row in data['balances']
    ), want
  for key, want in expected.get('totals', {}).items():
    assert totals(result)[key] == Decimal(want), key
  for want in expected.get('open_rows', []):
    assert any(
      project([row], [want]) == normalized([want]) for row in data['open_rows']
    ), (want, data['open_rows'])
  if 'journal' in expected:
    events = {line['event'] for line in expected['journal']}
    actual = [line for line in data['journal'] if line['event'] in events]
    assert sorted(map(journal_key, actual)) == sorted(
      map(journal_key, expected['journal'])
    )


def journal_key(line: Row) -> tuple[Any, ...]:
  """A journal line as a comparable tuple, amounts as decimals."""
  return (
    line['event'],
    line['account'],
    line['compartment'],
    line['asset'],
    line.get('label'),
    Decimal(line['debit']),
    Decimal(line['credit']),
  )
