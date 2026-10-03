"""Atomic groups (policy 05 rule 6.3): a group's events are applied in order and checked for shortness only after the last of them."""

from decimal import Decimal as D
import json
from litmus.accounting import run, FixedPricing, codec
from litmus.accounting.model import (
  Event,
  Leg,
  Result,
  Rollover,
  RolloverInput,
  RolloverOutput,
)
from tests.conftest import leg, event, policy, t

PRICES = FixedPricing(
  {('USDC', 'EUR'): '0.9', ('USDT', 'EUR'): '0.9', ('aUSDC', 'EUR'): '0.9'}
)
POOL = 'pool'


def codes(result: Result) -> list[str]:
  """Exception codes of a result."""
  return [x.code for x in result.exceptions]


def debt_swap() -> list[Event]:
  """
  The ethereum1 Aave debt swap of 2026-05-25 (one transaction), in the order
  its records are served: a USDT loan taken earlier, then the repayment of
  8,002.40 USDT, the borrow of 7,999.078215 USDC and the CoW swap of
  7,998.015652 USDC for the USDT, which leaves 1.062563 USDC.
  """
  return [
    event(
      'repay',
      2,
      leg('USDT', '-8002.4', 'wallet', 'repay', liability=POOL),
      leg('USDT', '0.000001', POOL, 'borrow', label='interest'),
    ),
    event('borrow', 2, leg('USDC', '7999.078215', 'wallet', 'borrow', liability=POOL)),
    event(
      'swap',
      2,
      leg('USDC', '-7998.015652', 'wallet'),
      leg('USDT', '8002.4', 'wallet'),
    ),
  ]


def loan() -> Event:
  """The USDT loan the debt swap refinances, spent the day it was taken."""
  return event(
    'loan',
    1,
    leg('USDT', '8002.399999', 'wallet', 'borrow', liability=POOL),
    leg('USDT', '-8002.399999', 'wallet', 'transfer'),
  )


def test_a_debt_swap_in_one_group_is_never_short(method, scope):
  """Policy 05 rule 6.3: the repayment is served before the swap that buys its USDT; as one group nothing goes short, and the wallet keeps the 1.062563 USDC left over."""
  r = run(
    [loan(), debt_swap()],
    policy=policy(method, scope, cash=('USDC', 'USDT')),
    pricing=PRICES,
  )
  assert 'negative_position' not in codes(r)
  assert 'negative_liability' not in codes(r)
  assert r.complete
  held = {(l.asset, l.compartment): l.quantity for l in r.lots}
  wallet = 'wallet' if scope == 'compartment' else None
  assert held == {('USDC', wallet): D('1.062563')}
  (owed,) = r.liabilities
  assert (owed.asset, owed.quantity) == ('USDC', D('7999.078215'))


def test_the_same_events_one_per_group_are_short():
  """Policy 05 rule 6.3: a flat list is one event per group, so the repayment leaves USDT short until the swap, as before."""
  r = run(
    [loan(), *debt_swap()],
    policy=policy(cash=('USDC', 'USDT')),
    pricing=PRICES,
  )
  assert [
    (x.code, x.event) for x in r.exceptions if x.code != 'unmatched_transfer'
  ] == [('negative_position', 'repay')]


def test_a_withdrawal_before_its_accrual_is_not_short(method, scope):
  """Policy 05 rule 6.3: an Aave withdrawal served before its same-second accrual takes more aUSDC than is held; in one group the accrual covers it."""
  supply = event(
    'supply', 1, leg('USDC', '-1000', 'wallet'), leg('aUSDC', '1000', 'wallet')
  )
  buy = event(
    'buy', 1, leg('USDC', '1000', 'wallet'), leg('EUR', '-900', 'wallet'), hour=1
  )
  withdraw = event(
    'withdraw', 2, leg('aUSDC', '-1004', 'wallet'), leg('USDC', '1004', 'wallet')
  )
  accrual = event('accrual', 2, leg('aUSDC', '4', 'wallet', 'income', label='yield'))
  grouped = run(
    [buy, supply, [withdraw, accrual]], policy=policy(method, scope), pricing=PRICES
  )
  assert codes(grouped) == []
  assert [(l.asset, l.quantity) for l in grouped.lots] == [('USDC', D('1004'))]
  flat = run(
    [buy, supply, withdraw, accrual], policy=policy(method, scope), pricing=PRICES
  )
  assert codes(flat) == ['negative_position']


def test_a_short_across_two_groups_is_reported():
  """Policy 05 rule 6.3: a holding still short after its group is reported, once, on the group's last event that touched it."""
  sell = event('sell', 1, leg('USDT', '-10', 'wallet'), leg('EUR', '9', 'wallet'))
  more = event('more', 1, leg('USDT', '-5', 'wallet'), leg('EUR', '4.5', 'wallet'))
  cover = event('cover', 2, leg('USDT', '15', 'wallet'), leg('EUR', '-13.5', 'wallet'))
  r = run([[sell, more], [cover]], policy=policy(), pricing=PRICES)
  (short,) = r.exceptions
  assert (short.code, short.event, short.detail['position']) == (
    'negative_position',
    'more',
    '-15',
  )


def test_a_repayment_before_its_accrual_is_not_negative():
  """Policy 05 rule 6.3: a repayment of principal and interest served before the interest accrues leaves the liability negative only within its group."""
  borrow = event('borrow', 1, leg('USDC', '100', 'wallet', 'borrow', liability=POOL))
  repay = event('repay', 2, leg('USDC', '-101', 'wallet', 'repay', liability=POOL))
  accrue = event('accrue', 2, leg('USDC', '1', POOL, 'borrow', label='interest'))
  buy = event(
    'buy', 1, leg('USDC', '1', 'wallet'), leg('EUR', '-0.9', 'wallet'), hour=1
  )
  grouped = run([buy, borrow, [repay, accrue]], policy=policy(), pricing=PRICES)
  assert codes(grouped) == [] and grouped.liabilities == ()
  flat = run([buy, borrow, repay, accrue], policy=policy(), pricing=PRICES)
  assert codes(flat) == ['negative_liability']


def supply(id: str, quantity: str, *, hour: int = 12) -> Event:
  """Supply USDC for aUSDC, carrying basis (a rollover)."""
  return Event(
    id,
    t(2, hour),
    (
      Leg('USDC', D('-' + quantity), 'wallet', 'rollover'),
      Leg('aUSDC', D(quantity), 'wallet', 'rollover'),
    ),
    rollover=Rollover((RolloverInput(0),), (RolloverOutput(1),)),
  )


def test_a_supply_into_a_holding_the_group_made_short_is_booked(method, scope):
  """Policy 05 rule 6.3: a rollover output into a holding short only within its group is booked; the short is delivered from it, and the journal balances."""
  buy = event('buy', 1, leg('USDC', '1100', 'wallet'), leg('EUR', '-990', 'wallet'))
  enter = event(
    'enter', 1, leg('USDC', '-1000', 'wallet'), leg('aUSDC', '1000', 'wallet'), hour=13
  )
  withdraw = event(
    'withdraw', 2, leg('aUSDC', '-1004', 'wallet'), leg('USDC', '1004', 'wallet')
  )
  accrual = event('accrual', 2, leg('aUSDC', '4', 'wallet', 'income', label='yield'))
  r = run(
    [buy, enter, [withdraw, supply('supply', '100'), accrual]],
    policy=policy(method, scope),
    pricing=PRICES,
  )
  assert codes(r) == [] and r.complete
  held: dict[str, D] = {}
  for lot in r.lots:
    held[lot.asset] = held.get(lot.asset, D(0)) + lot.quantity
  assert held == {'USDC': D('1004'), 'aUSDC': D('100')}
  assert sum((line.debit - line.credit for line in r.journal), D(0)) == 0


def test_a_supply_into_a_holding_short_before_the_group_is_refused():
  """Policy 05 rule 6.3: a holding short across two instants still refuses a rollover output, as before."""
  sell = event('sell', 1, leg('aUSDC', '-10', 'wallet'), leg('EUR', '9', 'wallet'))
  buy = event(
    'buy', 1, leg('USDC', '100', 'wallet'), leg('EUR', '-90', 'wallet'), hour=13
  )
  r = run([sell, buy, [supply('supply', '100')]], policy=policy(), pricing=PRICES)
  assert codes(r) == ['negative_position', 'unbooked', 'unbooked']
  assert 'non-short holding' in r.exceptions[1].message


def test_the_journal_keeps_the_group_order():
  """Policy 05 rule 6.3: a group's events are applied, and journalled, in the order given, not sorted by id."""
  r = run([loan(), debt_swap()], policy=policy(cash=('USDC', 'USDT')), pricing=PRICES)
  order = list(dict.fromkeys(line.event for line in r.journal))
  assert order == ['loan', 'repay', 'borrow', 'swap']


def test_a_group_spanning_two_times_is_booked_as_two():
  """A group's events at different times are booked as one group per time, so a short between them is reported."""
  sell = event('sell', 1, leg('USDT', '-10', 'wallet'), leg('EUR', '9', 'wallet'))
  cover = event('cover', 2, leg('USDT', '10', 'wallet'), leg('EUR', '-9', 'wallet'))
  r = run([[sell, cover]], policy=policy(), pricing=PRICES)
  assert [(x.code, x.event) for x in r.exceptions] == [('negative_position', 'sell')]


def test_a_ledger_reads_groups_and_bare_events():
  """The JSON ledger's `events` holds groups (arrays) and bare events (groups of one)."""
  doc = {
    'events': [
      [
        {
          'id': 'withdraw',
          'time': '2026-01-02T12:00:00Z',
          'legs': [
            {
              'asset': 'aUSDC',
              'quantity': '-4',
              'compartment': 'wallet',
              'tag': 'trade',
            },
            {'asset': 'USDC', 'quantity': '4', 'compartment': 'wallet', 'tag': 'trade'},
          ],
        },
        {
          'id': 'accrual',
          'time': '2026-01-02T12:00:00Z',
          'legs': [
            {
              'asset': 'aUSDC',
              'quantity': '4',
              'compartment': 'wallet',
              'tag': 'income',
            },
          ],
        },
      ],
      {
        'id': 'later',
        'time': '2026-01-03T12:00:00Z',
        'legs': [
          {'asset': 'USDC', 'quantity': '-4', 'compartment': 'wallet', 'tag': 'trade'},
          {'asset': 'EUR', 'quantity': '3.6', 'compartment': 'wallet', 'tag': 'trade'},
        ],
      },
    ]
  }
  ledger = codec.parse_ledger(json.dumps(doc))
  assert [type(item).__name__ for item in ledger.events] == ['tuple', 'Event']
  r = run(ledger.events, policy=policy(), pricing=PRICES)
  assert codes(r) == [] and r.lots == ()
