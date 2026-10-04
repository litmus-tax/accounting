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
from datetime import datetime
from decimal import Decimal
from typing_extensions import Literal, Sequence
from pydantic import ConfigDict

DOCUMENTED = ConfigDict(use_attribute_docstrings=True, extra='forbid')
"""Pydantic config shared by every model: field docstrings become schema descriptions."""

LegTag = Literal[
  'trade',
  'transfer',
  'income',
  'expense',
  'borrow',
  'repay',
  'rollover',
  'position',
  'notional',
  'contents',
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
  and a liability of the same quantity under `(Leg.liability, asset)`; no
  income, no PnL. Ordinary borrowing must be positive. Labelled `interest` it
  records signed noncash accrual (policy 05 rule 9.3): positive increases debt
  at market value and is an interest expense; negative reverses debt, releases
  proportional carried basis and is interest income of that basis. Neither
  receives or disposes inventory; a reversal needs no market price.
- `repay`: an asset given back against a liability. A disposal at market with
  its normal PnL; the liability shrinks, and the difference between the value
  given and the basis released is realized against it (policy 05 rule 9.4).
  Quantity must be negative.
- `rollover`: an input or output of the event's `Rollover`.
- `position`: the size leg of a perpetual fill: the signed change of the open
  position in `asset` (the instrument) in the leg's compartment. Books nothing
  (policy 05 rule 8.1). On a `notional` compartment its price comes from the
  fill's `notional` leg; on a `pnl` compartment it carries `price` and
  `settles_in`.
- `contents`: a change in what an opaque compartment holds that no transfer
  explains (an opaque result's per-asset residual, policy 05 rule 14.8). Only
  in a compartment of `Policy.opaque_compartments`. Books nothing by itself:
  when an event with `contents` legs leaves its compartment's event-implied
  contents at zero in every asset, the position's remaining cost is a
  `performance` loss (the empty compartment, rule 14.8).
- `notional`: the notional cash leg of a perpetual fill on a `notional`
  compartment, in the settlement asset: not booked; it gives the fill price
  (cash / size, rule 8.2).
"""


TransferBasis = Literal['unclassified', 'market', 'carried']
"""
How an unlinked transfer leg is booked (policy 05 rules 11 and 13.3):

- `unclassified`: at market value, and reported as `unmatched_transfer` (rule 13.4).
- `market`: classified as not retained (a payment, sale, purchase or gift): a
  disposal or acquisition at market value, not reported.
- `carried`: the asset stays the owner's outside the books (`ownership_retained`),
  or an unsolicited receipt at zero cost: out, lots leave at cost with no P&L;
  in, a lot opens at `cost` and `acquired`, with no income.

A linked transfer ignores it. Functional-currency transfers carry no lots and are
never reported (rule 13.2).
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
  On `borrow` and `repay` legs it describes the facility; `interest` on a
  `borrow` leg marks signed noncash accrual or reversal (see `LegTag`).
  """
  liability: str | None = None
  """
  On `borrow` and `repay` legs only: the liability compartment of the facility,
  the compartment of the unit's counter-leg (policy 05 rule 9.1). The liability
  is identified by `(liability, asset)`, so two facilities never merge. Absent,
  the leg's own compartment.
  """
  price: Decimal | None = None
  """On `position` legs of `pnl` compartments only: the venue's fill price, in `settles_in` per unit."""
  basis: TransferBasis = 'unclassified'
  """On unlinked `transfer` legs: how the boundary crossing is booked (policy 05 rules 11 and 13.3)."""
  cost: Decimal | None = None
  """On inbound `carried` transfer legs only (required there): the functional-currency cost the lot opens at."""
  acquired: datetime | None = None
  """On inbound `carried` transfer legs only: the lot's acquisition time; absent, the event's."""
  settles_in: str | None = None
  """On `position` legs of `pnl` compartments only: the settlement asset of the instrument."""


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
  """
  The output's fraction of the carried basis. With several outputs, give every
  allocation (summing exactly to 1) or none: then the engine allocates by the
  outputs' market value at the operation (policy 05 positions rule 5.1).
  """


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


Group = tuple[Event, ...]
"""
An atomic group (policy 05 rule 6.3): events applied in order, whose holdings
are checked for shortness only after the last of them, never in between.
"""
Events = Sequence[Event | Sequence[Event]]
"""
The engine's input: atomic groups in total order. An item that is a single
event is a group of one, so a flat list of events is one event per group.
"""


LinkKind = Literal['transfer', 'swap']
"""
`transfer`: the same units move, at carried basis, conserving each asset.
`swap`: a movement that changed asset on the way (a swap bridge, policy 05 rule
13.5): the outflow is disposed of at market and the inflow acquired at that value.
"""


@dataclass(frozen=True)
class Link:
  """Two events that are one internal movement (source out, destination in)."""

  __pydantic_config__ = DOCUMENTED

  src: str
  """Event id whose transfer legs are the outflow."""
  dst: str
  """Event id whose transfer legs are the inflow."""
  kind: LinkKind = 'transfer'


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
or deducted from the proceeds of what it disposes; fees on other events
(perpetual fills included) remain expenses.
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
  notional_scopes: tuple[NotionalScope, ...] = ()
  perp_cost_method: CostMethod = 'average'
  """Which entries a reduction of a perpetual position on a `notional` compartment closes (policy 05 rule 8.2)."""
  opaque_compartments: tuple[str, ...] = ()
  """
  Compartments booked as one position by value (policy 05 rule 14): the asset
  `position:opaque:<compartment>`, held in units of the functional currency at
  a cost of 1 each. A `transfer` leg into one carries the coins' basis into the
  position (a linked one) or enters at market value (an unlinked one); a
  `transfer` leg out of one is a redemption: the coin received opens a lot at
  market value, the position releases its cost up to that value, and any value
  beyond it is `performance` income. `contents` legs track what it holds; a fee
  leg in it is paid from inside, part of its result, and books nothing of its
  own; any other leg in it is unbooked. Results are recognised only at redemptions and
  when `contents` legs leave it empty (interim rule, specs#135): no true-ups of
  a position still open.
  """


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
  """Exact unrounded basis ancestry, retained across transformations and partial disposal; empty under `average` (policy 05 rule 18.2)."""


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
  instrument: str | None = None
  """The perpetual position whose reduction realized this flow (label `realized_pnl`, notional compartments)."""


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
class PerpPosition:
  """An open perpetual position: tracked, not booked (policy 05 rule 8.1)."""

  __pydantic_config__ = DOCUMENTED

  compartment: str
  instrument: str
  settles_in: str
  """The settlement asset its entry and P&L are in."""
  settlement: Literal['notional', 'pnl']
  size: Decimal
  """Signed: negative for a short."""
  entry: Decimal
  """Entry basis in `settles_in`, signed like `size`: `perp_cost_method` entries on `notional`, average entry on `pnl`."""
  opened: datetime
  """Earliest entry still open."""


@dataclass(frozen=True)
class Liability:
  """What is owed per (liability compartment, asset), opened by `borrow` legs and reduced by `repay` legs."""

  __pydantic_config__ = DOCUMENTED

  id: str
  asset: str
  compartment: str
  """The liability compartment (`Leg.liability`, else the leg's own); never pooled globally."""
  quantity: Decimal
  """Amount owed. Positive; negative only after a `negative_liability` exception."""
  cost: Decimal
  """Functional-currency value of what is owed at the time each part was borrowed or accrued."""
  label: str | None
  """Label of the opening leg; informational, the identity is `(compartment, asset)`."""
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
  'link_conflict',
  'unknown_event',
  'invalid_event',
  'duplicate_id',
  'negative_position',
  'negative_liability',
  'unbooked',
  'unbalanced',
  'cost_identity',
  'quantity_residue',
  'unpriced_opaque_fee',
]
"""
- `price_gap`: the pricing source had no price; the leg (or trade) is left unbooked.
- `unmatched_transfer`: a transfer with no link, booked at market value.
- `link_mismatch`: a link whose quantities do not conserve per asset; not moved, its legs unbooked.
- `link_conflict`: a link naming an event that is already in another link (or linking an event to itself); not booked.
- `unknown_event`: a link references an event id that does not exist.
- `invalid_event`: an event that fails validation; skipped.
- `duplicate_id`: two events share an id; the later one is skipped.
- `negative_position`: an asset outside `Policy.position_assets` is net short
  after an atomic group (policy 05 rule 6.3); one item per holding and group,
  on the group's last event that touched it.
- `negative_liability`: a repay or noncash interest reversal exceeded what was
  owed under its (liability compartment, asset), and the liability is still
  negative after the group; booked anyway.
- `unbooked`: a leg left out of the books (always paired with the cause). Makes the run incomplete.
- `unbalanced`: an event whose journal lines do not balance before rounding (policy 05 rule 29.2). Makes the run incomplete.
- `cost_identity`: after an atomic group, assets at cost (holdings and opaque
  positions) minus liabilities at carrying value differ from net contributions
  plus realized P&L plus income minus expenses (policy 05 rule 39.5.1). Reported
  for the group that opens or changes the difference. Makes the run incomplete.
- `quantity_residue`: informational. A take or a change would have left a
  holding nonzero but smaller than 1e-18; it was treated as zero, its basis
  released with the take (policy 05 rule 20.3). `detail` names the `asset`,
  `compartment` and `residue`. Never makes the run incomplete.
- `unpriced_opaque_fee`: informational. A fee leg inside an opaque compartment
  had no price at its time: it books no expense and reduces no cost, and stays
  in the compartment's `performance` through its contents (policy 05 rule
  14.14.3). `detail` names the `asset`, `compartment` and `quantity`; the event
  is the record. Never makes the run incomplete.
"""


@dataclass(frozen=True)
class ExceptionItem:
  """One item of the exceptions report."""

  __pydantic_config__ = DOCUMENTED

  code: ExceptionCode
  event: str | None
  message: str
  detail: dict[str, str] = field(default_factory=dict[str, str])


JournalAccount = Literal[
  'holding', 'liability', 'realized', 'income', 'expense', 'external', 'rounding'
]
"""
The kinds of account a journal line is on (policy 05 rule 29.1):

- `holding`: an asset held at cost, per lot key `(compartment, asset)` (the
  compartment is `None` under global lot scope); the functional currency too.
- `liability`: what is owed, per `(liability compartment, asset)`.
- `realized`: realized gains (credits) and losses (debits), per asset.
- `income`, `expense`: flows by `label`.
- `external`: movements across the books' boundary (unlinked transfers,
  capitalized costs not paid in the ledger).
- `rounding`: the rounding residue of an event, within the rounding bound.
"""


@dataclass(frozen=True, slots=True)
class JournalLine:
  """One line of an event's double entry in the functional currency."""

  __pydantic_config__ = DOCUMENTED

  event: str
  time: datetime
  account: JournalAccount
  compartment: str | None
  asset: str | None
  label: str | None
  debit: Decimal
  credit: Decimal
  """One of `debit` and `credit` is zero."""


OpenKind = Literal['holding', 'liability', 'position', 'opaque']
"""
The kind of an open-lot row (policy 05 term 14):

- `holding`: an asset held at cost per lot key, the functional currency included.
- `liability`: what is owed per `(liability compartment, asset)`, at carrying value.
- `position`: an open perpetual position, its size and entry basis in `settles_in`.
- `opaque`: an opaque compartment's position and its remaining cost (rule 14.1).
"""


@dataclass(frozen=True)
class OpenRow:
  """What is still held at cost at the end of the run, its `as_of` (policy 05 term 14). Needs no price."""

  __pydantic_config__ = DOCUMENTED

  kind: OpenKind
  compartment: str | None
  """The lot key's compartment (`None` under global lot scope); a liability's or position's compartment."""
  asset: str
  """The asset held or owed; a position's instrument; an opaque position's `position:opaque:<compartment>`."""
  quantity: Decimal
  """Quantity held or owed; a position's signed size; an opaque position's units at cost."""
  cost: Decimal
  """Remaining cost in the functional currency: the open lots' basis, a liability's carrying value; a position's entry basis in `settles_in`, signed like its size."""
  settles_in: str | None = None
  """`position` rows only: the settlement asset its entry basis is in."""


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


ResultVersion = Literal['0.13']
"""The result schema this engine writes and reads (policy 05 rule 27.2)."""
RESULT_VERSION: ResultVersion = '0.13'


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
  positions: tuple[PerpPosition, ...] = ()
  """Open perpetual positions at the end of the run (policy 05 rule 8.1)."""
  journal: tuple[JournalLine, ...] = ()
  """Double entry per event, balanced after rounding (policy 05 rule 29)."""
  open_rows: tuple[OpenRow, ...] = ()
  """What is still held at cost at the end of the run, its `as_of` (policy 05 term 14): holdings, liabilities, open positions and opaque positions."""
  schema_version: ResultVersion = RESULT_VERSION


@dataclass(frozen=True)
class Ledger:
  """The JSON document the CLI reads: events, optional links, policy and the prices its events need."""

  __pydantic_config__ = DOCUMENTED

  events: tuple[Event | Group, ...]
  """
  Atomic groups in total order (policy 05 rule 6.3): a list of events is one
  group, a bare event a group of one.
  """
  schema_version: Literal[
    '0.2',
    '0.3',
    '0.4',
    '0.5',
    '0.6',
    '0.7',
    '0.8',
    '0.9',
    '0.10',
    '0.11',
    '0.12',
    '0.13',
  ] = '0.13'
  links: tuple[Link, ...] = ()
  policy: Policy | None = None
  prices: tuple[PriceRecord, ...] = ()
  """
  The prices the events need (policy 05 rule 1.5). A row prices only its own
  UTC day: the caller chooses each day's price, the engine carries none forward.
  """
