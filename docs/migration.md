# Version 0.3 migration and extension

Distribution version is 0.3.0. `Ledger.schema_version` accepts `0.2` or `0.3` and
defaults to `0.3`; Result explicitly emits `schema_version:"0.3"`. Omitting the
input version preserves ordinary historical ledgers. New callers should pin 0.3
and validate against this repo's generated schemas. The package is currently in local development.

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
