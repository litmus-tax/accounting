# Worked examples

Each example is a ledger under `examples/`; run it with
`accounting run examples/<name>.json` (add `--json` for the full result).
Figures below are what the engine prints.

## 1. Notional perp (`perp.json`)

dYdX-style: the position is an asset, cash moves by `-size × price - fee`,
realized PnL is an output. Policy: FIFO, per-compartment lots, EUR books via
`quote_path: ["USD"]`, `cash: ["USDC", "USD", "EUR"]`.

| Event | Legs |
|---|---|
| `dep` | `+100000 USDC` transfer (unlinked on purpose) |
| `open` | `+1 BTC-PERP`, `-50000 USDC`, fee `-10 USDC` |
| `fund` | `-2.5 USDC` expense `funding` |
| `close` | `-1 BTC-PERP`, `+55000 USDC`, fee `-11 USDC` |
| `wd` → `recv` | `-1000 USDC` at `dydx:0`, `+1000 USDC` at `evm:0xabc`, linked |

Result: realized PnL `4500 EUR` on `BTC-PERP` (`(55000 - 50000) × 0.9`), zero FX
PnL on the USDC lots at a fixed rate, fees `18.9 EUR` and funding `2.25 EUR` as
expenses, one `unmatched_transfer` for the deposit, exit 3. The link moves
`1000 USDC` of basis to `evm:0xabc` (`moves` has one row).

Note that the position asset never needs a price: the USDC side fixes the
trade's value because USDC is in `cash`. Put `BTC-PERP` in `position_assets`
to allow shorts without a `negative_position` exception (the example does not,
because it only goes long).

## 2. Asset-changing bridge (`bridge.json`)

Bridging 0.999 ETH on Ethereum into 99 HYPE on Hyperliquid is not a transfer:
the asset changes, so a link cannot conserve quantity and a carried basis
would be a fiction. It is one **two-leg trade event whose legs sit in
different compartments**:

```json
{"id": "bridge:evm->hl", "time": "2026-02-10T12:00:00Z", "legs": [
  {"asset": "ETH",  "quantity": "-0.999", "compartment": "evm:0xabc", "tag": "trade", "label": "bridge_initiate"},
  {"asset": "HYPE", "quantity": "99",     "compartment": "hl:0xabc",  "tag": "trade", "label": "bridge_claim"}
]}
```

Neither side is cash, so the received side fixes the value: `99 HYPE × 20 EUR
= 1980 EUR`. That is the disposal proceeds of the ETH (basis `0.999 × 1530 =
1528.47`, PnL `451.53`) and the basis of the new HYPE lot in `hl:0xabc`. The
portfolio layer builds this event from the two bridge records (initiate on the
EVM unit, claim on the Hyperliquid unit).

The same ledger shows the other kind of bridge: `100 USDC` leaving Hyperliquid
and `100 USDC` arriving on Ethereum is the **same asset**, so it is a linked
transfer, and the `0.5 USDC` the bridge kept is a separate fee leg on the claim
event (an expense of `0.45 EUR`). Asset identity (is bridged USDC the same
asset as native USDC? is WETH ETH?) is decided by the caller before the ledger
is built; once two ids are equal the bridge is a plain link.

Result: realized PnL `451.50 EUR` (the bridge plus `-0.03` on the gas fee's
ETH), expenses `1.95 EUR`, open lots `99 HYPE` at `1980.00` and `99.5 USDC` at
`89.55`, two `unmatched_transfer` exceptions for the external deposits.
`accounting value examples/bridge.json --at 2026-03-01T00:00:00Z` values the
two lots at `2069.55 EUR` with zero unrealized (the price table has no later
prices).

## 3. PnL-settled venue (`pnl_settled.json`)

A CEX futures account whose export shows cash moving by realized PnL, funding
and commission, and never the position. The engine has no venue concept and
the position is invisible to it; the venue's figures are cash flows in the
settlement asset:

| Event | Legs |
|---|---|
| `cex:dep` | `+1000 USDT` transfer |
| `cex:pnl-1` | `+50 USDT` income `realized_pnl`, fee `-0.4 USDT` `commission` |
| `cex:funding` | `-1.25 USDT` expense `funding` |
| `cex:pnl-2` | `-20 USDT` expense `realized_pnl` |

Result: income `45.00 EUR`, expenses `19.49 EUR` (`0.36 + 1.125 + 18`, rounded
to the cent), one USDT pool of `1028.35` under average cost, no position lot,
no unrealized. Year-end open positions on such a venue are the caller's
problem, not the engine's; the engine only guarantees the cash reconciles.

## 4. Spot with capitalized fees under HIFO (`spot.json`)

Two BTC buys (`10000` and `30000 EUR`, fees `10` and `30`), one sale for
`25000` with a `25` fee, `fee_treatment: capitalize`, `cost_method: hifo`,
`minor_unit: 2`.

- Basis of the lots: `10010` and `30030` (fees capitalized on acquisition).
- HIFO sells the `30030` lot first.
- Proceeds `24975` (`25000 - 25`, the fee deducted; `fees: 25.00` on the row).
- Realized PnL `-5055.00`; no `Flow` rows at all, exit 0.

Under the default `fee_treatment: expense` the same ledger gives PnL `-5000`
on the sale and three expense flows of `10`, `30` and `25`.

## 5. Loan with accrued interest (`loan.json`)

An Aave-style facility: borrow `5000 USDC` against a wallet that holds ETH,
accrue `20 USDC` of unpaid interest, buy `20 USDC` with ETH, repay `5000`.
Policy: FIFO, per-compartment lots, EUR, `liability_valuation: cost`,
`max_age: P90D` on the price table.

| Event | Legs |
|---|---|
| `buy` | `+1 ETH`, `-2000 EUR` |
| `borrow` | `+5000 USDC` borrow `aave-v3`, fee `-0.001 ETH` gas |
| `accrue` | `+20 USDC` borrow labelled `interest` |
| `swap` | `-0.01 ETH`, `+20 USDC` |
| `repay` | `-5000 USDC` repay `aave-v3` |

Result: the borrow opens a `5000 USDC` lot at `4500 EUR` and a liability of
the same size, no income. The accrual grows the liability to `5020 USDC`
(`4518 EUR`) and opens no lot. The repay is a FIFO disposal of the borrowed
lot at `0.95`: PnL `250.00`, plus `-2.00` on the swap's ETH; the liability
releases `4500` of basis and `20 USDC` (`18.00 EUR`) remain owed. Exit 0.
`accounting value examples/loan.json --at 2026-06-01T00:00:00Z` reports the
liability at `19.00 EUR` with `unrealized: null` (under `cost` the liability
has no PnL of its own). Switch the policy to `liability_valuation: market`
and the repay adds a `Realized` row against `liability-1` of `-250.00`,
cancelling the asset's gain; the valuation then shows `unrealized: -1.00`.

`accounting run examples/loan.json --max-age P1D` turns every price into a
gap (the table is monthly), which is what `max_age` is for.
