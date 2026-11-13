# Positions

How the books treat everything that is not a plain holding of a coin: same-asset moves into staking, earn, lending and vault compartments; receipt positions (aTokens, vault shares, LP tokens); non-fungible positions (concentrated-liquidity NFTs, protocol balances); claims (withdrawal requests, removed liquidity waiting to be collected); and, adding to [policy.md](policy.md), liabilities, perpetuals and opaque compartments.

These are the cases where books go wrong quietly: a principal booked as income, a basis lost in a rollover, a debt that never costs anything. Every rule here has a worked example below, and every example is an engine fixture (`pkg/tests/fixtures/policy05/positions-*.json`). How units detect positions is each unit's own documentation; prices of positions, claims and marks are portfolio's.

## Terms

1. **Position.** Value held in a protocol rather than as a free coin, with its own identity (`position_id`): a supply on Aave, a pool share, a vault share, an NFT liquidity position, a withdrawal queue ticket.
2. **Representation.** How the position appears in balances:
   1. `fungible_receipt`: a token whose quantity is the position (aUSDC, an ERC-4626 share, an LP token, MegaVault shares);
   2. `nft`: a token of quantity 1 whose *contents* change (a Uniswap or PancakeSwap v3 position);
   3. `protocol_balance`: no token at all; the protocol keeps the balance.
3. **Component units.** The measure of an `nft` or `protocol_balance` position that can grow and shrink, for example v3 liquidity units. Books carry the position's basis on its component units, as the asset `position:<position_id>`.
4. **Claim.** A right to receive assets later, created when a position is reduced but not yet paid out: removed v3 liquidity before `collect`, a withdrawal request before it is claimable. Booked as the asset `claim:<claim_id>`, in units of the asset it will pay.
5. **Operation.** One change to a position: `add`, `remove`, `request` (reduce into claims), `settle` (claims paid out), `transform` (one receipt into another). evm serves operations, as `detail.operation`.
6. **Open position.** An open perpetual position, on any venue: tracked for its entry and valued at the mark, but not booked.
7. **Rollover.** Basis carried from what is given to what is received, with nothing valued at market or realized.

## Rules

1. **Same-asset moves are internal.** Moving a coin into or out of a staking, unbonding, earn, lending or vault compartment of the same resource, where the legs net to zero per asset, moves basis and realizes nothing. Rewards arrive as `yield` or `bonus` (income at market value, R7).
   - Covered today: hl staking and lending, hl vault deposits and withdrawals (`liquidity` legs that net to zero), dYdX delegate, undelegate, redelegate and unbonding, cex earn subscriptions and redemptions.

   *Why:* the coin never left the owner; only where it sits changed. Staking is not a disposal in any regime we know of, and treating it as one would realize gains on every delegation.
2. **Positions are declared generically**.
   1. A unit that serves a record changing a position also serves the operation in a shared shape: `{position_id, action, representation, holding?, effects: [{component, delta, claim_id?, asset?}]}`, where `holding` is the receipt's asset key for `fungible_receipt`, and `asset` names what a claim will pay. This promotes evm's `detail.operation` into the contract ([policy 03](../03-unit-contract/contract.md) amendment).
   2. Portfolio books positions from that shape and the legs only. It never reads a kind or `detail` to tell entering from leaving.
   3. A record whose position cannot be assessed (the unit marks it `unassessable`, or a claim has no `asset`) is unbooked with an item, like `unknown`.

   *Why:* the legs alone cannot say which asset is the position: a single-sided vault deposit is one coin out and one share in, symmetric to a withdrawal.
3. **Entering a position.** `add`:
   1. `position_entry: carry` (default): a rollover moves the given assets' basis into the position (the receipt, or the component units), with no P&L. Acquisition dates are carried.
   2. `position_entry: exchange`: a trade; the given assets are disposed of at market value.
   3. For `nft` positions, the NFT itself carries no basis: it is an ownership marker, booked at zero cost. Basis sits on the component units (`position:<position_id>`).

   *Why:* supplying to a pool or a lending market changes the form of a holding, and many regimes do not tax it; others treat it as a swap. The option lets a portfolio follow either. A v3 NFT's quantity is always 1 while its liquidity changes, so its value cannot be measured in NFT units; its component units can.
4. **Leaving a position.** `remove` follows the `position_exit` option (owner, 2026-09-30):
   1. `realize` (default): a trade. The position (receipt units or component units) is disposed of at the market value of what is received, and each received asset opens a lot at its market value.
   2. `carry`: a rollover. The position's basis moves into the received assets, allocated by their market value at the exit (as for claims, below), and nothing is realized. The gain is realized later, when those assets are themselves disposed of.

   *Why:* leaving is when the result of the position is known, so realizing it then is the usual treatment. Some regimes, and some owners, treat getting back the same kind of assets as a change of form only; `carry` serves them. Either way the gain includes pool fees and impermanent loss, which cannot be separated from the legs.
5. **Requests and claims.** `request` reduces a position into claims.
   1. `withdrawal_recognition: receipt` (default): a rollover moves the reduced part's basis into the claims. With several claims, the basis is allocated by each claim's market value at the request (the price of the asset it pays, times its quantity).
   2. `withdrawal_recognition: receivable`: the reduced part is disposed of at the claims' market value; the claims open at that value.
   3. `settle` exchanges claims for the assets paid. Under `position_exit: realize` (default) it is a trade, realizing the difference between the claim's basis and the value received; under `carry` it is a rollover, and the assets received take over the claim's basis.
   4. Anything paid beyond the claims (for example v3 fees collected together with removed liquidity) is income: the unit serves it as `yield` legs.
   5. A claim that will never be paid is written off: a loss equal to its basis.

   *Why:* the time between request and payment can be days (withdrawal queues) or seconds (v3 `decreaseLiquidity` then `collect`), and the owner holds a right, not the coin, meanwhile. Market-value allocation is the standard way to split one basis across several things received, and it needs only prices the book already has.
6. **Receipts accrue by rebasing.** A receipt that grows by itself (aTokens) is income at each accrual cut-off: a `yield` leg on the receipt, opening a lot at market value. Repayment of a debt that grows the same way is `interest` accrued in the liability.
7. **Transforms and transfers.**
   1. `transform` (one receipt wrapped into another, such as a vault share staked into a gauge) is a rollover: basis carries, nothing is realized.
   2. Sending or receiving a fungible receipt is a transfer of that asset like any other; buying or selling one is a trade.
   3. **An `nft` position's component units go wherever the NFT goes.** When the NFT is transferred, the unit serves the position's component units as moving with it, and books move their basis like any transfer. When the NFT is sold or swapped, the component units are what is disposed of, at the sale's value; the NFT's own zero-cost lot moves with them. The unit serves this as an `ownership` effect on the transfer or trade record.
8. **Liabilities** are carried at cost ([policy.md](policy.md#liabilities)). Interest is an expense whether the unit serves it in the liability compartment (accrued: evm debt tokens) or in cash (paid: hl `borrow_lend`, cex `cex.interest`). Liabilities are always valued at market in the series.
9. **Perpetuals** are booked on a settled basis:
   1. every open position is tracked per account and instrument, valued at the mark; it books nothing;
   2. on `notional` compartments the engine computes realized P&L under `perp_cost_method` (`average` by default) when a fill reduces the position; on `pnl` compartments the venue's `realized_pnl` legs are the result;
   3. collateral lots are never disposed of by fills;
   4. liquidation penalties are `fee` legs; a liquidation that closes a position is a fill like any other.
   5. a settlement asset driven below zero (losses, funding or fees settled in an asset other than the collateral) is a liability in that asset, cleared by later inflows of it.
10. **Opaque compartments are positions booked by value** (owner, 2026-10-03; [model.md](model.md#opaque-positions)). Coins sent in leave the holdings and their basis carries into the compartment's position; coins coming back are a redemption at market value; a redemption first recovers the remaining cost, and value beyond it is a gain; at each period end, `as_of` and `opaque_result` the position is trued up to the market value of its event-implied contents, never below zero, which portfolio measures and hands to accounting as a true-up event. Results are `performance`, never split by coin. Compartments valued from event-implied contents at a book's `as_of` are flagged; a compartment no longer reported is observed at zero. Examples 6–10. Until decision 23 is decided, a position still open is not trued up: only examples 8 and 10 change, as their notes say.
11. **Positions are valued by portfolio, with the live prices** (owner, 2026-10-03). Accounting carries them at cost and never values them. A receipt with a market price is valued at it. LP tokens, component units and claims are valued from what they would pay: their underlying amounts at the instant, which the unit's state must serve, priced per policy 06. Until then they are "not valued", never zero, and totals that include them say so.
    1. **A daily snapshot of every receipt held** (owner, 2026-10-03). The unit serves a receipt's underlying amounts at the close (23:59:59Z, read at the last block not after it) of every UTC day the receipt is held at, as well as at period ends and checkpoints. A receipt sold before a day's close has no snapshot that day.
    2. **A day is valued from its own snapshot** (owner, 2026-10-03). On each day, portfolio's valuation, for the live layer and for valued `state?at=` alike, values the receipt from that day's snapshot (the day's last, if there are several): the leaf assets it is a claim on, each at its own price that day, never the receipt's own price. Labelled `components`.
    3. **A missing day carries the last snapshot forward** (owner, 2026-10-03). Only when a day has no snapshot of the receipt (the unit could not read that close, or lists it unread) is the latest snapshot at most `max_stale_days` earlier used, its leaves priced on the day valued. It is marked stale (`components_stale`, with the snapshot's day). With no snapshot in that window the receipt is "not valued", never zero.
    4. **One valuation.** The live layer and valued `state?at=` apply items 2 and 3 with the same window, so a receipt has the same value and label in both at a day's close. Since 2026-10-03 they are one valuation in portfolio, and revisions hold no values.

    *Why:* an LP or vault share is a mix of its pool's coins that changes every day. Snapshots only at period ends left every other day unvalued in `state?at=` while the books carried a value forward, so the two disagreed. A daily snapshot costs a few reads per receipt per day (five for a Stake DAO vault over a two-coin Curve pool). Carrying it forward only covers a day the unit missed.

12. **Receipts are ordinary assets.** A fungible receipt (aToken, vault share, LP token) is an asset like any other once received: it has lots, and it can be bought, sold, transferred, lent and valued as such. The rules on entering, leaving, requests and accrual only govern what happens through the protocol itself: entering, leaving, requests and accrual. So:
    1. selling an LP token on a market disposes of its lots, at the basis they carry (moved in from the underlyings under `carry`, or bought at market);
    2. a receipt bought on a market and later redeemed through the protocol is left like any other position;
    3. a rebasing receipt accrues income at cut-offs however it was acquired.

    *Why:* receipts trade on markets and move between wallets. Modelling them only as positions would lose them the moment they leave the protocol's own flows.

## Worked examples

All in USD with USDC at 1.00, `fifo`, `global` lot scope. Each example is also an engine test. Examples 6–10 are opaque compartments: they take USDT and USDe at 1.00 too, follow the owner's decisions on true-ups and on classifying opaque results ([decisions](decisions.md)), with monthly accrual cut-offs, and use the amounts of the owner's Bitget book, from its 2025 exports, unless they say otherwise.

1. **Aave supply, accrual and withdrawal**.
   1. Supply 1,000 USDC (cost 1,000); receive 1,000 aUSDC. Under `carry`: the aUSDC lot costs 1,000, nothing is realized.
   2. Month end: the balance grows to 1,004 aUSDC. `+4 aUSDC yield`: income 4.00, lot of 4 aUSDC at 4.00.
   3. Withdraw 1,004 aUSDC for 1,004 USDC: disposal of aUSDC, proceeds 1,004.00, cost 1,004.00, gain 0.00.
   4. Result: income 4.00, and 1,004 USDC at cost 1,004.00.
   5. If, instead of withdrawing, the 1,004 aUSDC are sold on a DEX for 0.3 ETH worth 1,050: proceeds 1,050.00 − cost 1,004.00 = 46.00 realized.
2. **A concentrated-liquidity NFT, partly withdrawn**.
   1. Hold 1 ETH (cost 3,000) and 3,000 USDC. Mint a v3 position with both: the NFT (zero cost) and 1,000 liquidity units as `position:X`, cost 6,000.
   2. ETH rises to 3,500. The pool rebalances: the whole position now holds 0.925820 ETH and 3,240.370 USDC, worth 6,480.74, against 6,500 for simply holding the coins (19.26 of impermanent loss).
   3. Decrease liquidity by 400 units (40%), creating two claims: 0.370328 ETH and 1,296.148 USDC, worth 1,296.15 each. The 400 units' basis, 2,400, is allocated by value: 1,200 to each claim. Nothing is realized.
   4. Collect: receive the claims plus fees of 0.01 ETH and 20 USDC. Under `position_exit: realize`, each claim settles for 1,296.15 − 1,200.00 = 96.15 realized. The fees are `yield`: income 35.00 + 20.00.
   5. Result: realized 192.30, income 55.00; the position keeps 600 units at cost 3,600.00. Check: 40% of the position went in at 2,400 and came out worth 2,592.30, which is ETH's rise on the ETH half (+200) less the impermanent loss on 40% (−7.70).
   6. Under `position_exit: carry`, nothing is realized at the collect: 0.370328 ETH and 1,296.148 USDC open at 1,200.00 each, and the 192.30 is realized when they are sold.
3. **A withdrawal request**.
   1. Hold 10 stETH (cost 30,000). Request withdrawal: a claim for 10 ETH. Under `receipt`, the claim costs 30,000 and nothing is realized.
   2. Claimed a week later, with ETH at 3,500: proceeds 35,000.00 − cost 30,000.00 = 5,000.00 realized.
   3. Under `receivable`, with stETH at 3,400 at the request, 4,000 would be realized at the request and 1,000 at the claim.
4. **MegaVault**.
   1. Deposit 10,000 USDC for 9,500 shares: under `carry`, the shares cost 10,000.
   2. Withdraw the 9,500 shares for 10,400 USDC: proceeds 10,400.00 − cost 10,000.00 = 400.00 realized.
5. **Debt, notional and `pnl` perpetuals, a pool, and an opaque account:** see the [guide](guide.md) §4.1–4.5.
6. <a id="opaque-examples"></a>**One grid bot: bot 3**.
   1. 2025-12-01 09:58 UTC: 1,612.5932 USDC goes from `spot` into `spot:strategy`. Its lots leave the holdings, and the position's remaining cost is 1,612.59. Nothing is realized.
   2. 10:03: the bot closes, and 524.6420 USDC and 1,087.7901 USDT come back. Each opens a lot at market value: 1,612.43 of value out. The redemption releases 1,612.43 of cost, and 0.16 remains. Nothing is recognised: value out did not exceed the cost.
   3. Had nothing else gone in: at the 2025-12-31 cut-off the event-implied contents are +1,087.9512 USDC and −1,087.7901 USDT, worth 0.16, equal to the remaining cost, so the true-up recognises nothing, and the 2025 revision flags the compartment. The 2026-01-01 snapshot finds the wallet empty; its `opaque_result` (−1,087.9512 USDC, +1,087.7901 USDT) brings the contents to zero, and the true-up there recognises −0.16 of `performance`. In the book, bot 4 went into the same wallet five minutes later, so the 0.16 stays in the position's cost and nets against bot 4 (example 7).
   4. Per asset (the per-asset booking the owner replaced on 2026-10-03), the same two transfers booked +1,087.25 USDT of performance on 2025-12-01, because USDT came out that never went in, and left 1,087.95 USDC "inside", which the 2026-01-01 snapshot booked as a loss in 2026 together with the other bots' remainders.
7. **All the Bitget bots, 2025-11-25 to 12-19**. All were closed by 12-19; the 2026-01-01 snapshot shows `spot:strategy`, `futures:strategy` and `futures:copytrade` empty.

   | Bot | In (`spot` → `spot:strategy`) | Out | Value out | Cost released | Result | Remaining cost |
   |---|---|---|---|---|---|---|
   | 1 | 11-25: 237.9437 USDT | 11-26: 238.3516 USDT + 0.5719 USDC | 238.92 | 237.94 | +0.98 | 0 |
   | 2 | 11-26: 238.4531 USDT | 11-28: 237.9121 USDT + 0.541 USDC | 238.45 | 238.45 | 0.00 | 0 |
   | 3 | 12-01 09:58: 1,612.5932 USDC | 12-01 10:03: 524.6420 USDC + 1,087.7901 USDT | 1,612.43 | 1,612.43 | — | 0.16 |
   | 4 | 12-01 10:08: 1,302.9646 USDT | 12-04: 631.1894 USDT + 672.1072 USDe | 1,303.30 | 1,303.13 | +0.17 | 0 |
   | 5–6 | 12-06: 500 and 3 USDT | 12-08: 499.0767 USDT + 0.9236 USDC, then 3.9853 USDT + 0.000033956 BTC | 500.00, then about 7.10 | 500.00, then 3.00 | about +4.10 | 0 |
   | 7 | 12-13: 99.75 USDT | 12-19: 49.1191 USDT + 50.8962 USDC | 100.02 | 99.75 | +0.27 | 0 |

   1. `spot:strategy` recognises about +5.52 in 2025. Bot 3's −0.16 is netted against bot 4 because they shared the wallet: one compartment is one position, whatever the bots inside.
   2. `futures:strategy` (USDT only) recognises +1.93 on 12-04 (24.2 in, 26.1289 out), +3.91 on 12-08 (6.07 in, 9.9844 out) and nothing on 12-19 (100 in, 100 out): +5.84.
   3. Total: about +11.4, all in 2025.
   4. True-ups recognise nothing. At the 2025-11-30 and 12-31 cut-offs the remaining cost is zero, and the event-implied contents are worth less than zero: at 12-31, +1,035.0185 USDC, −365.3129 USDT, −672.1072 USDe and −0.000033956 BTC, about −5.5, because the gains taken out are not in them. The value is taken as zero, so the gains recognised at the redemptions stand. The 2026-01-01 snapshot's `opaque_result` (the same amounts with the opposite sign) brings the contents to zero, and with no remaining cost it recognises nothing. `futures:strategy` likewise (about −5.84, taken as zero).
   5. Per asset, the same records booked 1,521.92 EUR of performance in 2025 (+1,087.25 USDT on 12-01, +672.11 USDe on 12-04, and dust) and about −1,500 EUR on 2026-01-01 (−722.34 USDT, −1,036.13 USDC).
   6. Which outflow belongs to which bot (bots 5 and 6 were paired by inference) does not matter: the position needs only the order of the transfers.
   7. In the company's EUR book, each coin carries its EUR basis into the position and each coin received is valued at that day's EUR price, so the results also hold the stablecoins' move against the euro between their purchase and the bot, a real result of holding dollars.
8. **Copy trading** (`futures:copytrade`, USDT; illustrative amounts). The leader's positions outlive any one transfer.
   1. 2025-06-01: 1,000 USDT in. Remaining cost 1,000. At each month end the event-implied contents are 1,000 USDT, worth the cost: nothing.
   2. 2025-12-31: a year-end snapshot (a `snapshot` edit) shows 1,080 USDT. Its `opaque_result` (+80 USDT) is a true-up: +80 in 2025, and the position is carried at 1,080.
   3. 2026-02-10: 500 USDT out. It releases 500 of cost; 580 remains. Nothing is recognised.
   4. 2026-03-15: 650 USDT out. It releases the remaining 580 and recognises +70. At the March cut-off the event-implied contents are −70 USDT, taken as zero: nothing.
   5. 2026-04-01: a snapshot shows the wallet empty. Its `opaque_result` (+70 USDT) brings the contents to zero; with no remaining cost, nothing.
   6. Total +150 = 1,150 out − 1,000 in: +80 in 2025, +70 in 2026. Until the owner decides how a measured value reaches the books (see [decisions](decisions.md)) the year-end snapshot is not a true-up: the 03-15 withdrawal releases the remaining 500 of cost and recognises +150, all in 2026, and the 2025 revision flags the compartment. Without the year-end snapshot, 2025 recognises nothing and flags the compartment, and the +150 is recognised at the 03-15 withdrawal.
   7. For a wallet that holds one coin, this is what the per-asset rule booked: the guide's example (§4.5) gives the same figures under both.
9. **A strategy futures wallet** (`futures:strategy`, USDT). A bot trades perpetuals inside. Its fills, funding and fees are never served, so no open position is tracked (the perpetual rules do not apply), and they are all inside the result.
   1. 24.2 USDT in, 26.1289 out three days later: +1.93 at the redemption (example 7).
   2. Illustrative loss: 100 USDT in, 97 out. The redemption releases 97, and 3 of cost remains; month-end true-ups value the event-implied 3 USDT at the cost, so nothing. The next snapshot shows the wallet empty; its `opaque_result` (−3 USDT) brings the contents to zero, and −3 is recognised there.
10. **A vault** (hl `vault:<address>`, USDC; illustrative amounts).
    1. Deposit 10,000 USDC: remaining cost 10,000.
    2. A vault-equity checkpoint (`userVaultEquities`) at 10,400: an `hl.opaque_result` of +400 USDC, and the true-up there recognises +400; the position is carried at 10,400.
    3. Withdraw the whole equity: 10,250 USDC arrive. The redemption releases 10,250, and 150 of cost remains; the event-implied 150 USDC equal it at the month end.
    4. The next `userVaultEquities` no longer lists the vault, so the unit observes it at zero: an `hl.opaque_result` of −150 USDC, and −150 recognised there.
    5. Total +250 = 10,250 − 10,000. Leader distributions are `yield`, booked apart as before. Until the owner decides how a measured value reaches the books (see [decisions](decisions.md)) the checkpoint at 10,400 is not a true-up: the withdrawal of 10,250 releases the 10,000 of cost and recognises +250, and the vault observed at zero recognises nothing.
