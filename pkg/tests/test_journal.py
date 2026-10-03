"""The journal: balanced double entry per event that reconciles with lots and liabilities."""

import json
from dataclasses import replace
from datetime import datetime
from decimal import Decimal as D
from pathlib import Path
import pytest
from litmus.accounting import run, codec, FixedPricing
from litmus.accounting.model import Event, Result
from litmus.accounting.pricing import TablePricing
from litmus.accounting.engine.journal import Journal
from tests.conftest import flat, leg, event, policy, t

Key = tuple[str | None, str | None]
"""A journal or liability key: `(compartment, asset)`."""
ROOT = Path(__file__).resolve().parents[2]
LEDGERS = sorted((ROOT / 'examples').glob('*.json')) + sorted(
  (Path(__file__).parent / 'fixtures' / 'policy05').glob('*.json')
)


def ledger_of(path: Path):
  """An example ledger or a policy 05 fixture's ledger, as parsed."""
  document = json.loads(path.read_text())
  return codec.parse_ledger(json.dumps(document.get('ledger', document)))


def per_event(result: Result) -> dict[str, D]:
  """Debits minus credits per event."""
  out: dict[str, D] = {}
  for line in result.journal:
    out[line.event] = out.get(line.event, D(0)) + line.debit - line.credit
  return out


@pytest.mark.parametrize('path', LEDGERS, ids=lambda p: p.stem)
@pytest.mark.parametrize('minor_unit', [None, 2])
def test_every_event_balances(path: Path, minor_unit: int | None):
  """Debits equal credits per event in every example, before and after rounding."""
  # policy 05 rule 29.2
  ledger = ledger_of(path)
  assert ledger.policy is not None
  p = replace(ledger.policy, minor_unit=minor_unit)
  r = run(
    ledger.events, links=ledger.links, policy=p, pricing=TablePricing(ledger.prices)
  )
  assert 'unbalanced' not in {x.code for x in r.exceptions}
  tolerance = D(0) if minor_unit is not None else D('1e-18')
  assert all(abs(v) <= tolerance for v in per_event(r).values()), per_event(r)
  assert all((line.debit == 0) != (line.credit == 0) for line in r.journal)


@pytest.mark.parametrize('path', LEDGERS, ids=lambda p: p.stem)
def test_the_journal_reconciles_with_lots_and_liabilities(path: Path):
  """Holding accounts sum to the open lots' cost and liability accounts to what is owed, per key."""
  # policy 05 rule 29.1
  ledger = ledger_of(path)
  assert ledger.policy is not None
  p = replace(ledger.policy, minor_unit=None)
  r = run(
    ledger.events, links=ledger.links, policy=p, pricing=TablePricing(ledger.prices)
  )
  holdings: dict[tuple[str | None, str | None], D] = {}
  owed: dict[tuple[str | None, str | None], D] = {}
  for line in r.journal:
    target = {'holding': holdings, 'liability': owed}.get(line.account)
    if target is not None:
      key = (line.compartment, line.asset)
      target[key] = target.get(key, D(0)) + line.debit - line.credit
  lots: dict[tuple[str | None, str | None], D] = {}
  for lot in r.lots:
    lots[(lot.compartment, lot.asset)] = (
      lots.get((lot.compartment, lot.asset), D(0)) + lot.cost
    )
  fc = p.functional_currency
  for key, amount in holdings.items():
    if key[1] != fc:
      assert abs(amount - lots.get(key, D(0))) < D('1e-18'), key
  assert all(key in holdings for key, cost in lots.items() if cost)
  for liability in r.liabilities:
    assert abs(owed[(liability.compartment, liability.asset)] + liability.cost) < D(
      '1e-18'
    )


def test_accrued_interest_no_longer_disappears():
  """Probe 2: borrow 100, accrue 5, repay 105; the journal shows the 5 as an interest expense."""
  # policy 05 rules 9.3 and 29 (gap 11, probe 2)
  events = [
    event('hold', 1, leg('USDC', '50'), leg('EUR', '-50')),
    event('borrow', 2, leg('USDC', '100', tag='borrow', liability='debt')),
    event('accrue', 3, leg('USDC', '5', 'debt', tag='borrow', label='interest')),
    event('repay', 4, leg('USDC', '-105', tag='repay', liability='debt')),
  ]
  r = run(
    events, policy=policy(minor_unit=2), pricing=FixedPricing({('USDC', 'EUR'): '1'})
  )
  expenses = [x for x in r.journal if x.account == 'expense']
  assert [(x.label, x.debit) for x in expenses] == [('interest', D('5.00'))]
  assert sum(l.quantity for l in r.lots) == D('45') and r.liabilities == ()


def test_a_rounding_residue_gets_a_rounding_line():
  """Three equal acquisitions for 100 cost 33.33 each; the cent left over is a `rounding` line."""
  # policy 05 rules 20 and 29.2
  events = [
    event(
      'buy',
      1,
      leg('A', '1'),
      leg('B', '1'),
      leg('C', '1'),
      leg('EUR', '-100'),
    )
  ]
  prices = FixedPricing({('A', 'EUR'): '1', ('B', 'EUR'): '1', ('C', 'EUR'): '1'})
  r = run(events, policy=policy(minor_unit=2), pricing=prices)
  assert r.complete
  (rounding,) = [x for x in r.journal if x.account == 'rounding']
  assert (rounding.debit, rounding.credit) == (D('0.01'), D('0'))
  assert per_event(r) == {'buy': D(0)}


def test_an_unbalanced_event_is_an_exception():
  """Lines that do not balance before rounding raise `unbalanced` (checked on the journal itself)."""
  # policy 05 rule 29.2
  journal = Journal()
  e = Event('e', t(1), (leg('USDC', '1'),))
  journal.add(e, 'holding', D('10'), asset='USDC')
  journal.add(e, 'income', D('-9'), label='yield')
  r = run([], policy=policy(), pricing=FixedPricing({}))
  lines, problems = journal.finish(r, 2)
  assert [(x.code, x.event, x.detail) for x in problems] == [
    ('unbalanced', 'e', {'difference': '1'})
  ]
  assert [line.account for line in lines] == ['holding', 'income']


def test_a_linked_transfer_is_one_entry_on_the_withdrawal():
  """Both sides of a move, and the withdrawal's fee, are one entry on the source event."""
  # policy 05 rules 13.1 and 29.1; guide §3 step 3
  events = [
    event('buy', 1, leg('ETH', '1'), leg('EUR', '-3000')),
    event(
      'out',
      2,
      leg('ETH', '-0.999', tag='transfer'),
      leg('ETH', '-0.001', tag='expense', fee=True, label='transfer_fee'),
    ),
    event('in', 3, leg('ETH', '0.999', 'B', tag='transfer')),
  ]
  from tests.conftest import link

  r = run(
    events,
    links=[link('out', 'in')],
    policy=policy('fifo', 'compartment', minor_unit=2),
    pricing=FixedPricing({('ETH', 'EUR'): '3200'}),
  )
  assert {x.event for x in r.journal} == {'buy', 'out'}
  assert per_event(r) == {'buy': D(0), 'out': D(0)}


def liability_balances(result: Result, until: datetime | None = None) -> dict[Key, D]:
  """Debits minus credits on the `liability` account per `(compartment, asset)`, up to `until`."""
  out: dict[Key, D] = {}
  for line in result.journal:
    if line.account == 'liability' and (until is None or line.time <= until):
      key = (line.compartment, line.asset)
      out[key] = out.get(key, D(0)) + line.debit - line.credit
  return out


def assert_liability_matches_rows_at_every_point(result: Result):
  """At every series point the journal's liability account is minus the liabilities' carrying value, per key."""
  assert result.series
  for point in result.series:
    owed: dict[Key, D] = {}
    for row in point.liabilities:
      key = (row.compartment, row.asset)
      owed[key] = owed.get(key, D(0)) - row.cost
    journal = liability_balances(result, point.at)
    for key in owed.keys() | journal.keys():
      assert abs(journal.get(key, D(0)) - owed.get(key, D(0))) < D('1e-18'), (
        point.at,
        key,
        journal.get(key),
        owed.get(key),
      )


def loan(*, accrue_first: bool) -> list[Event]:
  """Borrow 100 USDC, accrue 5 of interest, repay 105; the accrual before or after the repayment at one instant."""
  accrue = event('accrue', 3, leg('USDC', '5', 'debt', tag='borrow', label='interest'))
  repay = event('repay', 3, leg('USDC', '-105', tag='repay', liability='debt'))
  return [
    event('hold', 1, leg('USDC', '50'), leg('EUR', '-50')),
    event('borrow', 2, leg('USDC', '100', tag='borrow', liability='debt')),
    *((accrue, repay) if accrue_first else (repay, accrue)),
  ]


def test_rule_9_3_1_accrued_interest_is_credited_to_the_liability():
  """Borrow 100, accrue 5, repay 105: the journal's liability goes 0 → −100 → −105 → 0 and the expense is 5, never a credit to a holding."""
  # policy 05 rule 7 row 10, rules 9.3.1 and 29.1
  r = run(
    loan(accrue_first=True),
    policy=policy(minor_unit=2),
    pricing=FixedPricing({('USDC', 'EUR'): '1'}),
  )
  running = D(0)
  path = [running]
  for name in ('borrow', 'accrue', 'repay'):
    lines = [x for x in r.journal if x.event == name]
    running += sum(
      (x.debit - x.credit for x in lines if x.account == 'liability'), D(0)
    )
    path.append(running)
  assert path == [D(0), D(-100), D(-105), D(0)]
  accrual = {
    (x.account, x.label, x.debit, x.credit) for x in r.journal if x.event == 'accrue'
  }
  assert accrual == {
    ('liability', None, D(0), D('5.00')),
    ('expense', 'interest', D('5.00'), D(0)),
  }
  assert [(f.kind, f.label, f.value) for f in r.flows] == [
    ('expense', 'interest', D('5.00'))
  ]
  assert r.liabilities == () and 'negative_liability' not in {
    x.code for x in r.exceptions
  }


@pytest.mark.parametrize(
  'accrue_first', [True, False], ids=['accrued', 'accrued_after']
)
def test_rule_29_journal_liability_equals_liability_rows_at_every_point(
  accrue_first: bool,
):
  """The journal's liability balance equals the liabilities rows at every point, including an accrual booked after the repayment it preceded (evm closes it after its block)."""
  # policy 05 rules 9.3.1, 29.1 and 39
  events = loan(accrue_first=accrue_first)
  r = run(
    events,
    policy=policy(),
    pricing=FixedPricing({('USDC', 'EUR'): '1'}),
    grid=sorted({e.time for e in events}),
  )
  assert_liability_matches_rows_at_every_point(r)
  assert liability_balances(r) == {('debt', 'USDC'): D(0)}
  assert sum(f.value for f in r.flows if f.label == 'interest') == D(5)


@pytest.mark.parametrize('path', LEDGERS, ids=lambda p: p.stem)
def test_rule_29_every_example_journal_liability_equals_liability_rows_at_every_point(
  path: Path,
):
  """In every example and fixture, at every event instant, the journal's liability account matches the liabilities rows."""
  # policy 05 rules 29.1 and 39
  ledger = ledger_of(path)
  assert ledger.policy is not None
  r = run(
    ledger.events,
    links=ledger.links,
    policy=replace(ledger.policy, minor_unit=None),
    pricing=TablePricing(ledger.prices),
    grid=sorted({e.time for e in flat(ledger)}),
  )
  assert_liability_matches_rows_at_every_point(r)
