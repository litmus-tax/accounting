# Versions, migration and extension

Distribution version is 0.13.0. `Ledger.schema_version` accepts `0.2` to `0.13`
and defaults to `0.13`; Result explicitly emits `schema_version:"0.13"`.
Omitting the input version preserves ordinary historical ledgers. New callers
should pin 0.10 and validate against this repo's generated schemas. The package is
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
| `0.9` | Ledger: `Link.kind`, `transfer` (default) or `swap`. A `swap` link (a swap bridge, policy 05 rule 13.5) is booked as a swap: the source's outflow is disposed of at market at the source's time (a `Realized` row on the source event) and the destination's inflow acquired at that value; it needs an outflow and an inflow, else `link_mismatch`. Result: `Move.link` carries `kind`. | Send `kind: swap` for every `swap_bridge` link (policy 01 rule 3.1) instead of a transfer link, which still raises `link_mismatch` across two assets. Results sealed under `0.8` are refused by `parse_result`. |
| `0.10` | Quantities are exact (policy 05 rule 20.3): the lot book adds, subtracts and compares quantities without rounding, and a position is the exact sum of its lots, so a take or a close no longer leaves a residue lot (about 1e-28) of the opposite sign. A take or a change that would leave a holding nonzero but smaller than 1e-18 treats it as zero: the shortfall is discarded, or the crumb's lots go with the take and release their basis with it; new exception code `quantity_residue` (informational, the run stays complete) names the asset, compartment and residue. The ledger is unchanged. | Accept `quantity_residue` wherever exception codes are enumerated, as information, never as an open issue. Lots, realized rows and positions can differ from `0.9` in the last digits where `0.9` rounded, and records `0.9` left unbooked behind a residue ("output requires a non-short holding") are booked. Results sealed under `0.9` are refused by `parse_result`. |
| `0.11` | Opaque positions (policy 05 rule 14, owner, 2026-10-03; interim rule of specs#135). Ledger: `Policy.opaque_compartments` and tag `contents`. Each opaque compartment is one position `position:opaque:<compartment>`, lots of functional-currency units at a cost of 1: a linked transfer in carries the coins' basis into it (a `Move`), a transfer out is a redemption at market value that releases cost up to that value, the excess a `performance` income `Flow` whose asset is the position; `contents` legs track what it holds, and leaving it empty books the cost left as a `performance` expense. No true-up of an open position. Series points value a position from its contents, at least zero. A fee leg in an opaque compartment is paid from inside it: it counts in the contents and books nothing. Any other leg in an opaque compartment is `unbooked`. | Send each opaque compartment (`visibility: opaque` in the unit's `/compartments`) in `opaque_compartments`, and an `opaque_result`'s legs as `contents` legs instead of `performance` income or expense. Expect `performance` flows on `position:opaque:` assets, one line per compartment, and position lots in `lots`. Ledgers without `opaque_compartments` book as before. Results sealed under `0.10` are refused by `parse_result`. |
| `0.12` | Accounting computes realized results, flows and cost only (policy 05 rules 1.5 and 39.9; owner, 2026-10-03, specs#135). Removed: `run(grid=...)`, `Ledger.grid`, `Ledger.max_age`, `Result.series` and the `SeriesPoint` family, `value()` with `Valuation`, `Position` and `LiabilityPosition`, the CLI `value` verb and the `valuation` schema, `Policy.liability_valuation` (decision 24: liabilities are carried at cost until repaid), and the exception codes `series_mismatch` and `invalid_grid`. `TablePricing` is daily: a row prices its own UTC day from its time on and nothing carries forward. Added: `Result.open_rows` (`OpenRow`: holdings, opaque positions, liabilities at carrying value and open positions at entry basis, at the end of the run, term 14) and the exception code `cost_identity`, checked after every atomic group without prices (rule 39.5.1). | Stop sending `grid`, `max_age` and `liability_valuation`, and the prices of holdings: send only the prices events need, one row per (asset, UTC day) the caller resolved (its stale, peg and first-available rules). Read the remaining cost from `open_rows`, and value holdings in the caller (portfolio's live layer, rule 39). Accept `cost_identity` wherever exception codes are enumerated; it makes the result incomplete. Results sealed under `0.11` are refused by `parse_result`. |
| `0.13` | Fees inside an opaque compartment (policy 05 rule 14.14, owner, 2026-10-04). A fee leg in an opaque compartment is a fee expense `Flow` (`fee: true`, its own label) at market value at its time, never capitalised, and the position's remaining cost falls by that value; value beyond the remaining cost is a `performance` gain at the fee's time. The leg still counts in the contents. A fee without a price books nothing, stays in the compartment's `performance`, and raises the new exception code `unpriced_opaque_fee` (informational, the run stays complete) naming the asset, compartment and quantity. The ledger is unchanged. | Price fee legs in opaque compartments like any fee leg. Accept `unpriced_opaque_fee` wherever exception codes are enumerated, as information, never as an open issue. A compartment's `performance` now excludes its reported fees; its total result is unchanged. Results sealed under `0.12` are refused by `parse_result`. |

Distribution 0.10.1 keeps result schema 0.10. Ledger (additive): `events`
takes atomic groups (policy 05 rule 6.3, owner, 2026-10-03): each item is an
array of events (a group) or a bare event (a group of one), so existing flat
ledgers read unchanged as one event per group. A group's events are applied in
order and journalled in that order; shortness is checked only after the whole
group: `negative_position` and `negative_liability` are reported once per
holding or liability whose position is negative after the group, on the last
event of the group that touched it, and a rollover output into a holding the
group itself made short is booked (the short delivered from it) instead of
refused. Under one event per group, an event that goes short and back between
its own legs no longer reports it, and an event short through several legs
reports once instead of once per leg. Consumer impact: send the groups your
policy chooses (portfolio's `atomic_groups`); results of flat ledgers differ
only in those duplicate or transient items.

Distribution 0.8.1 keeps result schema 0.8 and changes only the order within an
event: income legs are booked before transfers, and a linked pair after the
income legs of both its events, so a withdrawal's `performance` leg is
recognised before the transfer takes the units out (policy 05 rule 14.3).
Consumer impact: none in shape; an overdrawn opaque compartment now books
completely once its unit serves the leg (specs#70).

Distribution 0.8.2 keeps result schema 0.8. Ledger (additive): `Leg.basis`
(`unclassified` default, `market`, `carried`), `Leg.cost` and `Leg.acquired` on
unlinked transfers (policy 05 rules 11 and 13.3). Behaviour: functional-currency
transfers are no longer reported as `unmatched_transfer` (rule 13.2). Consumer
impact: book `classify_boundary` corrections as `basis: carried` with the
correction's cost and acquisition time (`ownership_retained`) or `basis: market`
(not retained), and the `unsolicited: zero_cost` option as `basis: carried` with
`cost: 0`; count open unclassified transfers from `unmatched_transfer` as before.

Distribution 0.8.3 keeps result schema 0.8. A rollover with several outputs may
omit every allocation: the engine then allocates the basis by the outputs'
market value at the operation (policy 05 positions rule 5.1), asking and
recording their prices. Consumer impact: stop typing `position_allocations` per
record (policy option, positions gap 3); send multi-output operations without
allocations and supply the outputs' prices (claims priced as the asset they
pay); keep explicit allocations only for a correction that overrides one record,
and for an NFT ownership marker at zero (allocation 0 with the component units
at 1).

Distribution 0.8.4 keeps result schema 0.8: the shape is unchanged. Under
`cost_method: average` a pool is its quantity and total cost only (owner,
2026-10-01; policy 05 rule 18.2): `Lot.origins` and the rollover slices'
`origins` are always empty there, and every pool operation is O(1) (52k
synthetic events: over 580 s before, 7.7 s after). Every other field is
unchanged under `average`, and results under `fifo`, `lifo` and `hifo` are
byte-identical. Consumer impact: none in shape; a reader that shows lot
ancestry shows none under `average`.

## 0.3

1. `Event` adds `depends_on` and optional `rollover`; `Leg.tag` adds `rollover`.
2. `Policy` adds exact `notional_scopes`; the legacy global position list remains.
3. `Lot.origins` and `Result.rollovers` add exact ancestry and operation audit rows.
4. Unknown JSON fields now fail rather than being silently discarded. Correct the
   caller's field names; do not strip unrecognized accounting semantics to pass.
5. Sequential lot IDs for ordinary ledgers retain their behavior. New operations
   create different lots; corrections should reference stable event/operation IDs
   and regenerate selected lot IDs from the same preceding ledger and policy.

The schema command writes the ledger and result schemas from the domain types:

```sh
accounting schema --out build/schema
accounting schema --check build/schema
accounting validate examples/rollover.json --json
accounting --json run examples/rollover.json
.venv/bin/pytest pkg/tests -q
```

`validate` checks structural constraints without prices. `run` reports unbookable
inputs and price gaps in the result and returns zero for completed calculations.
Usage/invalid JSON exits 2; runtime errors and strict price gaps exit 1. The
engine values nothing at market: valuing open receipts and claims is the
caller's (portfolio's live layer), never zero.

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
