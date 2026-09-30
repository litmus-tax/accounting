# Versions, migration and extension

Distribution version is 0.8.0. `Ledger.schema_version` accepts `0.2` to `0.8`
and defaults to `0.8`; Result explicitly emits `schema_version:"0.8"`.
Omitting the input version preserves ordinary historical ledgers. New callers
should pin 0.8 and validate against this repo's generated schemas. The package is
currently in local development.

## Result schema versions

The result schema versions sealed revisions (policy 05 rule 27.2). It changes
whenever the result's shape or the meaning of one of its fields changes, and the
distribution's minor version follows it. `codec.parse_result` reads only the
current version and raises `SchemaVersionError` for any other; a result is never
read best-effort. A revision keeps the version it was sealed with: to read or
replay it, use the engine version it pins; to apply a newer engine, build a new
revision.

| Result schema | Change | Consumer impact |
|---|---|---|
| `0.3` | Rollovers, lot origins, notional scopes. | — |
| `0.4` | Exception code `link_conflict`. `link_mismatch` now leaves its legs `unbooked` and the result incomplete. The engine fixes its own decimal context. | Accept `link_conflict` wherever exception codes are enumerated. Books with a mismatched or doubly linked transfer are now incomplete. Results sealed under `0.3` are refused by `parse_result`. |
| `0.5` | Ledger: `Leg.liability` names a facility's liability compartment. Liabilities are keyed by `(liability compartment, asset)`; `Liability.compartment` and the liability-side `Realized.compartment` are that compartment. Accrued interest (`borrow` labelled `interest`) is an `interest` expense flow, and its reversal `interest` income. Every repay adds a liability-side `Realized` row, under `cost` as under `market`. | Set `liability` on `borrow` and `repay` cash legs to the counter-leg's compartment, and send accrued interest as a `borrow` leg labelled `interest` in the liability compartment, instead of dropping liability legs by name; `policy.loan_interest` becomes unnecessary. Realized P&L now includes the liability side of repayments under `cost`; income and expense totals include accrued interest. |
| `0.6` | Ledger: tags `position` and `notional`, `Leg.price` and `Leg.settles_in`, `Policy.perp_cost_method` (default `average`). Result: `positions` (open `PerpPosition`s) and `Flow.instrument`. In a compartment with `position` legs a settlement asset never goes below zero: the shortfall is a liability labelled `settlement`, cleared by later inflows (new liability-side `Realized` rows). | Map perp fills by the compartment's declared settlement: on `notional`, the size leg as `position` and the cash leg as `notional` (price from the legs, never `detail`); on `pnl`, the size leg as `position` with the fill `price` and `settles_in` (specs#72). Stop sending the notional as `trade` legs with `position_assets`. Send `perp_cost_method`. Read `positions` for open positions and `Flow.instrument` for realized P&L per position (rule 30.3). The venue-basis valuation in `perps.py` stays labelled as the venue's. |
| `0.7` | Result: `journal` (`JournalLine` rows: double entry per event on `holding`, `liability`, `realized`, `income`, `expense`, `external` and `rounding` accounts); exception code `unbalanced`. | Store the journal with the revision and serve it as `rows?kind=journal` and in the reports export (rules 29.3, 31.2, 36). An `unbalanced` exception makes the result incomplete and must be acknowledged to mark a revision final (rule 26.2). |
| `0.8` | `run(grid=...)` and `Ledger.grid`; Result: `series` (`SeriesPoint` with holdings, liabilities at market, open positions at the mark, cumulative realized and flows, total P&L, net assets, contributions); exception codes `series_mismatch` and `invalid_grid`. | Pass the grid of rule 39.1 (every UTC day end from the first record to `as_of`, plus `as_of`) and prices for every held asset and perp mark (instrument in its settlement asset) at each instant; store the series with the revision and serve it as `{P}/books/{rev}/series` (rule 36). A `series_mismatch` makes the result incomplete. |

## 0.3

1. `Event` adds `depends_on` and optional `rollover`; `Leg.tag` adds `rollover`.
2. `Policy` adds exact `notional_scopes`; the legacy global position list remains.
3. `Lot.origins` and `Result.rollovers` add exact ancestry and operation audit rows.
4. Unknown JSON fields now fail rather than being silently discarded. Correct the
   caller's field names; do not strip unrecognized accounting semantics to pass.
5. Sequential lot IDs for ordinary ledgers retain their behavior. New operations
   create different lots; corrections should reference stable event/operation IDs
   and regenerate selected lot IDs from the same preceding ledger and policy.

The schema command writes ledger/result/valuation from the domain types:

```sh
accounting schema --out build/schema
accounting schema --check build/schema
accounting validate examples/rollover.json --json
accounting --json run examples/rollover.json
.venv/bin/pytest pkg/tests -q
```

`validate` checks structural constraints without prices. `run` reports unbookable
inputs and price gaps in the result and returns zero for completed calculations.
Usage/invalid JSON exits 2; runtime errors and strict price gaps exit 1. `value` needs prices for open receipts and claims even when
carry entry needed none. Missing market valuation remains explicit, not zero.

To add a second receipt deployment, the caller supplies a different opaque receipt
asset, observed units and the same operation shape. No engine decoder or protocol
branch is necessary. A nested receipt is another dependent rollover; a pending
claim is another evidenced holding. Add a fixture that conserves every unit and
basis origin, then test missing inputs, ambiguous allocations and unavailable exit
prices. A new source meaning belongs in source specs; a genuinely new accounting
primitive requires a versioned domain/engine extension, generated schemas and
policy tests rather than an undocumented asset-name branch.

The baseline golden files in `pkg/tests/fixtures/baseline/` were captured before changes.
Regression tests remove only additive schema fields and compare every existing
financial field, exception and price record. They cover spot, notional perpetual,
PnL-settled, bridge and loan examples. No provider calls are needed.
