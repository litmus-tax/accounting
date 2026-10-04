# Scoped notional eligibility and liabilities

`Policy.notional_scopes` is a list of exact `{compartment,asset}` pairs. Entries
must be unique and identify an asset/compartment present in the input ledger.
The caller establishes leveraged settlement eligibility from venue facts; the
engine cannot prove the declaration. No wildcard compartment or symbol match is
supported. Perpetual signed instruments can also use scopes. Legacy
`position_assets` remains an explicit global allowance for compatibility.

With compartment lots, scopes affect negative-position eligibility only. With
global lots, each declared scope retains a separate compartment-specific pool;
other assets retain global pooling. This prevents a leveraged settlement short
from consuming unrelated CEX inventory. When scoped eligibility is enabled,
negative checks track actual booked compartment quantities independently of global
cost pooling. Linked transfers update both endpoints once before testing source
negativity, including destination-first observations. The ordinary global costing
convention therefore does not hide an unrelated spot deficit.

Synthetic regressions cover both scopes and a real spot deficit alongside an
eligible margin short. Actual Hyperliquid USDH/USDT0/USDe eligibility must be supplied
by portfolio from retained fill/state evidence. This engine does not identify
settlement assets from symbol names and does not invent conversion transactions.

Liabilities are tracked separately from notional positions. Identity is
`(liability compartment, asset)` regardless of lot scope, the liability
compartment being `Leg.liability` or the leg's own; labels remain informational.
Borrow principal opens inventory and debt at market basis. `borrow` labelled
`interest` increases unpaid debt without receiving inventory and is an interest
expense; repay disposes paid assets, reduces debt and realizes the debt's value
change; until then it is carried at cost. Over-repayment remains an exception and
is not clipped.

The engine retains raw leg balances, including interest accrual legs, for backwards
compatibility. Those balances are not a wallet-state or liability audit. Portfolio
must compare cash, positions and liabilities separately at an aligned checkpoint
and avoid interpreting unpaid interest as received cash. To run a checkpoint,
supply the ledger prefix through that timestamp with required predecessor events.
The same inputs/policy/prices produce deterministic quantities and liability basis.

The public synthetic fixture `pkg/tests/fixtures/synthetic-scoped-settlement.json`
checks three margin settlement assets and two unrelated spot deficits under
both lot scopes. All identifiers, prices and quantities are invented.

A `borrow` leg with exactly `label:"interest"` and `fee:false` may be negative to
reverse evidenced noncash interest already included elsewhere. This is a source
supersession adjustment, not negative borrowing or a cash repayment. The engine
reduces outstanding debt and releases a proportional share of its carried basis,
which is `interest` income (the accrual it reverses was an expense); it creates no
lot or realized gain, and requires no
current market price. Positive interest accrual remains valued at
market as before. A reversal exceeding outstanding debt reports
`negative_liability` and retains the negative quantity/basis rather than clipping
it. Ordinary borrow legs must still be positive.

The caller must provide the evidence and stable event ID for the signed adjustment.
For example, principal100 + checkpoint accrual20 + crystallized interest5 minus
superseded interest5 leaves debt120 and received cash100. The synthetic fixture
`pkg/tests/fixtures/synthetic-signed-debt-accrual.json` tests negative interest
adjustments against invented opening and closing debt quantities. Raw `balances` still sum supplied legs, including
signed noncash interest, so wallet audit callers must exclude those legs explicitly.
