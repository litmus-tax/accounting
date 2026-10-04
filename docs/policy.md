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
| `perp_cost_method` | `average` \| `fifo` \| `lifo` \| `hifo` | `average` | Which entries a reduction of a perpetual position on a `notional` compartment closes (below). |
| `opaque_compartments` | list of str | `[]` | Compartments booked as one position by value (policy 05 rule 14; [model](model.md#opaque-positions)). |
| `minor_unit` | int \| null | `null` | Decimal places of the functional currency's minor unit. When set, every money field of the result is rounded half-up to it at output; quantities and prices are never rounded, and `pnl` is recomputed from the rounded parts so `pnl = proceeds - cost` holds. |

Strict mode is a run option, not a policy field: `run(..., strict=True)`
raises `PriceGap` on the first missing price; the CLI
flag is `--strict` and returns status 1 when a missing price aborts the run.

## Fee treatment

A fee leg is any leg with `fee: true`; it must be negative.

1. `expense` (default): every fee is an expense `Flow` at market value and, when paid in a non-functional asset, a disposal of that asset at market (with its own realized PnL). It never touches the basis or proceeds of the trade it belongs to.
2. `capitalize`: on an event that has trade legs, the fee legs are valued at market and folded into the non-fixing side of the trade. If that side is received (an acquisition, e.g. buying BTC with EUR or opening a perp with USDC) the fee value is added to the basis of the lots opened. If that side is given (a disposal, e.g. selling BTC for EUR or closing a perp) the fee value is deducted from proceeds, and the `Realized` row reports it in `fees`. No `Flow` is emitted for those fees. The fee legs still leave their own lots (a USDC fee reduces the USDC lots at market). Fees on events without trade legs (transfer fees, gas on a plain transfer, funding) remain expenses.

A trade whose fee cannot be priced is left entirely unbooked (`price_gap` plus one `unbooked` per leg), like a trade whose fixing side cannot be priced.

## Liabilities

A `borrow` leg opens a liability worth the market value of what was received; a `repay` leg disposes of the asset given (normal PnL on its lots) and reduces the liability.

The liability is carried at cost until it is repaid (policy 05 rule 9.4; owner, 2026-10-03, decision 24: `liability_valuation` is removed). Repaying realizes the difference between the basis released and the market value repaid as a `Realized` row whose `lots` names the liability id (`quantity` positive like a short cover, `proceeds` negative, `pnl = proceeds - cost`), so borrowing an asset and repaying the same units nets to zero. Its market value before then is portfolio's live layer, never the engine's (rule 39.4).

Accrued unpaid interest is a `borrow` leg labelled `interest`: nothing is received, the liability grows at market value, and the same value is an `interest` expense (rule 9.3.1). A negative one reverses an accrual: the liability releases a proportional share of its basis, which is `interest` income. Interest actually paid is an ordinary `expense` leg labelled `interest` (rule 9.3.2); it does not touch the liability. Interest is never capitalized into the borrowed asset's lots.

## Perpetuals on a settled basis

Policy 05 rule 8: a perpetual's result is booked only when the position is reduced or closed, in the settlement asset; while it is open only its funding and fees are booked. A fill's size leg has tag `position`; it books nothing and changes the **open position** per `(compartment, instrument)`, so positions never net across compartments (`Result.positions`).

1. **`notional` compartments** (Hyperliquid, dYdX, Lighter): the fill's cash leg has tag `notional` and is not booked; it gives the fill price (`-notional / size`) and the settlement asset. `+1 BTC-PERP position`, `-60000 USDC notional`, fee `-30 USDC`. When a fill reduces the position the engine computes realized P&L in the settlement asset under `perp_cost_method` (`average` by default, the venues' method) and books it as `income` or `expense` labelled `realized_pnl`, with `Flow.instrument` naming the position.
2. **`pnl` compartments** (CEX futures, Aster): the size leg carries the venue's fill `price` and `settles_in` (the settlement asset); the position is kept at average entry. The venue's own `realized_pnl` legs are `income` / `expense` legs and are the booked result; the engine realizes nothing itself.
3. **Collateral is untouched by fills**: the settlement asset's lots move only by the realized P&L, funding and fees booked above, so their gains against the reporting currency stay unrealized until spent.
4. **A settlement asset never goes below zero in a perpetual compartment** (a compartment with any `position` leg): an outflow beyond what the compartment holds is owed, a liability `(compartment, asset)` labelled `settlement` at the outflow's market value; a later inflow of that asset into the compartment (income, a trade, a transfer, linked or not) clears it first, realizing the difference between its value and the basis released (rule 8.6). Collateral in other assets is never converted.
5. A fill whose size or cash cannot be read (no notional leg and no price, a notional leg without one size leg of the opposite sign, several size legs against one notional leg) is `unbooked` and the result incomplete, never priced at zero (rule 8.7). So is a fill whose convention or settlement asset differs from its open position's.

Fees on a fill are expenses under either `fee_treatment`: a fill has no `trade` legs to capitalize into.

The legacy booking of fills as served, a `trade` of the position asset against the full notional with the position asset in `position_assets`, still works for callers that send it; policy 05 replaces it (probe 7 shows why: it books FX on the whole notional and disposes of collateral at every fill).
