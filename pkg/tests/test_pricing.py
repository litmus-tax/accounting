"""`TablePricing`: the latest price at or before, on the same UTC day only."""

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


def test_latest_at_or_before_on_its_day():
  """A row prices its own UTC day from its time on; nothing is carried to a later day (policy 05 rule 1.5)."""
  p = TablePricing(table(1, 5))
  assert p.price('BTC', 'EUR', t(1, 0), source='market') == D(1)
  assert p.price('BTC', 'EUR', t(1, 23), source='market') == D(1)
  assert p.price('BTC', 'EUR', t(5, 12), source='market') == D(5)
  assert p.price('BTC', 'EUR', t(4), source='market') is None
  assert p.price('BTC', 'EUR', t(31), source='market') is None
  assert p.price('BTC', 'EUR', t(1, 0) - timedelta(seconds=1), source='market') is None


def test_a_later_row_of_the_day_wins_from_its_time():
  """Two rows on one day: each prices the instants from its own time to the next."""
  rows = [
    *table(1),
    PriceRecord(asset='BTC', quote='EUR', time=t(1, 12), source='market', price=D(2)),
  ]
  p = TablePricing(rows)
  assert p.price('BTC', 'EUR', t(1, 11), source='market') == D(1)
  assert p.price('BTC', 'EUR', t(1, 13), source='market') == D(2)


def test_a_day_without_a_row_is_a_gap_in_the_engine():
  """Through `run` a day the table does not price produces `price_gap` plus `unbooked`, same as any gap."""
  r = run(
    [event('y', 9, leg('BTC', '1', tag='income'))],
    policy=policy(),
    pricing=TablePricing(table(1)),
  )
  assert [x.code for x in r.exceptions] == ['price_gap', 'unbooked']
  assert r.prices[0].price is None
  assert not r.complete
