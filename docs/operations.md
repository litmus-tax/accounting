# Generic basis operations and claim recognition

## Input ownership and order

An event may contain `rollover` with `inputs:[{leg,lots}]`,
`outputs:[{leg,allocation}]`, `acquisition_date`, `capitalized_costs`,
`cost_reference`, `capitalized_fee_legs` and `write_off`. Indices refer to that
event's `legs`. Every `tag:"rollover"` leg must occur exactly once: negative in
inputs, positive in outputs. A selected lot list overrides the cost method in the
specified order; IDs must exist in the input asset/compartment pool. Duplicate
selected references are rejected. Empty selection uses FIFO/LIFO/HIFO/average.

The input representation is entirely caller supplied. Receipt tokens, claims,
liquidity units and ordinary assets all use opaque asset IDs. An NFT ownership unit
must not be used as fractional liquidity: the caller supplies the independently
measured component asset and units. Accounting never creates both a receipt and its
represented position unless the caller explicitly supplies both.

`depends_on` references event IDs. The engine uses stable topological sorting with
(time, original input index) as priority. A dependency can precede another event at
the same timestamp, but cannot point to a later timestamp. Unknown references,
cycles and duplicate references fail validation. Ledgers without dependencies keep
legacy ordering. Ordinary within-event order remains borrow, trade, transfer,
repay, income/expense, fees. Rollover occurs before ordinary legs; therefore a
same-time trade that creates rollover inputs must be a separate predecessor event.
Unbooked predecessors also block dependent events at execution time.

## Basis conservation and lineage

The engine first validates and consumes a private copy of its lot book. Missing
lots or impossible quantities leave the operation unchanged. Input principal must
be held long; a transformation cannot manufacture short receipts. Functional
currency contributions have known basis equal to quantity and appear as
`cash:<event>:<leg>` consumed audit slices, without fictitious currency lots. A
functional-currency output requires ordinary trade/disposal treatment because cash
cannot carry an arbitrary historical basis.

One output receives all basis without a market price. Multiple outputs require
nonnegative caller-provided fractions summing exactly to one. No market-value or
equal-split allocation is inferred. Each output receives a slice from each consumed
acquisition; receipt units are weighted by contributed basis, preserving effective
lot dates. All-zero basis uses equal unit shares between the consumed slices. A
zero-basis contribution mixed with positive basis retains its zero-cost origin in
the first created slice, and its original quantity remains in the consumed audit.
These are engine allocation conventions, not protocol quantities or investment
performance valuations; callers needing another unit split must supply separate
explicit operations.

`acquisition_date:"carry"` preserves consumed lots' effective dates; `"operation"`
uses the conversion time. Both retain original acquisition dates and event IDs in
`Lot.origins`. Average pools retain every origin despite pooling quantities.
Partial disposals and linked transfers scale/carry that origin basis. The
`Result.rollovers` consumed/created slices connect immediate lot ancestry; recursively
following records and origins reaches original acquisitions.

All decimal division uses the process Decimal context (28 significant digits by
default). For rollover-participating lots, finite sums/subtractions use enough precision
to preserve their exact represented values. Ordinary lots retain the baseline
Decimal operation order, including its ambient-context rounding; this prevents
incidental changes at historical half-cent realization thresholds. The final output receives each input's monetary division
residue. The largest weighted slice receives unit-quantity residue, avoiding loss
of a tiny final contribution. Thus exact finite-decimal sums satisfy:

`basis_out + written_off = basis_in + capitalized_costs`

Audit slices and `origins` remain unrounded even when `minor_unit` rounds display
money on lots/realizations. This allows exact audit conservation without confusing
minor-unit display rounding with a loss of basis. Consumers summing very long
Decimal strings must use sufficient precision; binary float is unsuitable.

## Fees and losses

`capitalized_costs` is an explicit nonnegative functional-currency amount and
requires `cost_reference` when nonzero. It does not imply a payment, accrual or
external price lookup. Caller evidence supports that amount and the treatment of
any accrued portion.

For actual payment in the same event, list negative ordinary fee leg indices in
`capitalized_fee_legs`. The engine values them, disposes their assets once, and
creates no expense flow for those fees. Their aggregate market value cannot exceed
the declared capitalized amount. A missing fee price leaves the entire operation
unbooked. Crypto paid as fees can realize a gain/loss on its disposal, independently
of the operation's carried basis. Other fee legs retain ordinary policy treatment;
do not also emit the same payment as an expense event.

`write_off:true` requires no outputs or capitalized costs. It disposes input units
for zero proceeds and realizes negative released basis. This explicit loss pathway
covers extinguishment without inventing positive proceeds or fake receipts.

## Worked synthetic lifecycle

`examples/rollover.json` contains evidence-labelled illustrative events:

1. `evidence:opening`: buy 10 TOKEN for €1000.
2. `evidence:entry`: carry those units into 100 RECEIPT. No receipt price is needed.
3. `evidence:withdraw-request`: carry 100 RECEIPT into 1 enforceable CLAIM.
4. `evidence:claim-settlement`: dispose CLAIM for €1300. Realized gain is €300.

Run `accounting --json run examples/rollover.json`. The committed expected
result is `pkg/tests/fixtures/rollover.result.json`. No prices are requested and no lots
remain. The records identify both transformations and the settlement's consumed
claim lot. This fixture is synthetic, not observed company NFT or pool history.

For exchange entry replace the entry rollover legs with ordinary trade legs and
provide RECEIPT/EUR at 12: entry realizes €200 and establishes €1200 basis; exit
realizes €100. Pool performance is €100 either way, but the engine does not confuse
that market-value performance input with historical cost.

For receivable-time recognition keep carry entry, replace claim creation with a
trade and provide CLAIM/EUR at 1300. Claim creation realizes €300; unchanged later
settlement realizes zero. A changed settlement value realizes only the incremental
difference. Receipt-time recognition instead carries basis into the claim and
realizes on receipt. Partial claim sales dispose only the sold quantity: selling
0.4 of this claim for €520 releases €400 and realizes €120, leaving €600 basis.

If proceeds are an unpriced asset, the trade remains unbooked and the claim keeps
its €1000 basis with a price-gap item. A request or preview without an evidenced
claim is not sufficient input for any of these pathways. Fee, claim, reward and
acquisition-date choices are software capabilities; company close selects policy.
