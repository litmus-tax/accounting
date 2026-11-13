# Decisions

Dated decisions that shaped the engine, with what was decided, why, and who decided it. "Owner" is the product owner; "agent, provisional, not reviewed by owner" marks a decision an agent took that the owner has not read. Decisions about how portfolio builds, seals and shows books are in portfolio's [decisions](https://github.com/litmus-tax/portfolio/blob/main/docs/decisions.md). These were recorded in the specs repository (policy 05) until it was retired in November 2026.

## Open

1. **How a measured value reaches the books** (open since 2026-10-03; owner wants more thought). An opaque position is trued up to a value at period ends, `as_of` and each opaque result only once this is decided. Framings: (a) portfolio hands the engine a value for the opaque position and the engine books value − remaining cost as `performance`; (b) a generic `mark` event with a measurement policy per class of holding (`cost` or `fair_value`), which could also serve fair-value debt; (c) portfolio computes the result and hands the engine an amount, which needs lot cost in portfolio and is ruled out by "accounting logic lives only in the engine". Until then results are recognised only at redemptions and when a compartment is observed empty.
2. **Unsolicited receipts at zero cost** (proposed). A `zero_cost` option for `unsolicited`: a lot at zero cost and no income, with any gain realized on disposal, for regimes that tax an unrequested airdrop only on disposal.
3. **A holdings check beside the state check** (owner, 2026-10-04: a follow-up, not decided). Whether the books audit also compares the engine's open-lot quantities at `as_of` per holding with each unit's state. It would catch an event the engine drops, at the cost of a second comparison that overlaps the books-against-live warning.

## Decided

1. **2026-09-26, owner: fees are separate legs, and there are sixteen natures.** Fees, gas and rebates are legs of the record that caused them, never attributes; the nature list is closed so booking has one rule per nature. A wrap is a `trade` until a jurisdiction needs otherwise.
2. **2026-09-26, owner: a venue's realized P&L is a leg only where it moves cash.** On `pnl` compartments it is cash (`realized_pnl` legs); on `notional` compartments it is a `detail` cross-check, never booked, because cash already moved by notional.
3. **2026-09-26, owner: liabilities are negative balances of the owed asset in a `liability` compartment.** It is the only representation under which a generic balance view and the sign identity are right without unit knowledge.
4. **2026-09-29, owner: perpetuals and debt are accounted in the engine.** Portfolio's perp valuation is the venue's view, labelled as such, and never enters a books total.
5. **2026-09-29, owner: perpetuals on a settled basis, with `perp_cost_method` (`average` by default).** Booking notional fills as served books FX on the whole notional and disposes of collateral at every fill.
6. **2026-09-29, owner: one cost method per portfolio; any fiat can be the reporting currency.**
7. **2026-09-30, owner: a negative settlement balance is a liability in the settlement asset,** cleared first by later inflows of it; collateral in other assets is never converted.
8. **2026-09-30, owner: a book at a date that is not an accrual cut-off adds it as one** and re-syncs the accruing resources first, falling back to listing the interest after the last cut-off as not booked.
9. **2026-10-01, owner: every perpetual and futures leg carries its fill price.** A position is wholly determined by size and average entry; a leg without a price is `unknown`.
10. **2026-10-02, owner: the dust line for unsolicited receipts** (`unsolicited_dust`, 1 in the reporting currency), and above it "own transfer, or airdrop?" answered by a link, a tag or `confirm_airdrop`.
11. **2026-10-02, owner: a swap bridge is booked as a swap.**
12. **2026-10-02, owner: the engine's journal is the export's source; export profiles map it** ([journal-export.md](journal-export.md)).
13. **2026-10-03, owner: exact quantities with a 1e-18 net, and atomic groups.**
14. **2026-10-03, owner: an opaque compartment is one position, booked by value,** superseding the per-asset booking decided on 2026-09-30 the same day it was found wrong on the Bitget grid bots. True-ups are at each accrual cut-off, `as_of` and opaque result; every opaque result is one `performance` line, never split by coin.
15. **2026-10-03, owner: accounting computes realized results, flows and cost; portfolio values.** Remaining cost is the open-lot rows at `as_of` (holdings, liabilities at carrying value, open positions' entry basis, opaque positions' remaining cost). The engine serves no cost at other instants; revisions seal no series; the engine alone decides the books' completeness, with a cost identity checked without prices after every group. Liabilities are always at cost (`liability_valuation` removed). Unrealized P&L at `as_of` is the per-holding sum, and its difference from MTM P&L minus realized results is a warning, never folded in.
16. **2026-10-04, owner: fees and rebates the venue reports inside an opaque compartment are fees,** lowering or raising the position's remaining cost; unreported or unpriced ones stay in `performance`.
17. **2026-10-04 (owner-authorised): money is rounded by running total per journal account,** and a liability left owing nothing releases the carrying value left.
18. **2026-10-21, agent, provisional, not reviewed by owner: a sealed revision also states its open-lot rows at each month end it covers,** so a period inside the seal has a balance at cost without a revision of its own. It reverses, for month ends only, the owner's "no cost at other instants" of 2026-10-03, so it is the owner's call.
