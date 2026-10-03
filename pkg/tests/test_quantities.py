"""
Quantities are exact, with a 1e-18 net (policy 05 rule 20.3): splitting a lot
never loses precision, and a holding a crumb away from zero is treated as zero
and reported as information.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from litmus.accounting import run, FixedPricing
from litmus.accounting.engine import lots, totals
from litmus.accounting.model import (
  Event,
  Leg,
  Policy,
  Result,
  Rollover,
  RolloverInput,
  RolloverOutput,
)

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
POLICY = Policy(
  cost_method='fifo', lot_scope='global', functional_currency='EUR', minor_unit=2
)
PRICES = FixedPricing({('ETH', 'EUR'): '2500', ('aave-ethereum', 'EUR'): '2500'})
WALLET = 'evm:ethereum:0xabc'


def at(text: str) -> datetime:
  """A UTC instant from `YYYY-MM-DDTHH:MM:SS`."""
  return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)


def buy(name: str, when: datetime, eth: str, eur: str) -> Event:
  """ETH bought for euro."""
  return Event(
    name,
    when,
    (Leg('ETH', D(eth), WALLET, 'trade'), Leg('EUR', -D(eur), WALLET, 'trade')),
  )


def supply(name: str, when: datetime, eth: str, aeth: str) -> Event:
  """ETH supplied to Aave: a rollover into aETH, carrying the ETH's basis."""
  return Event(
    name,
    when,
    (
      Leg('ETH', -D(eth), WALLET, 'rollover'),
      Leg('aave-ethereum', D(aeth), WALLET, 'rollover', label='position_basis'),
    ),
    rollover=Rollover(inputs=(RolloverInput(0),), outputs=(RolloverOutput(1),)),
  )


def withdraw(name: str, when: datetime, aeth: str) -> Event:
  """aETH withdrawn from Aave as ETH."""
  return Event(
    name,
    when,
    (
      Leg('ETH', D(aeth), WALLET, 'trade'),
      Leg('aave-ethereum', -D(aeth), WALLET, 'trade', label='position_basis'),
    ),
  )


def accrue(name: str, when: datetime, aeth: str) -> Event:
  """aETH interest accrued (rebasing)."""
  return Event(
    name, when, (Leg('aave-ethereum', D(aeth), WALLET, 'income', label='yield'),)
  )


def codes(result: Result, code: str) -> list[str]:
  """The events of the result's exceptions with `code`."""
  return [x.event or '' for x in result.exceptions if x.code == code]


def held(result: Result, asset: str) -> D:
  """The exact quantity of `asset` in the result's open lots."""
  return sum((lot.quantity for lot in result.lots if lot.asset == asset), D(0))


def test_thirds_of_one_aeth_withdrawn_whole_and_supplied_again_are_booked():
  """1 aETH carried in thirds is withdrawn as 1 and supplied again: no residue, the new supply is booked."""
  # policy 05 rule 20.3: quantities are exact, so splits never lose precision
  events = [
    buy('b1', T0, '0.3', '100'),
    buy('b2', T0 + timedelta(hours=1), '0.3', '100'),
    buy('b3', T0 + timedelta(hours=2), '0.4', '100'),
    supply('s1', T0 + timedelta(days=1), '1', '1'),
    withdraw('w1', T0 + timedelta(days=2), '1'),
    supply('s2', T0 + timedelta(days=3), '0.5', '0.5'),
  ]
  r = run(events, policy=POLICY, pricing=PRICES)
  thirds = [s.quantity for record in r.rollovers[:1] for s in record.created]
  assert len(thirds) == 3 and sum(thirds, D(0)) == 1
  assert all(len(q.as_tuple().digits) == 28 for q in thirds)
  assert r.complete
  assert not codes(r, 'unbooked') and not codes(r, 'negative_position')
  assert not codes(r, 'quantity_residue')
  assert held(r, 'aave-ethereum') == D('0.5')


# The company's aETH on ethereum1, 2026-01-29 to 2026-09-18, as served: three
# supplies the 0.9 engine left unbooked ("output requires a non-short holding")
# behind a residue lot of -3E-29 from the first withdrawal.
AETH = [
  ('supply', '2026-01-29T09:36:59', '0.511097754921530793', '0.511097754921530792'),
  ('accrue', '2026-02-01T00:02:11', '0.000058334647796694', None),
  ('withdraw', '2026-02-09T01:13:11', '0.511407353491615171', None),
  ('accrue', '2026-02-09T01:13:11', '0.000251263922287685', None),
  ('supply', '2026-02-11T22:55:35', '0.57', '0.569999999999999999'),
  ('withdraw', '2026-02-23T08:06:11', '0.570449815379598146', None),
  ('accrue', '2026-02-23T08:06:11', '0.000449815379598147', None),
  ('supply', '2026-02-26T23:06:35', '0.58', '0.579999999999999999'),
  ('accrue', '2026-03-01T00:01:59', '0.000062332196529655', None),
  ('withdraw', '2026-03-31T21:57:59', '0.580919078891097347', None),
  ('accrue', '2026-03-31T21:57:59', '0.000856746694567693', None),
  ('supply', '2026-06-03T06:38:23', '1', '0.999999999999999999'),
  ('withdraw', '2026-06-07T20:25:47', '0.4', None),
  ('accrue', '2026-06-07T20:25:47', '0.00017555754189736', None),
  ('accrue', '2026-06-30T23:59:59', '0.000554871914159048', None),
  ('accrue', '2026-07-31T23:59:59', '0.000723467451126055', None),
  ('accrue', '2026-08-31T23:59:59', '0.000749633610958403', None),
  ('withdraw', '2026-09-18T19:26:23', '0.602627634600037873', None),
  ('accrue', '2026-09-18T19:26:23', '0.000424104081897008', None),
]


def test_the_company_aeth_sequence_books_every_supply():
  """The real aETH sequence: every supply is booked and aETH ends at the exact sum of its legs."""
  # policy 05 rule 20.3; the 0.9 engine left the supplies of 02-11, 02-26 and 06-03 unbooked
  events = [
    # ETH lots of very different sizes, so the first supply carves aETH lots
    # whose quantities need more than 28 digits to add up.
    buy('b1', at('2026-01-01T00:00:00'), '0.01', '15'),
    buy('b2', at('2026-01-02T00:00:00'), '0.02', '30'),
    buy('b3', at('2026-01-03T00:00:00'), '5', '9500'),
  ]
  for index, (kind, when, quantity, output) in enumerate(AETH):
    name = f'{kind}{index}'
    if kind == 'supply':
      assert output is not None
      events.append(supply(name, at(when), quantity, output))
    elif kind == 'withdraw':
      events.append(withdraw(name, at(when), quantity))
    else:
      events.append(accrue(name, at(when), quantity))
  r = run(events, policy=POLICY, pricing=PRICES)
  assert r.complete and not codes(r, 'unbooked')
  assert len(r.rollovers) == 4
  assert any(len(s.quantity.as_tuple().digits) == 28 for s in r.rollovers[0].created)
  # Each withdrawal before its same-second accrual runs short by that accrual
  # (an ordering matter, not a residue), and the accrual closes it exactly.
  short = {
    x.event: D(x.detail['position'])
    for x in r.exceptions
    if x.code == 'negative_position'
  }
  assert short == {
    'withdraw2': -D('0.000251263922287685'),
    'withdraw5': -D('0.000449815379598147'),
    'withdraw9': -D('0.000856746694567693'),
    'withdraw17': -D('0.000424104081897008'),
  }
  assert not codes(r, 'quantity_residue')
  legs = sum(
    (leg.quantity for e in events for leg in e.legs if leg.asset == 'aave-ethereum'),
    D(0),
  )
  assert held(r, 'aave-ethereum') == legs


def test_a_crumb_left_by_a_withdrawal_is_treated_as_zero_with_an_item():
  """Three receipts of a third make 0.999…9; withdrawing 1 leaves -1E-28, treated as zero and reported."""
  # policy 05 rule 20.3: the 1e-18 net, and its information item
  third = D(1) / 3
  assert third * 3 == D('0.9999999999999999999999999999')
  events = [
    buy('b1', T0, '1', '2500'),
    supply('s1', T0 + timedelta(hours=1), '1', str(third)),
    accrue('r1', T0 + timedelta(hours=2), str(third)),
    accrue('r2', T0 + timedelta(hours=3), str(third)),
    withdraw('w1', T0 + timedelta(days=1), '1'),
    supply('s2', T0 + timedelta(days=2), '0.5', '0.5'),
  ]
  r = run(events, policy=POLICY, pricing=PRICES)
  assert r.complete and not codes(r, 'unbooked') and not codes(r, 'negative_position')
  (item,) = [x for x in r.exceptions if x.code == 'quantity_residue']
  assert item.event == 'w1'
  assert item.detail == {
    'asset': 'aave-ethereum',
    'compartment': WALLET,
    'residue': '-1E-28',
  }
  assert held(r, 'aave-ethereum') == D('0.5')
  # The withdrawal realized the whole holding: all its basis left with it.
  (realized,) = [
    row for row in r.realized if row.event == 'w1' and row.asset == 'aave-ethereum'
  ]
  assert realized.quantity == -1


def test_a_crumb_left_over_goes_with_the_take_and_its_basis_too():
  """Taking a crumb less than held empties the key: the crumb's basis is released with the take."""
  # policy 05 rule 20.3
  book = lots.LotBook('fifo')
  key = (None, 'EURC')
  book.open(key, quantity=D('100'), cost=D('90'), time=T0, event='a')
  book.open(
    key,
    quantity=D('141.4760800000000000000000001'),
    cost=D('130'),
    time=T0 + timedelta(days=1),
    event='b',
  )
  taken = book.take(key, D('-241.47608'))
  assert book.lots[key] == [] and book.position(key) == 0
  assert sum((share for _, _, share in taken), D(0)) == D('220')
  assert sum((quantity for _, quantity, _ in taken), D(0)) == D('241.47608')
  assert book.residues == [totals.Residue(key, D('1E-25'))]


def test_one_wei_is_a_holding_not_a_crumb():
  """The net is strictly below 1e-18: one wei stays a holding."""
  # policy 05 rule 20.3
  book = lots.LotBook('fifo')
  key = (None, 'ETH')
  book.open(key, quantity=D('1.000000000000000001'), cost=D(1), time=T0, event='a')
  book.take(key, D('-1'))
  assert book.position(key) == D('1E-18') and book.residues == []
