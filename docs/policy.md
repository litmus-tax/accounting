# Policy

`Policy` is everything the engine needs beyond the ledger. Required:
`cost_method`, `lot_scope`, `functional_currency`. Everything else has a
default.

| Field | Values | Default | Effect |
|---|---|---|---|
| `cost_method` | `fifo` \| `lifo` \| `hifo` \| `average` | required | Which lots a disposal consumes: oldest first; newest first; highest absolute unit cost first (ties oldest first); a proportional share of one pooled lot. |
| `lot_scope` | `global` \| `compartment` | required | One lot pool per asset, or one per `(compartment, asset)`. Under `global` a link only classifies a transfer as internal; under `compartment` it also moves lots at carried basis. |
| `functional_currency` | str | required | Reporting currency. Has no lots. Always counts as fiat. |
| `price_time` | `trade_time` \| `daily_close` | `trade_time` | Price at the event's own time, or at 23:59:59.999999 UTC of its day. |
| `quote_path` | list of str | `[]` | Intermediate quotes for non-fiat assets: `["USD"]` prices `BTC` as `BTC/USD × USD/EUR`. |
| `fiat` | list of str | `[]` | Assets priced directly in the functional currency from the `official` source class. |
| `cash` | list of str | `[]` | Assets that fix a trade's value when present on one side. Without it a perp fill would try to price `BTC-PERP`. |
| `trade_valuation` | `given` \| `received` | `received` | Which side fixes a trade's value when neither side holds the functional currency or cash. |
| `fee_treatment` | `expense` \| `capitalize` | `expense` | Below. |
| `position_assets` | list of str | `[]` | Assets that may go net short (perp position assets). Any other asset going negative under its lot key is a `negative_position` exception (the short lot is still opened). |
| `liability_valuation` | `cost` \| `market` | `cost` | Below. |
| `minor_unit` | int \| null | `null` | Decimal places of the functional currency's minor unit. When set, every money field of the result and the valuation is rounded half-up to it at output; quantities and prices are never rounded, and `pnl` / `unrealized` are recomputed from the rounded parts so the identities hold. |

Strict mode is a run option, not a policy field: `run(..., strict=True)` and
`value(..., strict=True)` raise `PriceGap` on the first missing price; the CLI
flag is `--strict` and returns status 1 when a missing price aborts the run.

## Fee treatment

A fee leg is any leg with `fee: true`; it must be negative.

1. `expense` (default): every fee is an expense `Flow` at market value and, when paid in a non-functional asset, a disposal of that asset at market (with its own realized PnL). It never touches the basis or proceeds of the trade it belongs to.
2. `capitalize`: on an event that has trade legs, the fee legs are valued at market and folded into the non-fixing side of the trade. If that side is received (an acquisition, e.g. buying BTC with EUR or opening a perp with USDC) the fee value is added to the basis of the lots opened. If that side is given (a disposal, e.g. selling BTC for EUR or closing a perp) the fee value is deducted from proceeds, and the `Realized` row reports it in `fees`. No `Flow` is emitted for those fees. The fee legs still leave their own lots (a USDC fee reduces the USDC lots at market). Fees on events without trade legs (transfer fees, gas on a plain transfer, funding) remain expenses.

A trade whose fee cannot be priced is left entirely unbooked (`price_gap` plus one `unbooked` per leg), like a trade whose fixing side cannot be priced.

## Liability valuation

A `borrow` leg opens a liability worth the market value of what was received; a `repay` leg disposes of the asset given (normal PnL on its lots) and reduces the liability.

1. `cost` (default): the liability carries no PnL of its own. Repaying releases its basis and nothing is realized against it; `value` reports the liability's market value with `unrealized: null`.
2. `market`: the liability is revalued in functional currency at repayment and the difference between the basis released and the market value repaid is realized as a `Realized` row whose `lots` names the liability id (`quantity` positive like a short cover, `proceeds` negative, `pnl = proceeds - cost`). For regimes that treat crypto debt like FX debt. `value` reports `unrealized = cost - value`. When the borrowed asset is still held at repayment, the asset's gain and the liability's loss cancel.

Accrued unpaid interest is a `borrow` leg labelled `interest`: the liability grows at market value and nothing is received. Interest actually paid is an ordinary `expense` leg (labelled `interest` by convention); it does not touch the liability. Interest is never capitalized into the borrowed asset's lots.

## Cash and position assets for notional venues

On a notional venue (dYdX, Hyperliquid) a fill is a trade of the position asset against the settlement asset: `+1 BTC-PERP`, `-50000 USDC`, fee `-10 USDC`. The caller lists `USDC` in `cash` (so the fill is valued from the cash side) and `BTC-PERP` in `position_assets` (so a short is not an exception). Realized PnL on the position is then an accounting output; the venue's own PnL figure is a diagnostic.

## PnL-settled venues

On a PnL-settled venue (most CEX futures exports) the ledger never sees the position: the venue moves cash by realized PnL, funding and commission. Book those as `income` / `expense` legs in the settlement asset with a label (`realized_pnl`, `funding`, `commission`). The position has no lot, no basis and no unrealized figure in the engine; a year-end position on such a venue is a question for the caller, not the engine. There is deliberately no venue concept to switch on.
