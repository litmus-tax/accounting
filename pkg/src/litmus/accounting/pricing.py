"""
Pricing protocol and the policy-applying valuer.

The engine never fetches. The caller implements `Pricing`; the engine decides
*which* prices to ask for (timestamp rule, quote path, source class), asks,
records every answer so the caller can persist it, and reports gaps.
"""

import bisect
from datetime import date, datetime, timezone
from decimal import Decimal
from typing_extensions import Protocol, Mapping, Iterable
from litmus.accounting.model import Policy, PriceRecord, PriceSource


class Pricing(Protocol):
  """Implemented by the caller. Return `None` for a gap; never zero."""

  def price(
    self, asset: str, quote: str, time: datetime, *, source: PriceSource
  ) -> Decimal | None:
    """
    Price of one `asset` in `quote` at `time`.

    Args:
      asset: Base asset.
      quote: Quote asset (a fiat or intermediate quote from the policy).
      time: Effective timestamp, already adjusted by the policy's `price_time`.
      source: Source class the policy expects for this pair.
    """
    ...


class PriceGap(Exception):
  """A price along the quote path is missing. Raised by `Valuer`, and by `run(strict=True)`."""

  def __init__(self, asset: str, quote: str, time: datetime):
    super().__init__(f'no price for {asset}/{quote} at {time.isoformat()}')
    self.asset = asset
    self.quote = quote
    self.time = time


class TablePricing:
  """
  In-memory `Pricing` over a table of records; picks the latest price for the
  pair at or before the requested time on the same UTC day. A table is daily:
  a row prices only its own UTC day, and nothing is carried forward to a later
  day. Which price a day takes (a stale one, a peg) is the caller's choice
  (policy 05 rules 1.5 and 39.10). Used by the CLI, portfolio and tests.
  """

  def __init__(self, records: Iterable[PriceRecord]):
    self.table: dict[tuple[str, str], list[tuple[datetime, Decimal]]] = {}
    for r in records:
      if r.price is not None:
        self.table.setdefault((r.asset, r.quote), []).append((r.time, r.price))
    for series in self.table.values():
      series.sort(key=lambda p: p[0])
    self.times = {pair: [t for t, _ in series] for pair, series in self.table.items()}

  def price(
    self, asset: str, quote: str, time: datetime, *, source: PriceSource
  ) -> Decimal | None:
    """Latest price at or before `time` on its UTC day, or `None`."""
    index = bisect.bisect_right(self.times.get((asset, quote), []), time)
    if index == 0:
      return None
    at, price = self.table[(asset, quote)][index - 1]
    if utc_day(at) != utc_day(time):
      return None
    return price


def utc_day(time: datetime) -> date:
  """The UTC calendar day of an aware instant."""
  return time.astimezone(timezone.utc).date()


class FixedPricing:
  """`Pricing` from a constant map of `(asset, quote)` to price. Convenient in tests."""

  def __init__(self, prices: Mapping[tuple[str, str], Decimal | str]):
    self.prices = {k: Decimal(v) for k, v in prices.items()}

  def price(
    self, asset: str, quote: str, time: datetime, *, source: PriceSource
  ) -> Decimal | None:
    """Constant price for the pair, or `None`."""
    return self.prices.get((asset, quote))


def effective_time(time: datetime, policy: Policy) -> datetime:
  """Apply `Policy.price_time`: the event time, or the close (end) of its UTC day."""
  if policy.price_time == 'trade_time':
    return time
  utc = time.astimezone(timezone.utc)
  return utc.replace(hour=23, minute=59, second=59, microsecond=999999)


class Valuer:
  """
  Values quantities in the functional currency according to a policy, through a
  caller-supplied `Pricing`. Every price asked for is appended to `records`,
  gaps included (as `price: None`).
  """

  def __init__(self, pricing: Pricing, policy: Policy):
    self.pricing = pricing
    self.policy = policy
    self.records: list[PriceRecord] = []
    self.cache: dict[tuple[str, str, datetime], Decimal | None] = {}
    self.fiat = set(policy.fiat) | {policy.functional_currency}

  def source(self, asset: str, quote: str) -> PriceSource:
    """`official` when both legs of the pair are fiat, `market` otherwise."""
    return 'official' if asset in self.fiat and quote in self.fiat else 'market'

  def path(self, asset: str) -> list[str]:
    """Quotes to walk from `asset` to the functional currency."""
    fc = self.policy.functional_currency
    if asset in self.fiat:
      return [fc]
    return [*self.policy.quote_path, fc]

  def ask(self, asset: str, quote: str, time: datetime) -> Decimal:
    """One price, recorded; raises `PriceGap` when missing."""
    key = (asset, quote, time)
    if key not in self.cache:
      source = self.source(asset, quote)
      price = self.pricing.price(asset, quote, time, source=source)
      self.cache[key] = price
      self.records.append(
        PriceRecord(asset=asset, quote=quote, time=time, source=source, price=price)
      )
    price = self.cache[key]
    if price is None:
      raise PriceGap(asset, quote, time)
    return price

  def rate(self, asset: str, time: datetime) -> Decimal:
    """Functional-currency price of one unit of `asset` at `time` (policy timestamp applied)."""
    fc = self.policy.functional_currency
    if asset == fc:
      return Decimal(1)
    t = effective_time(time, self.policy)
    rate = Decimal(1)
    base = asset
    for quote in self.path(asset):
      if base == quote:
        continue
      rate *= self.ask(base, quote, t)
      base = quote
    return rate

  def value(self, asset: str, quantity: Decimal, time: datetime) -> Decimal:
    """Value of a signed `quantity` of `asset` in the functional currency."""
    return quantity * self.rate(asset, time)
