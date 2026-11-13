# Accounting: a guide

This page teaches double-entry bookkeeping as Litmus applies it. It is written for a reader who knows crypto (wallets, exchanges, perps, lending) but not bookkeeping. The engine's rules are in [policy.md](policy.md) and [model.md](model.md); how portfolio builds and seals books is in portfolio's [books.md](https://github.com/litmus-tax/portfolio/blob/main/docs/books.md). Every worked example below is an engine fixture in `pkg/tests/fixtures/policy05/` (see [examples.md](examples.md)).

## 1. Double-entry basics

1. **An account** is a named bucket that holds one kind of value: "ETH held in my wallet", "USD at Binance", "trading fees paid", "money the owners put in". Books are a set of accounts and a history of changes to them.
2. **Five kinds of account:**
   1. **Assets:** what you have (coins, cash, a claim on an exchange).
   2. **Liabilities:** what you owe (a loan from Aave).
   3. **Equity:** what the owners put in, plus everything earned since.
   4. **Income:** what increased equity through activity (staking rewards, a gain on a sale).
   5. **Expenses:** what decreased it (fees, gas, interest, a loss).

   Income and expenses are equity's detail: at the end of a period they close into equity. Assets = liabilities + equity, always.
3. **Debits and credits** are the two sides of every entry, and nothing more mysterious than "left" and "right".
   1. A **debit** increases an asset or an expense, and decreases a liability, equity or income.
   2. A **credit** does the opposite.
   3. Why two words instead of plus and minus: so that one rule, "debits equal credits", checks every kind of account at once.
4. **Why every movement balances.** Value does not appear or vanish; it moves. Buying 1 ETH for 3,000 USD takes 3,000 out of one account (credit USD) and puts 3,000 into another (debit ETH). If an entry's debits and credits differ, something was lost or invented, and the entry is wrong. That check, applied to every entry, is double entry's whole point.
5. **Cost, not market value.** Assets are carried at what they cost. When the price of ETH moves, nothing is booked: the difference between cost and today's value is **unrealized** P&L, a measurement, not an entry. When you sell, the difference between what you got and what the sold coins cost is **realized** P&L, and that is an entry (income or expense).
6. **The ledger vs. the books.**
   1. The **ledger** is what happened, in quantities: +1 ETH here, −3,000 USD there. In Litmus that is the units' records, and their legs sum to balances that verification checks against the source.
   2. The **books** are the same history valued in one currency under one set of choices (which coins a sale used, whether fees raise cost), with income and expenses recognised. The ledger is fact; the books are fact plus policy.

## 2. How Litmus applies them

1. **Accounts and compartments.** A portfolio has accounts (a Binance account, a wallet on Arbitrum). Each is backed by a unit resource, whose balances live in **compartments**: `spot`, `earn:flexible`, `subaccount:0`, `debt:aave-usdc`. In the books, every `(account, compartment, asset)` holding is an asset account carried at cost; a `liability` compartment is a liability account.
2. **Natures say what a leg is.** Units give every leg a **nature** (`trade`, `fee`, `yield`, `transfer`, …). The books never guess from a record's kind: each nature has one booking rule. A `yield` leg is income; a `fee` leg is an expense; a `trade` leg is half of an exchange.
3. **Internal vs. external movements.**
   1. Moving your own coins from Binance to your wallet is **internal**: the coins, and their cost, just change place. Nothing is realized.
   2. Sending coins to someone else, or receiving them from outside, is **external**: a disposal or an acquisition, at market value.
   3. The two sides of an internal movement are two records in two accounts. Reconciliation **links** them (portfolio's [reconciliation](https://github.com/litmus-tax/portfolio/blob/main/docs/reconciliation.md)); an unlinked transfer is external until you classify it. That is why linking comes before booking.
   4. **Litmus is a sub-ledger** ([journal export](journal-export.md); portfolio's [boundary transfers](https://github.com/litmus-tax/portfolio/blob/main/docs/boundary-transfers.md)). It keeps the crypto books; the company's general ledger (Holded, or the accountant's) keeps the bank, the loans and the equity. An external movement is a **boundary transfer**: money crossing between the tracked accounts and the outside. It is never income or expense. The owner tags its other side with a **counterparty**, named by the owner ("Shareholder (loan)", "Company bank", "My cold wallet") and marked either mine or someone else's. A journal export maps counterparties to the accountant's codes (551 for a shareholder loan, 572 for the bank); untagged or unmapped, it posts to a clearing account (PGC 555) for the accountant to allocate.
4. **Lots and cost basis.**
   1. Each acquisition opens a **lot**: this quantity, acquired at this time, for this cost.
   2. A disposal consumes lots. Which ones is the **cost method**: oldest first (`fifo`), newest first (`lifo`), most expensive first (`hifo`), or one pooled average (`average`).
   3. The cost of the consumed lots is the disposal's **cost basis**. Proceeds minus cost basis is the realized gain or loss.
   4. The **lot scope** says whether lots are pooled per asset across all your accounts (`global`) or kept per compartment (`compartment`). Under `global`, internal transfers do not even need to move lots; under `compartment`, they move at carried cost.
5. **Realized vs. unrealized P&L.** Realized P&L is booked when lots are consumed. Unrealized P&L is computed at an instant for the lots still held (market value − cost) and is never booked. In Litmus the two halves come from two places: the accounting engine books realized results and knows the **remaining cost** of what is still held at its `as_of`; portfolio knows prices, values what the imports say is held every day (the **NAV**, and the result to date, **MTM P&L**, with or without books), and subtracts a revision's realized results to give unrealized P&L. The engine never looks at a price unless an event needs one (§4.7).
6. **Fees.** A fee is an expense at its market value. If you paid it in a coin (a withdrawal fee in ETH, gas), you also disposed of that coin, so its lots are consumed and a small gain or loss is realized. Under the `capitalize` option, a fee on a trade instead raises the cost of what you bought, or lowers the proceeds of what you sold.
7. **Income.** Staking rewards (`yield`), airdrops and program rewards (`bonus`) and funding received are income at market value when received. The received coins open a lot at that value, so a later sale only realizes the change since receipt.
8. **Funding and perpetuals.**
   1. The books treat every perpetual **on a settled basis**: its result is booked only when the position is reduced or closed, as income in the settlement asset. While it is open, it is an **open position**: tracked for its entry and valued at the mark for the P&L chart, but not booked.
   2. On exchanges that settle futures by P&L (**pnl** settlement), the venue computes the result, by average entry, and pays it; that payment is what is booked.
   3. On Hyperliquid and dYdX (**notional** settlement), a fill moves the venue's cash by the full notional, but no P&L is paid. The engine computes the result itself, by average entry by default (`perp_cost_method`).
   4. Funding is income or expense either way.
9. **Debt.** Borrowing 100 USDC is +100 USDC in your wallet (an asset, with a lot) and −100 USDC in a debt compartment (a liability). No income: you are not richer. Interest is an expense, whether you pay it or it accrues onto the debt. Repaying consumes lots of the repaid coin and shrinks the liability.
10. **Revisions.** A book build freezes everything it used (records, links, corrections, policy, prices, engine version) into a sealed, content-addressed **revision** that replays to the byte. Changing the policy never edits a revision; it makes it **stale** and calls for a new one.

## 3. A worked example

One month of activity, in USD (`fifo`, `compartment` lot scope, `expense` fee treatment, USDC as a cash asset).

Prices: ETH 3,000 on the 2nd, 3,200 on the 3rd, 3,500 on the 4th and 5th; USDC 1.00.

1. **Deposit** (1st). 10,000 USD arrives at Binance from the owner's bank.

   | Account | Debit | Credit |
   |---|---|---|
   | Assets: binance · USD | 10,000.00 | |
   | External: the deposit's counterparty (here the owner's bank; untagged, the clearing account) | | 10,000.00 |

   Legs: `+10,000 USD transfer`. It is unlinked (the bank is not a Litmus account); in the reporting currency it needs no classification, because it carries no lots. It is a boundary transfer: the general ledger, not Litmus, says whether it is a contribution, a loan or the company's own cash.
2. **Trade and its fee** (2nd). Buy 1 ETH for 3,000 USD, paying a 3 USD trading fee.

   | Account | Debit | Credit |
   |---|---|---|
   | Assets: binance · ETH (lot 1: 1 ETH, cost 3,000) | 3,000.00 | |
   | Assets: binance · USD | | 3,000.00 |
   | Expenses: fees | 3.00 | |
   | Assets: binance · USD | | 3.00 |

   Legs: `+1 ETH trade`, `−3,000 USD trade`, `−3 USD fee`, all in one record and one engine event. Under `capitalize`, the fee would instead make lot 1 cost 3,003.
3. **Transfer between two accounts, with a fee** (3rd). Withdraw ETH from Binance to your own wallet. Binance takes 0.001 ETH as a withdrawal fee; the wallet receives 0.999 ETH. The two records are linked.

   | Account | Debit | Credit |
   |---|---|---|
   | Assets: wallet · ETH (0.999 ETH moved from lot 1) | 2,997.00 | |
   | Assets: binance · ETH | | 2,997.00 |
   | Expenses: fees (0.001 ETH at 3,200) | 3.20 | |
   | Assets: binance · ETH (0.001 ETH of lot 1, at cost) | | 3.00 |
   | Income: realized gains | | 0.20 |

   The moved coins keep their cost (0.999 × 3,000): an internal move realizes nothing. The fee coin was disposed of, though: worth 3.20, it cost 3.00, so 0.20 is realized.
4. **Sale** (4th). From the wallet, sell 0.5 ETH for 1,750 USDC on a DEX, paying 0.0002 ETH of gas.

   | Account | Debit | Credit |
   |---|---|---|
   | Assets: wallet · USDC (new lot, cost 1,750) | 1,750.00 | |
   | Assets: wallet · ETH (0.5 ETH, cost 0.5 × 3,000) | | 1,500.00 |
   | Income: realized gains | | 250.00 |
   | Expenses: gas (0.0002 ETH at 3,500) | 0.70 | |
   | Assets: wallet · ETH (0.0002 ETH, at cost) | | 0.60 |
   | Income: realized gains | | 0.10 |

   USDC is a cash asset, so it fixes the sale's value: proceeds 1,750.
5. **Where it ends up** (5th, ETH at 3,500).

   | | Quantity | Cost | Market value | Unrealized |
   |---|---|---|---|---|
   | binance · USD | 6,997 | 6,997.00 | 6,997.00 | 0.00 |
   | wallet · ETH | 0.4988 | 1,496.40 | 1,745.80 | 249.40 |
   | wallet · USDC | 1,750 | 1,750.00 | 1,750.00 | 0.00 |

   1. Realized gains: 0.20 + 250.00 + 0.10 = 250.30. Expenses: 3.00 + 3.20 + 0.70 = 6.90. Net income: 243.40.
   2. Check: assets at cost are 6,997.00 + 1,496.40 + 1,750.00 = 10,243.40, which equals what came in from outside (10,000.00) plus net income (243.40). The books balance.
   3. Under `capitalize` instead, the trade fee raises lot 1 to 3,003; the gas on the sale reduces its proceeds to 1,749.30; realized gains are 0.20 + 247.80 + 0.10; and the wallet's ETH costs 1,497.90. The withdrawal fee stays an expense, because it is not on a trade. Same history, different numbers: that is why every figure states its policy.

## 4. More worked examples

Each example stands alone, in USD, with USDC and USDT at 1.00 throughout. Entries show what the [rules](policy.md) require. Every example is also an engine test.

### 4.1 Debt: borrow, accrue, repay

A wallet holds 200 USDC (cost 200) and borrows on Aave. The unit serves the debt as a negative balance in a `liability` compartment, `debt:aave-usdc`.

1. **Borrow 1,000 USDC.** Legs: `+1,000 USDC wallet borrow`, `−1,000 USDC debt:aave-usdc borrow`.

   | Account | Debit | Credit |
   |---|---|---|
   | Assets: wallet · USDC (new lot, cost 1,000) | 1,000.00 | |
   | Liabilities: aave · USDC | | 1,000.00 |

   No income: the wallet holds more, but it owes exactly as much.
2. **Month end: 5 USDC of interest accrues.** Leg: `−5 USDC debt:aave-usdc interest` (the debt grows; nothing leaves the wallet).

   | Account | Debit | Credit |
   |---|---|---|
   | Expenses: interest | 5.00 | |
   | Liabilities: aave · USDC | | 5.00 |

   Interest is an expense when it accrues, not when it is paid.
3. **Repay 1,005 USDC.** Legs: `−1,005 USDC wallet repay`, `+1,005 USDC debt:aave-usdc repay`.

   | Account | Debit | Credit |
   |---|---|---|
   | Liabilities: aave · USDC | 1,005.00 | |
   | Assets: wallet · USDC (lots used first in, first out: 200 + 805, at cost) | | 1,005.00 |

   End: 195 USDC (cost 195), no debt. Equity fell from 200 to 195: exactly the interest.
4. **Borrowing a volatile asset.** Borrow 1 ETH at 3,000; repay the same ETH when it trades at 3,500. The coin you hold gained 500, and your debt grew by 500: the result is zero. The books carry the debt at cost, so nothing is booked until repayment, when both sides are realized. While the price is up, the live layer values the debt at market, so NAV shows the 500 against the 500 the coin gained.

### 4.2 Perpetuals on a notional venue (Hyperliquid, dYdX)

A Hyperliquid perp compartment (`settlement: notional`) holds 10,000 USDC. On a notional venue, a fill moves cash by the full notional and the position is held at size. The books do not follow that: they book perps **on a settled basis**, as if the venue paid out P&L only when you reduce or close.

1. **Open a long: buy 1 BTC-PERP at 60,000, fee 30.** Legs: `+1 BTC-PERP trade`, `−60,000 USDC trade`, `−30 USDC fee`.
   1. The two `trade` legs are not booked. They start an **open position**: 1 BTC-PERP at an entry of 60,000 USDC (cash ÷ size), tracked by the engine but booking nothing.
   2. Only the fee is booked: debit Expenses: fees 30; credit Assets: hl · perp · USDC 30.

   The venue's ledger now shows USDC at −50,030. That is its **notional basis**, which the unit serves and verification checks. The books show the **settled** cash, 9,970, which is also what the venue's own interface calls your balance.
2. **Funding: pay 10 USDC.** Debit Expenses: funding 10; credit USDC 10.
3. **Mark at 61,000 (valuation, no entry).** The open position is worth 1,000 more than its entry: unrealized P&L 1,000. Equity is 9,960 + 1,000 = 10,960.
4. **Close: sell 1 BTC-PERP at 62,000, fee 31.** The fill closes the open position. The engine computes realized P&L in USDC, (62,000 − 60,000) × 1 = 2,000, and books it:

   | Account | Debit | Credit |
   |---|---|---|
   | Assets: hl · perp · USDC (new lot, cost 2,000) | 2,000.00 | |
   | Income: realized P&L (perpetuals) | | 2,000.00 |
   | Expenses: fees | 31.00 | |
   | Assets: hl · perp · USDC | | 31.00 |

   End: 11,929 USDC. Net: 2,000 − 30 − 10 − 31 = 1,929.

Why not book the notional? In USD books with USDC at 1.00 it makes no difference to the numbers. In a EUR book it does. The −50,030 USDC would be a short position in USDC, so every move of USDC against the euro would realize an FX result on the whole notional, offset by an inflated trading result. The collateral would also be "sold" at every fill. Say USDC was bought at 0.90 EUR, is at 0.92 at the open and 0.93 at the close. On a settled basis the trade books 2,000 USDC × 0.93 = 1,860 EUR realized, and the collateral shows 300 EUR unrealized. Booked as served, the same trade shows +2,460 on the position, −500 on the notional and +200 on the collateral: the same 2,160 total, split wrongly ([policy.md](policy.md) probe 7).

**If the position is built and reduced in steps,** the realized P&L depends on which entries a reduction closes. Notional venues don't say, so the books choose: `perp_cost_method`, `average` by default, which is how venues compute their own figure. Hyperliquid's closed P&L (2,000 here) should then match exactly, and serves as a cross-check. A short is the mirror image: selling first opens a negative open position. Positions are per account (`perp:hyperliquid:BTC@<account>`), so a long on one account never nets against a short on another.

### 4.3 Perpetuals on a P&L-settled venue (CEX futures)

A Binance USDⓈ-M futures compartment (`settlement: pnl`) holds 10,000 USDT. Here cash moves only when the venue settles profit or loss.

1. **Open a long: buy 1 BTC at 60,000, fee 24.** Legs: `+1 BTC perpetual trade` (size only) and `−24 USDT fee`. The size leg is not booked, because no cash moved. Only the fee is booked: debit Expenses: fees 24; credit USDT 24.
2. **Funding: pay 10 USDT.** Debit Expenses: funding 10; credit USDT 10.
3. **While open:** the position is tracked as an open position, 1 BTC at an average entry of 60,000, from the fill price. It books nothing, but the P&L chart and holdings value it at the mark: at 61,000, unrealized P&L is 1,000, exactly as in 4.2.
4. **Close at 62,000, fee 24.80.** Legs: `+2,000 USDT realized_pnl` and `−24.80 USDT fee`.

   | Account | Debit | Credit |
   |---|---|---|
   | Assets: binance · futures · USDT (new lot, cost 2,000) | 2,000.00 | |
   | Income: realized P&L (venue-settled) | | 2,000.00 |
   | Expenses: fees | 24.80 | |
   | Assets: binance · futures · USDT | | 24.80 |

   End: 11,941.20 USDT. Net: 2,000 − 24 − 10 − 24.80 = 1,941.20.

Compare with 4.2: the books treat both venues alike. Results are booked only when a position is reduced or closed; while it is open, the P&L chart values the position at the mark. The one difference is who computes the result: the engine on a notional venue (under `perp_cost_method`), the venue itself here, by average entry.

### 4.4 A liquidity pool

A wallet holds 1 ETH (cost 3,000) and 3,500 USDC (cost 3,500). ETH trades at 3,500. It deposits both into a pool and receives 100 LP tokens; later it withdraws.

1. **Deposit.** Legs: `−1 ETH liquidity`, `−3,500 USDC liquidity`, `+100 LP liquidity`. The legs do not net to zero per asset, so this is entering a position, and the `position_entry` option decides it.
   1. `carry` (default): the basis moves into the LP tokens, and nothing is realized.

      | Account | Debit | Credit |
      |---|---|---|
      | Assets: wallet · LP (cost 6,500) | 6,500.00 | |
      | Assets: wallet · ETH | | 3,000.00 |
      | Assets: wallet · USDC | | 3,500.00 |
   2. `exchange`: the deposit is a sale of the ETH and USDC at market value (7,000).

      | Account | Debit | Credit |
      |---|---|---|
      | Assets: wallet · LP (cost 7,000) | 7,000.00 | |
      | Assets: wallet · ETH | | 3,000.00 |
      | Assets: wallet · USDC | | 3,500.00 |
      | Income: realized gains | | 500.00 |
2. **Withdraw, with ETH at 4,000.** The pool has rebalanced: burning 100 LP returns 0.935414 ETH and 3,741.657 USDC, worth 7,483.31, against 7,500 for simply holding the coins (16.69 of impermanent loss). The pool's trading fees, if any, are inside these amounts too. By default, leaving is a disposal of the LP tokens at the value received.

   | Account | Debit | Credit |
   |---|---|---|
   | Assets: wallet · ETH (new lot, cost 3,741.66) | 3,741.66 | |
   | Assets: wallet · USDC (new lot, cost 3,741.66) | 3,741.66 | |
   | Assets: wallet · LP (at cost) | | 6,500.00 (7,000.00 under `exchange`) |
   | Income: realized gains | | 983.31 (483.31 under `exchange`) |

   Over both steps the gain is 983.31 under either option; only its timing differs. It is ETH's rise on the ETH you put in (+500 at the deposit price, +500 more to 4,000) less the impermanent loss (−16.69). Under `position_exit: carry`, the exit realizes nothing instead: the ETH and USDC received take over the LP tokens' basis, split by value.

   LP tokens are ordinary assets once received. Had the wallet sold its 100 LP tokens on a market instead of withdrawing, the sale would dispose of them at their basis (6,500 under `carry`, 7,000 under `exchange`), like any coin.

### 4.5 An opaque account (copy trading)

A Bitget copy-trading wallet is **opaque**: the exports show money moving in and out, but never the trades inside. The books therefore treat the wallet as **one position**, like a share in a fund: what goes in is its cost, what comes out is valued at market, and its value is measured at each month end and at each balance snapshot. Between snapshots the books know the wallet's contents only from the transfers; a snapshot that finds something the transfers do not explain becomes a `cex.opaque_result` record.

1. **Move 1,000 USDT from spot into copy trading.** The USDT leaves the holdings and its cost carries into the position: debit Assets: copytrade position 1,000; credit Assets: spot · USDT 1,000. Nothing is realized.
2. **First snapshot: the wallet holds 1,080.** The unit records `+80 USDT` unexplained, and the position's value is 1,080 against a cost of 1,000: `+80 performance`. Debit Assets: copytrade position 80; credit Income: performance 80.
3. **Second snapshot: it holds 1,050.** `−30 performance`: debit Expenses: performance 30; credit Assets: copytrade position 30.
4. **Move 1,050 USDT back to spot.** A redemption: the USDT arrives at its market value, 1,050, and releases 1,050 of the position's cost, which is now zero.

Net result: +50, recognised at the snapshots. Until the owner settles how a snapshot's value reaches the books, snapshots of a wallet that still holds something recognise nothing: the 1,050 withdrawal releases the 1,000 of cost and recognises the +50 there. The books never see the trades, fees or funding inside, only the position's value. A fee the venue reports inside the wallet is still a fee: it is booked as an expense and lowers the position's cost, so the performance line leaves it out (1,000 in, a 0.80 fee, 1,020 out: fee −0.80, performance +20.80, total +20.00). A rebate the venue reports inside is the mirror: income on the fee line that raises the position's cost (1,000 in, a +0.30 rebate, 1,020.30 out: fees +0.30, performance +20.00, total +20.30). A fee nobody reports, or a fee or rebate that cannot be priced, simply moves the performance by its amount. At a month end with no snapshot, the books value what the transfers say is inside, which leaves out what the trading did since the last snapshot. If a snapshot is missing, one result simply covers a longer interval. If a transfer's other side is unknown, the result may include that money, and the unit says so with an item that the book lists.

**What if more comes out than went in?** Start again: 1,000 USDT in, a snapshot at 1,080 (+80, cost now 1,080), and then 1,200 USDT withdrawn before the next snapshot. The withdrawal first recovers the position's cost, 1,080; the other 120 is a gain at the withdrawal:

| Account | Debit | Credit |
|---|---|---|
| Assets: spot · USDT | 1,200.00 | |
| Assets: copytrade position | | 1,080.00 |
| Income: performance | | 120.00 |

The position's cost is now zero. The transfers now say the wallet holds −120, which cannot be: the books take its value as zero, not as a loss. Whether it still holds something, the next snapshot will say, and its whole value will then be a result. How much of the 1,200 was principal and how much was result nobody can tell, and nothing needs to: the total recognised over the position's life is exactly what came out plus what is left, minus what went in.

**What if a different coin comes out?** A grid bot can take in 1,612.59 USDC and give back 524.64 USDC and 1,087.79 USDT five minutes later. Per coin, that would look like a gain of 1,087 USDT and a loss of 1,088 USDC. As a position it is 1,612.59 in and 1,612.43 out: the 0.16 of cost not recovered is a loss once a snapshot shows the wallet empty, or once a month end values what is left below it ([positions](positions.md#opaque-examples) example 6). The swap inside is not a result; only the value is.

**Period ends.** The year end values the wallet from what the transfers say is inside. Whatever the trading did since the last snapshot is recognised when the next snapshot finds it, in January, although part of it may have been earned in December. The books do not guess a split. The December revision flags the wallet, and the unit suggests uploading a snapshot at the year end, which splits the result.

### 4.6 A real hedge across two venues (the owner's FOLKS positions)

Figures from the owner's portfolio on 2026-09-30 at 10:07 UTC; USDC and USDT are taken at 1.00 USD for readability. The same token is held long on one venue and short on another.

1. **Aster** (`settlement: pnl`, multi-asset margin). Collateral 399.90 USDC. Long 241.3 FOLKS at an average entry of 2.420397. Fees, funding and the realized P&L of partial closes settle in **USDT**, and have taken it to −2.735981 USDT.
   1. The FOLKS size legs book nothing; the position is an open position at the venue's average entry.
   2. The −2.74 USDT is not a negative lot: it is a liability of 2.74 USDT, because the venue settled losses in an asset the account does not hold. Debit the fees, funding and realized loss as expenses; credit Liabilities: aster · perp · USDT. The USDC collateral is untouched.
   3. At the mark of 2.606326: unrealized P&L +44.86.
2. **Lighter** (`settlement: notional`). Short 238 FOLKS at 2.425009. Settled cash 496.44 USDC.
   1. The fills' notional cash legs are not booked; they feed the open position, whose realized P&L the engine computes under `perp_cost_method: average` when it is reduced. The books' cash is the settled cash, 496.44, never the notional 1,073 the unit serves.
   2. At the mark of 2.616800: unrealized P&L −45.65.
3. **Together.** Two open positions, one per account and instrument, never netted: +44.86 and −45.65, −0.78 combined. The P&L series shows both; its check holds at each point because the USDT liability is valued at market alongside the collateral and the two positions' unrealized P&L.

This example is the engine fixture `guide-4.6-folks-hedge.json`.

### 4.7 Realized in accounting, unrealized in portfolio

A USD book, `fifo`. One lot is bought, its price moves, and part of it is sold. The example shows which part of the system computes which number (owner, 2026-10-03: accounting books realized results and cost; portfolio values).

ETH closes (UTC day ends): 3,050 on 1 March, 3,300 on 2 March, 3,500 on 3 March; the price source has no close for 4 March.

1. **1 March.** 6,000 USD arrives at the exchange from the owner's bank (a boundary transfer: a contribution of 6,000), and buys 2 ETH for 6,000 USD.
2. **3 March, 14:00.** Sell 0.5 ETH for 1,800 USD.

**The live layer (portfolio, no books).** From the imported balances and the closes, portfolio values every day, labels each value, and computes MTM P&L. It needs no revision and no engine:

| Day end | ETH held | ETH price, label | ETH value | USD held (`market`) | Live NAV | Net contributions | MTM P&L |
|---|---|---|---|---|---|---|---|
| 1 March | 2 | 3,050, `market` | 6,100.00 | 0.00 | 6,100.00 | 6,000.00 | +100.00 |
| 2 March | 2 | 3,300, `market` | 6,600.00 | 0.00 | 6,600.00 | 6,000.00 | +600.00 |
| 3 March | 1.5 | 3,500, `market` | 5,250.00 | 1,800.00 | 7,050.00 | 6,000.00 | +1,050.00 |
| 4 March | 1.5 | 3,500, `stale` (3 March) | 5,250.00 | 1,800.00 | 7,050.00 | 6,000.00 | +1,050.00 |

On 4 March the source has no close, so portfolio carries 3 March's price forward and labels it `stale`, with the day of the price. With no price within `max_stale_days`, ETH would be a gap that day: NAV and MTM P&L not computed, the day marked incomplete.

**The books (accounting, sealed).** Both events are valued by their USD side, so the engine needs no price at all. It books lot 1 (2 ETH, cost 6,000) on 1 March. The sale consumes 0.5 ETH of lot 1: proceeds 1,800.00, cost 0.5 × 3,000 = 1,500.00, **realized +300.00**, dated 3 March. A revision with `as_of` 4 March seals that. Its realized series (no income or expenses here) and its open-lot rows at `as_of`:

| Day | Realized that day | Realized to date |
|---|---|---|
| 1 March | 0.00 | 0.00 |
| 2 March | 0.00 | 0.00 |
| 3 March | +300.00 | +300.00 |
| 4 March | 0.00 | +300.00 |

| Open lot at `as_of` | Quantity | Remaining cost |
|---|---|---|
| ETH, lot 1 | 1.5 | 4,500.00 |
| USD | 1,800 | 1,800.00 |

Its cost identity holds after every event with no price: 4,500.00 + 1,800.00 = 6,000.00 contributed + 300.00 realized.

**Unrealized P&L over time (joined on read, never booked)** = live MTM P&L − realized to date:

| Day end | Live MTM P&L | Realized to date | Unrealized |
|---|---|---|---|
| 1 March | +100.00 | 0.00 | +100.00 |
| 2 March | +600.00 | 0.00 | +600.00 |
| 3 March | +1,050.00 | +300.00 | +750.00 |
| 4 March | +1,050.00 | +300.00 | +750.00 |

**Per holding at `as_of`** (4 March) = live value − remaining cost of the open lots:

| Holding | Live value, label | Remaining cost | Unrealized |
|---|---|---|---|
| ETH | 5,250.00, `stale` (3 March) | 4,500.00 | +750.00 |
| USD | 1,800.00, `market` | 1,800.00 | 0.00 |

1. The sale moved 300 from unrealized into realized: on 2 March the whole 600 was unrealized; on 3 March 300 is realized, and the 1.5 ETH still held carry 750 more. Accounting never saw 3,050, 3,300 or 3,500.
2. **Cross-check at `as_of`:** the books' total P&L, 300.00 realized + 750.00 per-holding unrealized = 1,050.00, equals the live MTM P&L. A record the books did not book would show as a difference here, served as a warning and never folded into unrealized P&L.
3. If the 3 March close were later corrected to 3,480, the live layer would recompute: NAV 7,020.00, MTM P&L +1,020.00, and, reading the same revision, unrealized +720.00 (per holding: 5,220.00 − 4,500.00). The revision itself does not change: realized 300.00 and the open lots' 6,300.00 are sealed and replay exactly.
4. Per-holding unrealized P&L on 2 or 3 March is not served: it would need the open lots of those days, which is left to a later analytics pass.

## 5. Where you see it

1. **Home:** one P&L (the live mark-to-market result) with its breakdown: realized P&L, income and expenses from the live books, unrealized P&L per holding, and what is not in the books, by cause. The lines add up to the P&L.
2. **Activity:** each row shows its accounting from the live books: for a sale, proceeds, cost basis, gain and the lots used; for a transfer, the basis it carried; for a record the books could not book, why.
3. **Charts:** NAV and MTM P&L from portfolio's live layer, with or without books; realized results and income by period from the books, and unrealized P&L from the two joined on read (§4.7).
4. **Books:** the seal timeline, the open issues of the books at a chosen date, "Seal up to", and the exports of each seal: the accountant's summary workbook, CSV reports (disposals, income and expenses, holdings, liabilities, movements with outside parties, journal), Koinly's CSV and the sealed package.
5. **CLI:** `litmus pnl`, `litmus totals`, `litmus books live | build | list | show | rows | series | export | replay | final`.

Every one of these states whether it reads the live books or a sealed revision, and the policy it was computed under.
