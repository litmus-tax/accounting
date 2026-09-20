# Accounting 0.3 technical reference

This pure engine receives caller-selected facts, policy and prices. It does not fetch
source records, interpret protocols, prove wallet ownership or select company tax
policy. Portfolio owns those decisions and preserves evidence behind event IDs.

1. [Model and ordinary lot behavior](model.md): legs, links, liability identity and JSON.
2. [Basis operations and claims](operations.md): causal order, lineage, capitalization,
   allocations and worked position lifecycles.
3. [Policy and valuation](policy.md): cost methods, pricing and ordinary fees.
4. [Scoped notional accounting](notional.md): compartment eligibility and global pools.
5. [CLI and replay](cli.md): commands and exit behavior.
6. [Migration and extension](migration.md): schema versions, strict validation and fixtures.

The frozen dataclasses in `litmus.accounting.model` define the contract.
Pydantic TypeAdapter provides validation and generates JSON Schema on demand
with `accounting schema ledger`, `result` or `valuation`. Decimals are JSON strings and times are timezone-aware ISO timestamps.
Unknown JSON fields are rejected. Structural cross-field invariants are checked by
`validate`/`run`; a JSON-schema pass alone does not establish a bookable operation.

The pipeline is strict JSON parsing → event/leg/dependency validation → stable
causal order → price lookup and lot/liability booking → monetary rounding. Invalid
operations produce explicit exceptions and `complete:false`; other valid events
continue. A successor of an unbooked dependency remains unbooked. A price gap never
becomes zero or a fabricated holding. `balances` is the original input-leg sum,
including legs left unbooked; compare it with exceptions before claiming coverage.

Original source records are outside this engine's trust boundary. Stable event IDs,
leg indices, lot IDs and cost references connect its audit trail to the caller's
retained records and policy. Replacing a correction means regenerating the selected
ledger and rerunning; no permanent history of intermediate ledgers is required.
For replay retain the selected input, policy, prices, output and source evidence in
the caller's package. This unit's fixtures need no credentials or network access.
