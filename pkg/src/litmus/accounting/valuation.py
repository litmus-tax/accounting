"""Period-end valuation: open lots and liabilities priced at one point in time through the same pricing protocol."""

from datetime import datetime
from typing_extensions import Sequence
from litmus.accounting.model import (
  Lot,
  Liability,
  LiabilityPosition,
  Policy,
  Position,
  Valuation,
  ExceptionItem,
)
from litmus.accounting.pricing import Pricing, Valuer, PriceGap
from litmus.accounting.engine.rounding import round_valuation


def gap_item(e: PriceGap, event: str, **detail: str) -> ExceptionItem:
  """The `price_gap` exception for one missing price."""
  return ExceptionItem(
    'price_gap',
    event,
    str(e),
    {'asset': e.asset, 'quote': e.quote, 'time': e.time.isoformat(), **detail},
  )


def value(
  lots: Sequence[Lot],
  *,
  liabilities: Sequence[Liability] = (),
  at: datetime,
  policy: Policy,
  pricing: Pricing,
  strict: bool = False,
) -> Valuation:
  """
  Value open lots and liabilities at `at`.

  Args:
    lots: Open lots, typically `Result.lots`.
    liabilities: Open liabilities, typically `Result.liabilities`.
    at: Valuation time (timezone-aware); `policy.price_time` applies to it.
    policy: Same policy the lots were produced under.
    pricing: Caller-implemented price source.
    strict: Raise `PriceGap` on the first missing price instead of reporting it.

  Returns:
    One position per lot with market value and unrealized PnL, one per
    liability with its market value (unrealized only under
    `liability_valuation: market`), every price asked for, and price gaps as
    exceptions. `complete` is false on any gap.

  Raises:
    PriceGap: In strict mode, on the first missing price.
  """
  valuer = Valuer(pricing, policy)
  positions: list[Position] = []
  owed: list[LiabilityPosition] = []
  exceptions: list[ExceptionItem] = []
  for lot in lots:
    try:
      market = valuer.value(lot.asset, lot.quantity, at)
    except PriceGap as e:
      if strict:
        raise
      exceptions.append(gap_item(e, lot.event, lot=lot.id))
      positions.append(
        Position(lot.id, lot.asset, lot.compartment, lot.quantity, lot.cost, None, None)
      )
      continue
    positions.append(
      Position(
        lot.id,
        lot.asset,
        lot.compartment,
        lot.quantity,
        lot.cost,
        market,
        market - lot.cost,
      )
    )
  for liability in liabilities:
    try:
      market = valuer.value(liability.asset, liability.quantity, at)
    except PriceGap as e:
      if strict:
        raise
      exceptions.append(gap_item(e, liability.event, liability=liability.id))
      market = None
    owed.append(
      LiabilityPosition(
        liability=liability.id,
        asset=liability.asset,
        compartment=liability.compartment,
        label=liability.label,
        quantity=liability.quantity,
        cost=liability.cost,
        value=market,
        unrealized=(
          liability.cost - market
          if market is not None and policy.liability_valuation == 'market'
          else None
        ),
      )
    )
  valuation = Valuation(
    at=at,
    positions=tuple(positions),
    liabilities=tuple(owed),
    prices=tuple(valuer.records),
    exceptions=tuple(exceptions),
    complete=not exceptions,
  )
  return round_valuation(valuation, policy.minor_unit)
