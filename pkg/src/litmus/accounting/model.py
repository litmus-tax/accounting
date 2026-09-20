"""
Ledger-input model and output records.

Every type is a frozen `dataclass`. The same classes are the JSON boundary
through `pydantic.TypeAdapter` (see `litmus.accounting.codec`), so there is one
schema for the library, the CLI and JSON Schema exports.

There are no venue concepts: a compartment is an opaque string, an asset is an
opaque string, and the only structure is legs grouped into events plus links
between events.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing_extensions import Literal
from pydantic import ConfigDict

DOCUMENTED = ConfigDict(use_attribute_docstrings=True, extra='forbid')
"""Pydantic config shared by every model: field docstrings become schema descriptions."""

LegTag = Literal[
  'trade', 'transfer', 'income', 'expense', 'borrow', 'repay', 'rollover'
]
"""
How the engine treats a leg:

- `trade`: one side of an exchange of assets. All trade legs of an event are
  valued together (see `Policy.trade_valuation`); legs may sit in different
  compartments (an asset-changing bridge is one two-leg trade event).
- `transfer`: a movement in or out of a compartment. Linked (via `Link`) it is
  an internal movement; unlinked it is external and booked at market value.
- `income`: an inflow valued at market at event time (funding received, yield,
  rewards). Opens a lot at that value. Quantity must be positive.
- `expense`: an outflow valued at market at event time (funding paid, fees).
  Consumes lots at market value. Quantity must be negative.
- `borrow`: an asset received against a liability. Opens a lot at market value
  and a liability of the same quantity in the leg's compartment; no income, no
  PnL. Ordinary borrowing must be positive. Labelled `interest` it records
  signed noncash accrual: positive increases debt at market basis, negative
  reverses debt and releases proportional carried basis. Neither receives or
  disposes inventory; a reversal needs no market price.
- `repay`: an asset given back against a liability. A disposal at market with
  its normal PnL; the liability shrinks. Quantity must be negative.
"""


@dataclass(frozen=True)
class Leg:
  """One signed quantity of one asset in one compartment."""

  __pydantic_config__ = DOCUMENTED

  asset: str
  """Opaque asset identifier. Perp positions are assets too (e.g. `BTC-PERP`)."""
  quantity: Decimal
  """Signed: positive inflow, negative outflow. Never zero."""
  compartment: str
  """Opaque account / subaccount / wallet identifier."""
  tag: LegTag
  fee: bool = False
  """Marks the leg as a fee of its event. Fee legs are negative; `Policy.fee_treatment` decides their booking."""
  label: str | None = None
  """
  Free-form sub-classification for reporting (`funding`, `gas`, `withdrawal`).
  On `borrow` and `repay` legs it names the facility; `interest` on a `borrow`
  leg marks signed noncash accrual or reversal (see `LegTag`).
  """


@dataclass(frozen=True)
class RolloverInput:
  """A negative leg and optional exact lot selection in consumption order."""

  __pydantic_config__ = DOCUMENTED
  leg: int
  lots: tuple[str, ...] = ()


@dataclass(frozen=True)
class RolloverOutput:
  """A positive leg and its fraction of all carried basis."""

  __pydantic_config__ = DOCUMENTED
  leg: int
  allocation: Decimal | None = None


@dataclass(frozen=True)
class Rollover:
  """Caller-selected basis transformation without a market-value exchange."""

  __pydantic_config__ = DOCUMENTED
  inputs: tuple[RolloverInput, ...]
  outputs: tuple[RolloverOutput, ...]
  acquisition_date: Literal['carry', 'operation'] = 'carry'
  capitalized_costs: Decimal = Decimal(0)
  """Explicit functional-currency addition; never implies cash payment or accrual."""
  cost_reference: str | None = None
  """Caller evidence/policy reference required for nonzero capitalized costs."""
  capitalized_fee_legs: tuple[int, ...] = ()
  """Paid fee legs consumed at market value without expense flows; included in capitalized_costs."""
  write_off: bool = False
  """Explicit zero-proceeds disposal; requires no outputs or capitalized costs."""


@dataclass(frozen=True)
class NotionalScope:
  """A caller-evidenced compartment/asset allowed a signed notional balance."""

  __pydantic_config__ = DOCUMENTED
  compartment: str
  asset: str


@dataclass(frozen=True)
class Origin:
  """Original acquisition or explicit capitalized cost contributing basis."""

  __pydantic_config__ = DOCUMENTED
  event: str
  acquired: datetime
  cost: Decimal


@dataclass(frozen=True)
class Event:
  """A dated group of legs that happened together."""

  __pydantic_config__ = DOCUMENTED

  id: str
  """Stable id, unique across the ledger; referenced by links and by every output row."""
  time: datetime
  """Timezone-aware."""
  legs: tuple[Leg, ...]
  description: str | None = None
  depends_on: tuple[str, ...] = ()
  rollover: Rollover | None = None


@dataclass(frozen=True)
class Link:
  """Two events that are one internal movement (source out, destination in)."""

  __pydantic_config__ = DOCUMENTED

  src: str
  """Event id whose transfer legs are the outflow."""
  dst: str
  """Event id whose transfer legs are the inflow."""


CostMethod = Literal['fifo', 'lifo', 'hifo', 'average']
"""
Which open lots a disposal consumes: oldest first, newest first, highest unit
cost first (ties oldest first), or a proportional share of one pooled lot.
"""
LotScope = Literal['global', 'compartment']
"""`global`: one lot pool per asset. `compartment`: one pool per (compartment, asset)."""
PriceTime = Literal['trade_time', 'daily_close']
"""Which timestamp to price at: the event's own time, or the close of its UTC day."""
TradeValuation = Literal['given', 'received']
"""Which side of a trade fixes its value when neither side is cash."""
FeeTreatment = Literal['expense', 'capitalize']
"""
`expense`: every fee is an expense flow at market value.
`capitalize`: a fee on a trade is added to the basis of what the trade acquires,
or deducted from the proceeds of what it disposes; fees on other events remain
expenses.
"""
LiabilityValuation = Literal['cost', 'market']
"""
`cost`: a liability carries no PnL of its own; repaying it only disposes of the
asset given. `market`: the liability is revalued in functional currency at
repayment and the difference between its basis and its market value is realized
(for regimes that treat crypto debt like FX debt).
"""


@dataclass(frozen=True)
class Policy:
  """Accounting policy. Everything the engine needs beyond the ledger itself."""

  __pydantic_config__ = DOCUMENTED

  cost_method: CostMethod
  lot_scope: LotScope
  functional_currency: str
  """The reporting currency. Has no lots; every other asset does."""
  price_time: PriceTime = 'trade_time'
  quote_path: tuple[str, ...] = ()
  """
  Intermediate quotes between a non-fiat asset and the functional currency, e.g.
  `['USD']` prices `BTC` as `BTC/USD × USD/EUR`. Empty means a direct quote.
  """
  fiat: tuple[str, ...] = ()
  """Assets priced from the `official` source class (FX). The functional currency is always fiat."""
  cash: tuple[str, ...] = ()
  """Assets that fix a trade's value when present on one side (stables, fiat)."""
  trade_valuation: TradeValuation = 'received'
  fee_treatment: FeeTreatment = 'expense'
  position_assets: tuple[str, ...] = ()
  """Assets allowed to go net short (perp positions). Any other asset going negative is a `negative_position` exception."""
  minor_unit: int | None = None
  """Decimal places of the functional currency's minor unit; when set, money outputs are rounded to it."""
  liability_valuation: LiabilityValuation = 'cost'
  notional_scopes: tuple[NotionalScope, ...] = ()


PriceSource = Literal['market', 'official']
"""Source class the caller should use: `official` for fiat FX, `market` otherwise."""


@dataclass(frozen=True)
class PriceRecord:
  """A price the engine asked for, as the caller should persist it."""

  __pydantic_config__ = DOCUMENTED

  asset: str
  quote: str
  time: datetime
  """The effective timestamp after applying `Policy.price_time`."""
  source: PriceSource
  price: Decimal | None
  """`None` when the caller had no price (a gap, also reported as an exception)."""


@dataclass(frozen=True)
class Lot:
  """An open (possibly short) holding with a cost basis."""

  __pydantic_config__ = DOCUMENTED

  id: str
  asset: str
  compartment: str | None
  """`None` under global lot scope."""
  quantity: Decimal
  """Signed; negative for short positions."""
  cost: Decimal
  """Total basis in functional currency, same sign convention as quantity."""
  acquired: datetime
  """Under `average` the pool's first acquisition; see docs/model.md."""
  event: str
  """Event that opened the lot (the original one after internal moves)."""
  origins: tuple[Origin, ...] = ()
  """Exact unrounded basis ancestry, retained across transformations and partial disposal."""


@dataclass(frozen=True)
class Realized:
  """A disposal (or short cover) matched against lots."""

  __pydantic_config__ = DOCUMENTED

  event: str
  time: datetime
  asset: str
  compartment: str
  quantity: Decimal
  """Quantity disposed (negative) or covered (positive)."""
  proceeds: Decimal
  """Value received (or paid, for a cover) in functional currency, net of `fees`."""
  cost: Decimal
  """Basis of the lots consumed."""
  pnl: Decimal
  lots: tuple[str, ...]
  """Ids of the lots consumed."""
  fees: Decimal = Decimal(0)
  """Fee value deducted from proceeds (only under `fee_treatment: capitalize`)."""


@dataclass(frozen=True)
class Flow:
  """An income or expense line in functional currency."""

  __pydantic_config__ = DOCUMENTED

  event: str
  time: datetime
  asset: str
  compartment: str
  quantity: Decimal
  value: Decimal
  """Always positive."""
  kind: Literal['income', 'expense']
  fee: bool
  label: str | None


@dataclass(frozen=True)
class Move:
  """An internal movement of lots between compartments at carried basis."""

  __pydantic_config__ = DOCUMENTED

  link: Link
  time: datetime
  """When the move was booked: the earlier of the two linked events' times."""
  asset: str
  quantity: Decimal
  cost: Decimal
  lots: tuple[str, ...]
  """Ids of the lots recreated in the destination."""


@dataclass(frozen=True)
class Liability:
  """What is owed per (compartment, asset), opened by `borrow` legs and reduced by `repay` legs."""

  __pydantic_config__ = DOCUMENTED

  id: str
  asset: str
  compartment: str
  """Always the leg's compartment; liabilities are never pooled globally."""
  quantity: Decimal
  """Amount owed. Positive; negative only after a `negative_liability` exception."""
  cost: Decimal
  """Functional-currency value of what is owed at the time each part was borrowed or accrued."""
  label: str | None
  """Facility named by the opening `borrow` leg."""
  opened: datetime
  """Time of the opening `borrow` leg."""
  updated: datetime
  """Time of the last `borrow` or `repay` leg that touched it."""
  event: str
  """Event that opened the liability."""


ExceptionCode = Literal[
  'price_gap',
  'unmatched_transfer',
  'link_mismatch',
  'unknown_event',
  'invalid_event',
  'duplicate_id',
  'negative_position',
  'negative_liability',
  'unbooked',
]
"""
- `price_gap`: the pricing source had no price; the leg (or trade) is left unbooked.
- `unmatched_transfer`: a transfer with no link, booked at market value.
- `link_mismatch`: a link whose quantities do not conserve per asset; not moved.
- `unknown_event`: a link references an event id that does not exist.
- `invalid_event`: an event that fails validation; skipped.
- `duplicate_id`: two events share an id; the later one is skipped.
- `negative_position`: an asset outside `Policy.position_assets` went net short.
- `negative_liability`: a repay or noncash interest reversal exceeded what was owed under its (compartment, asset); booked anyway.
- `unbooked`: a leg left out of the books (always paired with the cause). Makes the run incomplete.
"""


@dataclass(frozen=True)
class ExceptionItem:
  """One item of the exceptions report."""

  __pydantic_config__ = DOCUMENTED

  code: ExceptionCode
  event: str | None
  message: str
  detail: dict[str, str] = field(default_factory=dict[str, str])


@dataclass(frozen=True)
class Balance:
  """Net quantity per (compartment, asset) from the legs alone, as a check."""

  __pydantic_config__ = DOCUMENTED

  compartment: str
  asset: str
  quantity: Decimal


@dataclass(frozen=True)
class RolloverSlice:
  """Exact consumed or created lot slice in one transformation."""

  __pydantic_config__ = DOCUMENTED
  leg: int
  lot: str
  quantity: Decimal
  cost: Decimal
  acquired: datetime
  origins: tuple[Origin, ...]


@dataclass(frozen=True)
class RolloverRecord:
  """Unrounded audit trail; final allocation receives decimal division residue."""

  __pydantic_config__ = DOCUMENTED
  event: str
  consumed: tuple[RolloverSlice, ...]
  created: tuple[RolloverSlice, ...]
  allocations: tuple[Decimal, ...]
  basis_in: Decimal
  capitalized_costs: Decimal
  basis_out: Decimal
  written_off: Decimal
  cost_reference: str | None


@dataclass(frozen=True)
class Result:
  """Everything `run` returns."""

  __pydantic_config__ = DOCUMENTED

  policy: Policy
  lots: tuple[Lot, ...]
  liabilities: tuple[Liability, ...]
  """Open liabilities, reported separately from lots and balances."""
  realized: tuple[Realized, ...]
  flows: tuple[Flow, ...]
  moves: tuple[Move, ...]
  balances: tuple[Balance, ...]
  prices: tuple[PriceRecord, ...]
  exceptions: tuple[ExceptionItem, ...]
  complete: bool
  """False when any leg could not be booked (every such case is also an exception)."""
  rollovers: tuple[RolloverRecord, ...] = ()
  schema_version: Literal['0.3'] = '0.3'


@dataclass(frozen=True)
class Position:
  """An open lot valued at a point in time."""

  __pydantic_config__ = DOCUMENTED

  lot: str
  asset: str
  compartment: str | None
  quantity: Decimal
  cost: Decimal
  value: Decimal | None
  """Market value in functional currency; `None` on a price gap."""
  unrealized: Decimal | None
  """`value - cost`; `None` on a price gap."""


@dataclass(frozen=True)
class LiabilityPosition:
  """An open liability valued at a point in time."""

  __pydantic_config__ = DOCUMENTED

  liability: str
  asset: str
  compartment: str
  label: str | None
  quantity: Decimal
  cost: Decimal
  value: Decimal | None
  """Market value in functional currency of what is owed; `None` on a price gap."""
  unrealized: Decimal | None
  """`cost - value` under `liability_valuation: market`; `None` under `cost` (the liability carries no PnL) or on a price gap."""


@dataclass(frozen=True)
class Valuation:
  """Period-end valuation of open lots and liabilities (`value`)."""

  __pydantic_config__ = DOCUMENTED

  at: datetime
  positions: tuple[Position, ...]
  liabilities: tuple[LiabilityPosition, ...]
  prices: tuple[PriceRecord, ...]
  exceptions: tuple[ExceptionItem, ...]
  complete: bool


@dataclass(frozen=True)
class Ledger:
  """The JSON document the CLI reads: events, optional links, policy and price table."""

  __pydantic_config__ = DOCUMENTED

  events: tuple[Event, ...]
  schema_version: Literal['0.2', '0.3'] = '0.3'
  links: tuple[Link, ...] = ()
  policy: Policy | None = None
  prices: tuple[PriceRecord, ...] = ()
  max_age: timedelta | None = None
  """
  Oldest a price in `prices` may be, relative to the requested time, before it
  counts as a gap (ISO 8601 duration, e.g. `P1D`; seconds as a number). `None`
  carries the latest price forward without bound.
  """
