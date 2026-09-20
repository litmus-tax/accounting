"""`TablePricing`: latest at or before, and `max_age`."""

from datetime import timedelta
from decimal import Decimal as D
from litmus.accounting import run, TablePricing
from litmus.accounting.model import PriceRecord
from tests.conftest import leg, event, policy, t


def table(*days: int) -> list[PriceRecord]:
  """One BTC/EUR record per day, priced at the day number."""
  return [
    PriceRecord(asset='BTC', quote='EUR', time=t(d, 0), source='market', price=D(d))
    for d in days
  ]


def test_latest_at_or_before():
  """Without `max_age` the latest earlier record carries forward without bound."""
  p = TablePricing(table(1, 5))
  assert p.price('BTC', 'EUR', t(1, 0), source='market') == D(1)
  assert p.price('BTC', 'EUR', t(4), source='market') == D(1)
  assert p.price('BTC', 'EUR', t(31), source='market') == D(5)
  assert p.price('BTC', 'EUR', t(1, 0) - timedelta(seconds=1), source='market') is None


def test_max_age_turns_stale_prices_into_gaps():
  """A record older than `max_age` relative to the requested time is a gap, not a carry-forward."""
  p = TablePricing(table(1, 5), max_age=timedelta(days=1))
  assert p.price('BTC', 'EUR', t(2, 0), source='market') == D(1)
  assert p.price('BTC', 'EUR', t(2, 1), source='market') is None
  assert p.price('BTC', 'EUR', t(5, 12), source='market') == D(5)
  assert p.price('BTC', 'EUR', t(7), source='market') is None


def test_max_age_gap_is_recorded_by_the_engine():
  """Through `run` a stale table produces `price_gap` plus `unbooked`, same as any gap."""
  r = run(
    [event('y', 9, leg('BTC', '1', tag='income'))],
    policy=policy(),
    pricing=TablePricing(table(1), max_age=timedelta(days=2)),
  )
  assert [x.code for x in r.exceptions] == ['price_gap', 'unbooked']
  assert r.prices[0].price is None
