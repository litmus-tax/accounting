# Journal export examples

Three export profiles worked on the owner's real data: "Holded monthly", "Auditor detail" and a custom CEL format (the owner's sheet). The design they illustrate is [journal-export.md](journal-export.md); export profiles and the journal export are not built yet, so these examples are the reference for building them.

**Every figure here is illustrative** (owner reviewed, 2026-10-02): it comes from unsealed, incomplete books of 2026-10-01, not from a final revision. The layouts, the fields and the expressions are what the examples fix.

## How the figures were made

1. **Events:** the inputs of the portfolio's latest revision on the local stack (`62b21da1…`, sealed 2026-10-01, portfolio `personal`, "Tribulnation Labs"), read with `GET {P}/books/{rev}/export`. Its history starts with the shareholder loan on 2025-11-06. Only events before 2026-01-01 Madrid time are used.
2. **Engine:** `litmus-accounting` at [`5eef60a`](https://github.com/litmus-tax/accounting/tree/5eef60ac1e3b82ecd22dd754d066d6c4f2459e13), run offline. That revision is in USD, so the ledger was restated in EUR: `functional_currency: euro`, quote path `usd`, with daily ECB USD→EUR reference rates (via frankfurter.dev) because the revision's price table has EUR rates only from 2026. The real FX source is the pricing rules'.
3. **Tags:** the two receipts of the shareholder loan are tagged with the counterparty "Marcel (loan)" (`marcel-loan`, someone else), as the owner would tag them. The profiles' chart maps it to `551` "Préstamo socio (Marcel)". No counterparty or tag is recorded yet. The only other boundary transfers booked in the two months are two 0.00001 USDC receipts on 2025-12-01. They are untagged, so they are warnings and would post to the clearing account (555), but they are worth 0,00 € and produce no line.
4. **Incomplete:** the run reports 19 price gaps (aEURC at its 1 December accrual, MXPOINT fees, Curve LP shares), 20 unbooked legs and 101 negative-position exceptions on MEXC USDT. The peg fallback for prices was not implemented then.
5. **Mapping:** done by a throwaway script that implements the categories, asset classes, journal mapping and timezone rules of [journal-export.md](journal-export.md) with the "Spain PGC: crypto as inventories" preset. The custom format of example 3 was run with the real CEL library that cex uses (`common-expression-language`), plus the two formatting helpers below. Every event balances, and so does each month.

## The journal line, as CEL sees it

Every format, built-in or custom, reads the same line. A custom column is a CEL expression over `line`:

| Field | CEL type | Event level | Month level |
|---|---|---|---|
| `entry` | int | the entry's number in the file | the month's entry number |
| `date` | string, RFC 3339 with offset | the event's time in the profile's timezone | the month's last second |
| `period` | string | `2025-11` | `2025-11` |
| `level` | string | `event` | `month` |
| `account` | string | the code, padded to the chart's width | the same |
| `account_name` | string | from the chart | the same |
| `categories` | list(string) | the categories summed into the line (one, usually) | every category netted into the line |
| `debit`, `credit` | string, decimal with `.`, 2 places | the side's amount; the other is `0.00` | the net amount on its side |
| `description` | string | the record's kind and account | the profile's template |
| `note` | string | the tag's note, on a boundary line | empty |
| `reference` | string | the tag's reference, else the counterparty's, on every line of a tagged event | empty |
| `counterparty`, `counterparty_label` | string | the tag's counterparty id and label, on the boundary line; empty when untagged | the same, on the counterparty's line |
| `tracked_account` | string | the Litmus account | empty |
| `compartment`, `asset`, `quantity` | string | when the line is one leg; else empty | empty |
| `sources` | list(string) | the event's record ids | the events summed |
| `revision`, `profile` | string | the revision id; the profile id and version | the same |

Functions: the CEL standard library of cex (`join`, `split`, `substring`, `replace`, `startsWith`, `exists`, …) and its exact decimal helpers (`add_decimal`, `sub_decimal`, `mul_decimal`, `div_decimal`, `parse_decimal`), plus two formatting helpers:

1. `format_date(date, pattern)`: the date in the profile's timezone, by a `strftime` pattern. A pattern without `%z` drops the offset. That is the format's explicit choice, recorded in the profile.
2. `format_decimal(amount, decimal_mark, group_mark)`: `format_decimal("15401.08", ",", ".")` is `15.401,08`.

## Example 1: profile "Holded monthly"

The built-in `holded` format, the "Spain PGC: crypto as inventories" chart at 8 digits, Europe/Madrid, and monthly aggregation.

1. **The profile as stored** (`GET {P}/books/profiles/holded-monthly`):

   ```json
   {
     "id": "holded-monthly",
     "name": "Holded monthly",
     "version": 1,
     "chart": {
       "preset": "es-pgc-inventories",
       "code_width": 8,
       "counterparties": {
         "marcel-loan": {
           "code": "551",
           "name": "Préstamo socio (Marcel)"
         },
         "company-bank": {
           "code": "572",
           "name": "Bancos"
         }
       },
       "overrides": {},
       "remap": {}
     },
     "timezone": "Europe/Madrid",
     "aggregation": {
       "level": "month",
       "description": "Operaciones {month} {year}",
       "language": "es"
     },
     "format": {
       "builtin": "holded"
     }
   }
   ```

2. **The generated rows** of Holded's *libro diario* template, November and December 2025 (illustrative). The columns follow the template's order: entry number, date, account name, account number, debit, credit, description, tags, notes, entry type, dotted. The date column takes a calendar date, as the template asks, and the notes carry the timestamp with its offset. Entry type is blank, which Holded reads as an ordinary entry. Amounts use a decimal comma and no thousands separator.

   | Entry | Date | Account name | Account number | Debit | Credit | Description | Tags | Notes | Entry type | Dotted |
   |---|---|---|---|---|---|---|---|---|---|---|
   | 1 | 30/11/2025 | Criptomonedas | 30000000 | 1840,74 | 0 | Operaciones noviembre 2025 | | 2025-11-30T23:59:59+01:00 | | 0 |
   | 1 | 30/11/2025 | Stablecoins | 30010000 | 15401,08 | 0 | Operaciones noviembre 2025 | | 2025-11-30T23:59:59+01:00 | | 0 |
   | 1 | 30/11/2025 | Deuda DeFi | 52100000 | 0 | 6928,76 | Operaciones noviembre 2025 | | 2025-11-30T23:59:59+01:00 | | 0 |
   | 1 | 30/11/2025 | Préstamo socio (Marcel) | 55100000 | 0 | 10032,26 | Operaciones noviembre 2025 | | 2025-11-30T23:59:59+01:00 | | 0 |
   | 1 | 30/11/2025 | Comisiones | 62600000 | 3,98 | 0 | Operaciones noviembre 2025 | | 2025-11-30T23:59:59+01:00 | | 0 |
   | 1 | 30/11/2025 | Resultado trading | 76900000 | 0 | 260,06 | Operaciones noviembre 2025 | | 2025-11-30T23:59:59+01:00 | | 0 |
   | 1 | 30/11/2025 | Rendimientos | 76910000 | 0 | 3,24 | Operaciones noviembre 2025 | | 2025-11-30T23:59:59+01:00 | | 0 |
   | 1 | 30/11/2025 | Ingresos excepcionales | 77800000 | 0 | 21,48 | Operaciones noviembre 2025 | | 2025-11-30T23:59:59+01:00 | | 0 |
   | 2 | 31/12/2025 | Criptomonedas | 30000000 | 368,76 | 0 | Operaciones diciembre 2025 | | 2025-12-31T23:59:59+01:00 | | 0 |
   | 2 | 31/12/2025 | Stablecoins | 30010000 | 0 | 421,27 | Operaciones diciembre 2025 | | 2025-12-31T23:59:59+01:00 | | 0 |
   | 2 | 31/12/2025 | Comisiones | 62600000 | 2,53 | 0 | Operaciones diciembre 2025 | | 2025-12-31T23:59:59+01:00 | | 0 |
   | 2 | 31/12/2025 | Intereses de deudas | 66240000 | 20,89 | 0 | Operaciones diciembre 2025 | | 2025-12-31T23:59:59+01:00 | | 0 |
   | 2 | 31/12/2025 | Resultado trading | 66900000 | 33,53 | 0 | Operaciones diciembre 2025 | | 2025-12-31T23:59:59+01:00 | | 0 |
   | 2 | 31/12/2025 | Rendimientos | 76910000 | 0 | 3,33 | Operaciones diciembre 2025 | | 2025-12-31T23:59:59+01:00 | | 0 |
   | 2 | 31/12/2025 | Ingresos excepcionales | 77800000 | 0 | 1,11 | Operaciones diciembre 2025 | | 2025-12-31T23:59:59+01:00 | | 0 |

   The Excel file also has a sheet `profile` holding the profile above, so it documents itself. Each month balances: November 17.245,80 on each side, December 425,71. The 769/669 pair is netted into one line: a gain of 260,06 in November and a loss of 33,53 in December. The 778/678 pair nets to 21,48 and 1,11.
3. **`sources.csv`** for one aggregate line, November's `55100000`:

   ```
   entry,code,event,debit,credit
   1,55100000,evm:ethereum:0x60ffe08b24dd61ec77080d6b60d86bde5333f01e:0x5885a011f0e43700625aae9c28950e521ed9b56686e2fb6f257dc72d733cccd8:value,0.00,27.99
   1,55100000,evm:ethereum:0x60ffe08b24dd61ec77080d6b60d86bde5333f01e:0xe469aef82c5f1940cb1627c8d35d3a8fabf2e1e8d70dde031fde07541b5f9c59:log/934,0.00,10004.27
   ```

4. **One line as CEL sees it** (November, `55100000`, the counterparty "Marcel (loan)"):

   ```json
   {
     "entry": 1,
     "date": "2025-11-30T23:59:59+01:00",
     "period": "2025-11",
     "level": "month",
     "account": "55100000",
     "account_name": "Préstamo socio (Marcel)",
     "categories": [
       "boundary"
     ],
     "debit": "0.00",
     "credit": "10032.26",
     "description": "Operaciones noviembre 2025",
     "note": "",
     "reference": "",
     "counterparty": "marcel-loan",
     "counterparty_label": "Marcel (loan)",
     "tracked_account": "",
     "compartment": "",
     "asset": "",
     "quantity": "",
     "sources": [
       "evm:ethereum:0x60ffe08b24dd61ec77080d6b60d86bde5333f01e:0x5885a011f0e43700625aae9c28950e521ed9b56686e2fb6f257dc72d733cccd8:value",
       "evm:ethereum:0x60ffe08b24dd61ec77080d6b60d86bde5333f01e:0xe469aef82c5f1940cb1627c8d35d3a8fabf2e1e8d70dde031fde07541b5f9c59:log/934"
     ],
     "revision": "illustrative",
     "profile": "holded-monthly@1"
   }
   ```

5. **Against the owner's sheet.** Same layout, different figures:

   | Account | Sheet, November | Litmus, November (illustrative) | Sheet, December | Litmus, December (illustrative) |
   |---|---|---|---|---|
   | Stablecoins | 15.387,74 Dr | 15.401,08 Dr | 329,29 Cr | 421,27 Cr |
   | Criptomonedas | 1.534,97 Dr | 1.840,74 Dr | 379,08 Dr | 368,76 Dr |
   | Préstamo socio (Marcel) | 10.019,09 Cr | 10.032,26 Cr | — | — |
   | Deuda DeFi (USDe) | 6.968,22 Cr | 6.928,76 Cr | — | — |
   | Trading | 60,15 Dr (loss) | 260,06 Cr (gain) | 63,32 Cr (gain) | 33,53 Dr (loss) |
   | Comisiones | 4,45 Dr | 3,98 Dr | 13,53 Dr | 2,53 Dr |
   | Intereses de deudas | — | — | — | 20,89 Dr |
   | Rendimientos | — | 3,24 Cr | — | 3,33 Cr |
   | Ingresos excepcionales | — | 21,48 Cr | — | 1,11 Cr |
   | Total | 16.987,31 | 17.245,80 | 392,61 | 425,71 |

   The differences are leads to investigate before any acceptance, not conclusions:
   1. **The loan:** Litmus values the ETH at 27,99 € and the EURC at 10.004,27 €, against the sheet's 23,00 € and 9.996,09 €. The sheet appears to take EURC at 1,00 €.
   2. **The USDe debt:** the 8.000 USDe borrowed are 6.928,76 € at the ECB rate, against the sheet's 6.968,22 €.
   3. **Trading:** the sign is opposite in both months. In a EUR book, USDT and USDe trades realize the dollar's move against the euro, and the MEXC exceptions (item 4 above) leave part of November unbooked.
   4. **Accrued interest** on the USDe debt (24,35 USDe on 1 December) and **yield** on aEURC and aUSDT are in Litmus but not in the sheet.

## Example 2: profile "Auditor detail"

The built-in `journal` format (neutral CSV), the same chart, Europe/Madrid, one entry per event.

1. **The profile as stored:**

   ```json
   {
     "id": "auditor-detail",
     "name": "Auditor detail",
     "version": 1,
     "chart": {
       "preset": "es-pgc-inventories",
       "code_width": 8,
       "counterparties": {
         "marcel-loan": {
           "code": "551",
           "name": "Préstamo socio (Marcel)"
         },
         "company-bank": {
           "code": "572",
           "name": "Bancos"
         }
       }
     },
     "timezone": "Europe/Madrid",
     "aggregation": {
       "level": "event"
     },
     "format": {
       "builtin": "journal"
     }
   }
   ```

2. **The first 15 lines of the file**, verbatim (illustrative):

   ```
   entry,date,code,account_name,debit,credit,description,reference,sources,revision
   1,2025-11-06T13:11:35+01:00,30000000,Criptomonedas,27.99,0.00,evm.transfer · ethereum1,loan agreement 2025-11,evm:ethereum:0x60ffe08b24dd61ec77080d6b60d86bde5333f01e:0x5885a011f0e43700625aae9c28950e521ed9b56686e2fb6f257dc72d733cccd8:value,illustrative
   1,2025-11-06T13:11:35+01:00,55100000,Préstamo socio (Marcel),0.00,27.99,evm.transfer · ethereum1,loan agreement 2025-11,evm:ethereum:0x60ffe08b24dd61ec77080d6b60d86bde5333f01e:0x5885a011f0e43700625aae9c28950e521ed9b56686e2fb6f257dc72d733cccd8:value,illustrative
   2,2025-11-06T13:14:23+01:00,30010000,Stablecoins,10004.27,0.00,evm.transfer · ethereum1,loan agreement 2025-11,evm:ethereum:0x60ffe08b24dd61ec77080d6b60d86bde5333f01e:0xe469aef82c5f1940cb1627c8d35d3a8fabf2e1e8d70dde031fde07541b5f9c59:log/934,illustrative
   2,2025-11-06T13:14:23+01:00,55100000,Préstamo socio (Marcel),0.00,10004.27,evm.transfer · ethereum1,loan agreement 2025-11,evm:ethereum:0x60ffe08b24dd61ec77080d6b60d86bde5333f01e:0xe469aef82c5f1940cb1627c8d35d3a8fabf2e1e8d70dde031fde07541b5f9c59:log/934,illustrative
   3,2025-11-06T13:16:23+01:00,62600000,Comisiones,0.49,0.00,evm.execution · ethereum1,,evm:ethereum:0x60ffe08b24dd61ec77080d6b60d86bde5333f01e:0x32c049c9379c5553090c35bf5b86759c5ca687d326dffbc5df5b3056c90ea8d6:tx,illustrative
   3,2025-11-06T13:16:23+01:00,30000000,Criptomonedas,0.00,0.49,evm.execution · ethereum1,,evm:ethereum:0x60ffe08b24dd61ec77080d6b60d86bde5333f01e:0x32c049c9379c5553090c35bf5b86759c5ca687d326dffbc5df5b3056c90ea8d6:tx,illustrative
   4,2025-11-06T13:19:59+01:00,62600000,Comisiones,0.55,0.00,evm.execution · ethereum1,,evm:ethereum:0x60ffe08b24dd61ec77080d6b60d86bde5333f01e:0xd3022f84348a9a1fc2ce7d3c3a600680a3a5320c13725d0ec7e0195cad35fff0:tx,illustrative
   4,2025-11-06T13:19:59+01:00,30000000,Criptomonedas,0.00,0.55,evm.execution · ethereum1,,evm:ethereum:0x60ffe08b24dd61ec77080d6b60d86bde5333f01e:0xd3022f84348a9a1fc2ce7d3c3a600680a3a5320c13725d0ec7e0195cad35fff0:tx,illustrative
   5,2025-11-06T13:19:59+01:00,30010000,Stablecoins,10004.27,0.00,evm.liquidity · ethereum1,,evm:ethereum:0x60ffe08b24dd61ec77080d6b60d86bde5333f01e:0xd3022f84348a9a1fc2ce7d3c3a600680a3a5320c13725d0ec7e0195cad35fff0:liquidity/718,illustrative
   5,2025-11-06T13:19:59+01:00,30010000,Stablecoins,0.00,10004.27,evm.liquidity · ethereum1,,evm:ethereum:0x60ffe08b24dd61ec77080d6b60d86bde5333f01e:0xd3022f84348a9a1fc2ce7d3c3a600680a3a5320c13725d0ec7e0195cad35fff0:liquidity/718,illustrative
   6,2025-11-06T13:26:59+01:00,62600000,Comisiones,1.07,0.00,evm.execution · ethereum1,,evm:ethereum:0x60ffe08b24dd61ec77080d6b60d86bde5333f01e:0xc194f7cb882b288f9348fd0d78f3336e9dfcd1791b60edee2eb9facc0ae1c6c8:tx,illustrative
   6,2025-11-06T13:26:59+01:00,30000000,Criptomonedas,0.00,1.07,evm.execution · ethereum1,,evm:ethereum:0x60ffe08b24dd61ec77080d6b60d86bde5333f01e:0xc194f7cb882b288f9348fd0d78f3336e9dfcd1791b60edee2eb9facc0ae1c6c8:tx,illustrative
   7,2025-11-06T13:26:59+01:00,30010000,Stablecoins,6928.76,0.00,evm.loan · ethereum1,,evm:ethereum:0x60ffe08b24dd61ec77080d6b60d86bde5333f01e:0xc194f7cb882b288f9348fd0d78f3336e9dfcd1791b60edee2eb9facc0ae1c6c8:loan/453,illustrative
   7,2025-11-06T13:26:59+01:00,52100000,Deuda DeFi,0.00,6928.76,evm.loan · ethereum1,,evm:ethereum:0x60ffe08b24dd61ec77080d6b60d86bde5333f01e:0xc194f7cb882b288f9348fd0d78f3336e9dfcd1791b60edee2eb9facc0ae1c6c8:loan/453,illustrative
   ```

   Beside the CSV, the export writes `profile.json` with the profile above.

   Entry 1 is the ETH half of the loan and entry 2 the EURC half, both tagged "Marcel (loan)" and posted to its code, 551. Entries 3, 4 and 6 are gas, booked as fees (626) with a disposal of ETH at cost. Entry 5 is EURC supplied to Aave (EURC into aEURC, both stablecoins, so both sides land on `30010000`). Entry 7 is the 8.000 USDe borrowed on Aave.
3. **One line as CEL sees it** (entry 1, first line):

   ```json
   {
     "entry": 1,
     "date": "2025-11-06T13:11:35+01:00",
     "period": "2025-11",
     "level": "event",
     "account": "30000000",
     "account_name": "Criptomonedas",
     "categories": [
       "holding.crypto"
     ],
     "debit": "27.99",
     "credit": "0.00",
     "description": "evm.transfer · ethereum1",
     "note": "",
     "reference": "loan agreement 2025-11",
     "counterparty": "",
     "counterparty_label": "",
     "tracked_account": "ethereum1",
     "compartment": "",
     "asset": "ethereum",
     "quantity": "",
     "sources": [
       "evm:ethereum:0x60ffe08b24dd61ec77080d6b60d86bde5333f01e:0x5885a011f0e43700625aae9c28950e521ed9b56686e2fb6f257dc72d733cccd8:value"
     ],
     "revision": "illustrative",
     "profile": "auditor-detail@1"
   }
   ```

## Example 3: a custom CEL format, the owner's sheet

A layout like the sheet the owner gave the accountant: one row per account per month, with *Categoría*, *Cuenta*, *Debe* and *Haber*, and euros written as the sheet writes them. It is the kind of format an AI writes from a sample of the target sheet and checks with the preview.

1. **The profile as stored** (the format part is the YAML below):

   ```json
   {
     "id": "hoja-asesor",
     "name": "Hoja del asesor",
     "version": 1,
     "chart": {
       "preset": "es-pgc-inventories",
       "code_width": 8,
       "counterparties": {
         "marcel-loan": {
           "code": "551",
           "name": "Préstamo socio (Marcel)"
         },
         "company-bank": {
           "code": "572",
           "name": "Bancos"
         }
       }
     },
     "timezone": "Europe/Madrid",
     "aggregation": {
       "level": "month",
       "description": "Operaciones {month} {year}",
       "language": "es"
     },
     "format": {
       "custom": "<the YAML below, stored as JSON>"
     }
   }
   ```

2. **The format**, verbatim:

   ```yaml
   name: Hoja del asesor (mensual)
   file: {type: csv, separator: ";", quote: minimal, encoding: utf-8, header: true, max_lines: 0}
   filter: 'line.level == "month"'
   columns:
     - name: Asiento
       expr: 'line.description'
     - name: Fecha
       expr: 'format_date(line.date, "%d/%m/%Y")'
     - name: Categoría
       expr: >-
         line.account.startsWith("6")
           ? (line.categories.exists(c, c.startsWith("realized") || c.endsWith("trading")) ? "Pérdidas" : "Gastos")
           : line.account.startsWith("7")
             ? "Ingresos"
             : (line.account.startsWith("1") || (line.account.startsWith("5") && !line.account.startsWith("57")))
               ? "Pasivos" : "Activos"
     - name: Cuenta
       expr: 'line.account_name'
     - name: Código
       expr: 'line.account.substring(0, 3)'
     - name: Debe
       expr: 'line.debit == "0.00" ? "" : format_decimal(line.debit, ",", ".") + " €"'
     - name: Haber
       expr: 'line.credit == "0.00" ? "" : format_decimal(line.credit, ",", ".") + " €"'
     - name: Eventos
       expr: 'string(size(line.sources))'
   ```

   1. `filter` keeps month lines only, so the format refuses to run on a per-event profile instead of producing a sheet that makes no sense.
   2. *Fecha* drops the offset on purpose (`%d/%m/%Y`): the sheet has bare dates. The profile records the choice.
   3. *Categoría* mirrors the sheet's: a group 6 line is *Pérdidas* when it carries trading categories and *Gastos* otherwise; group 7 is *Ingresos*; group 1 and group 5 outside 57 are *Pasivos*; the rest are *Activos*.
   4. A format writes rows, one per line. It cannot add the sheet's "Total" row; the accountant's spreadsheet sums the columns.
3. **The output it produces**, verbatim, for November and December 2025 (illustrative), with `profile.json` beside it:

   ```
   Asiento;Fecha;Categoría;Cuenta;Código;Debe;Haber;Eventos
   Operaciones noviembre 2025;30/11/2025;Activos;Criptomonedas;300;1.840,74 €;;41
   Operaciones noviembre 2025;30/11/2025;Activos;Stablecoins;300;15.401,08 €;;10729
   Operaciones noviembre 2025;30/11/2025;Pasivos;Deuda DeFi;521;;6.928,76 €;1
   Operaciones noviembre 2025;30/11/2025;Pasivos;Préstamo socio (Marcel);551;;10.032,26 €;2
   Operaciones noviembre 2025;30/11/2025;Gastos;Comisiones;626;3,98 €;;27
   Operaciones noviembre 2025;30/11/2025;Ingresos;Resultado trading;769;;260,06 €;2560
   Operaciones noviembre 2025;30/11/2025;Ingresos;Rendimientos;769;;3,24 €;108
   Operaciones noviembre 2025;30/11/2025;Ingresos;Ingresos excepcionales;778;;21,48 €;20
   Operaciones diciembre 2025;31/12/2025;Activos;Criptomonedas;300;368,76 €;;9
   Operaciones diciembre 2025;31/12/2025;Activos;Stablecoins;300;;421,27 €;247
   Operaciones diciembre 2025;31/12/2025;Gastos;Comisiones;626;2,53 €;;13
   Operaciones diciembre 2025;31/12/2025;Gastos;Intereses de deudas;662;20,89 €;;1
   Operaciones diciembre 2025;31/12/2025;Pérdidas;Resultado trading;669;33,53 €;;107
   Operaciones diciembre 2025;31/12/2025;Ingresos;Rendimientos;769;;3,33 €;3
   Operaciones diciembre 2025;31/12/2025;Ingresos;Ingresos excepcionales;778;;1,11 €;39
   ```

4. **One line as CEL sees it** (December, `66900000`, sources shortened here):

   ```json
   {
     "entry": 2,
     "date": "2025-12-31T23:59:59+01:00",
     "period": "2025-12",
     "level": "month",
     "account": "66900000",
     "account_name": "Resultado trading",
     "categories": [
       "income.trading",
       "realized.gain",
       "realized.loss",
       "expense.trading",
       "rounding.loss"
     ],
     "debit": "33.53",
     "credit": "0.00",
     "description": "Operaciones diciembre 2025",
     "note": "",
     "reference": "",
     "counterparty": "",
     "counterparty_label": "",
     "tracked_account": "",
     "compartment": "",
     "asset": "",
     "quantity": "",
     "sources": [
       "cex:mexc:6dc209c0-07f3-44df-bf8d-2621bdcc6365:1581:0:pnl",
       "evm:arbitrum:0x60ffe08b24dd61ec77080d6b60d86bde5333f01e:0x91f8610e30f8808e8b554e1e7bf683419dd7690a2cd74fac050f02b4c56f2d49:swap/f0",
       "… (105 more)"
     ],
     "revision": "illustrative",
     "profile": "holded-monthly@1"
   }
   ```
