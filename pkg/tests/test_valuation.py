"""Period-end valuation over open lots."""

from decimal import Decimal as D
import pytest
from litmus.accounting import run, value, FixedPricing, PriceGap
from tests.conftest import leg, event, policy, t


def lots():
  """Two open lots: 1 BTC at 10000 in A and 2 ETH at 2000 in B."""
  events = [
    event('b', 1, leg('BTC', '1', 'A'), leg('EUR', '-10000', 'A')),
    event('e', 2, leg('ETH', '2', 'B'), leg('EUR', '-2000', 'B')),
  ]
  return run(
    events, policy=policy('fifo', 'compartment'), pricing=FixedPricing({})
  ).lots


def test_value_prices_each_lot():
  """Every lot gets a value and an unrealized figure; every price asked is recorded."""
  v = value(
    lots(),
    at=t(31),
    policy=policy('fifo', 'compartment'),
    pricing=FixedPricing({('BTC', 'EUR'): '30000', ('ETH', 'EUR'): '1500'}),
  )
  assert v.complete and v.exceptions == ()
  assert [(p.asset, p.compartment, p.value, p.unrealized) for p in v.positions] == [
    ('BTC', 'A', D('30000'), D('20000')),
    ('ETH', 'B', D('3000'), D('1000')),
  ]
  assert [(p.asset, p.time) for p in v.prices] == [('BTC', t(31)), ('ETH', t(31))]


def test_value_gap_is_reported():
  """A missing price yields `None` values, a `price_gap` exception and `complete: false`."""
  v = value(
    lots(),
    at=t(31),
    policy=policy('fifo', 'compartment'),
    pricing=FixedPricing({('BTC', 'EUR'): '30000'}),
  )
  assert not v.complete
  assert [x.code for x in v.exceptions] == ['price_gap']
  eth = v.positions[1]
  assert (eth.value, eth.unrealized) == (None, None)
  assert v.prices[1].price is None
  with pytest.raises(PriceGap):
    value(
      lots(),
      at=t(31),
      policy=policy('fifo', 'compartment'),
      pricing=FixedPricing({}),
      strict=True,
    )


def test_value_applies_price_time_and_rounding():
  """`daily_close` moves the price time to end of day; `minor_unit` rounds value and unrealized."""
  p = policy('fifo', 'compartment', price_time='daily_close', minor_unit=2)
  v = value(
    lots(),
    at=t(31, 3),
    policy=p,
    pricing=FixedPricing({('BTC', 'EUR'): '30000.005', ('ETH', 'EUR'): '1500'}),
  )
  assert v.prices[0].time.hour == 23
  btc = v.positions[0]
  assert (btc.value, btc.unrealized) == (D('30000.01'), D('20000.01'))
