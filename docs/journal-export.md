# Journal export

> **Not built yet.** Export profiles and the journal export are a design the owner decided on 2026-10-02; the engine's journal (`Result.journal`, [model.md](model.md)) exists, and portfolio's counterparties and boundary report exist. This page is the reference for building the rest. The [examples](https://github.com/litmus-tax/portfolio/blob/main/docs/journal-export-examples.md) (private portfolio repository) run it on real data.

The journal export maps the engine's balanced journal to an accountant's chart, so the books can be posted into a general ledger (Holded first). Who is on the other side of each boundary transfer comes from portfolio's [boundary transfers](https://github.com/litmus-tax/portfolio/blob/main/docs/boundary-transfers.md).

## Terms

1. **Clearing account.** Where a journal export posts a boundary transfer that has no counterparty, or whose counterparty the chart does not map: 555, "partidas pendientes de aplicación" (items pending allocation), in the Spain presets. The accountant clears it in the general ledger.
2. **Export profile.** A named export configuration: chart, timezone, aggregation and format.
3. **Chart.** A mapping of categories and counterparties to account codes and names, with the clearing account. Preset or custom.
4. **Category.** A closed set of kinds of journal line after mapping, such as `holding.stablecoin` or `expense.fee`. A code is chosen per category, not per line.
5. **Asset class.** `fiat`, `stablecoin` or `crypto`, derived from the catalogue.
6. **CEL.** The Common Expression Language: small, side-effect-free expressions, already used by the cex venue specs.
7. **PGC.** Spain's *Plan General de Contabilidad*, the chart the owner's accountant uses. Codes here are its 3- or 4-digit accounts; in Holded they are padded to 8 digits (55500000).

## Rules

### Export profiles

1. **An export profile is chart, timezone, aggregation and format** (owner, 2026-10-02).
   1. A profile has a name and four parts: a **chart**, a **timezone**, an **aggregation** and a **format**. A portfolio may keep several, for example "Holded monthly" (Spain PGC as inventories, Europe/Madrid, `month`, `holded`) and "Auditor detail" (the same chart, `event`, a custom CSV).
   2. **Profiles are export-time configuration.** They change no figure of a revision, so creating, changing or deleting one never makes a sealed revision stale, and no profile is among a revision's inputs. An export is a function of the revision and the profile, and states both.
   3. Changing a profile needs `correct`; reading one needs `read`. A profile is current state: it keeps no versions and no history (owner, 2026-10-02). **Every exported file includes the profile it was made with**: an extra sheet in an Excel file, a `profile.json` beside a CSV, so the file documents itself.
   4. A new portfolio has one profile, "Neutral journal" (the neutral chart, UTC, `event`, `journal`), and may add others from presets.

   *Why:* the same books go to different readers in different shapes: an accountant posts one entry per month in their chart, an auditor wants every event, another ledger wants its own columns. None of that is accounting, so none of it may stale sealed books or need a rebuild. Bundling the four parts under one name lets the owner, the CLI or an agent repeat an export exactly ("Holded monthly, 2025") without restating it.
2. **Charts: presets and custom.**
   1. A chart maps each **category** to an account code and name, and each **counterparty** to a code and an optional name (the counterparty's label by default): `shareholder-loan` → `551` "Préstamo socio". It also holds the **clearing account**, optional **overrides** per tracked account (a subaccount per venue) and per asset (its asset class), and the **code width** (codes padded on the right with zeros, 8 for Holded: `551` → `55100000`).
   2. **Presets** ship with Litmus and are read-only:
      1. **"Spain PGC: crypto as inventories"**: crypto and stablecoins under 300 (*existencias*), trading results 769 / 669;
      2. **"Spain PGC: crypto as intangible"**: crypto and stablecoins under 206 (*inmovilizado intangible*), disposals 771 / 671;
      3. **"Neutral"**: the category names as codes (`holding.crypto`, `realized.gain`…), for readers with no chart.
   3. **Custom charts** are copied from a preset and then edited: for example, stablecoins that are e-money tokens moved to group 5, or trading results booked as sales and purchases (700 / 600 / 610). A custom chart keeps the name of the preset it came from.
   4. The clearing account defaults to 555 (*partidas pendientes de aplicación*) in both PGC presets, and `boundary.clearing` in the neutral one.
   5. **Unmapped counterparties** post to the clearing account, with a warning in the export's `README` and in the preview that names them. Presets map no counterparty, because counterparties are the portfolio's own.
   6. **Exports that need no codes ignore charts:** the CSV reports (disposals, income and expenses, holdings, liabilities, movements with outside parties) and Koinly's CSV. A portfolio that never exports a journal never needs a chart.

   *Why:* where crypto sits in a Spanish chart is not settled: Spain's accounting institute (ICAC, BOICAC 120/2019, query 1) treats crypto held for sale in the ordinary course of business as inventories, and other crypto as intangible, and e-money tokens may be financial assets. That is the accountant's call per company, and may change. Presets make the common answers one click; custom charts make any other answer possible without a release. The owner's 2025 sheet is the first preset's worked example (examples 1 and 4).
3. **Categories.** Every engine journal line ([`A/model.py:459-473`](https://github.com/litmus-tax/accounting/blob/5eef60ac1e3b82ecd22dd754d066d6c4f2459e13/pkg/src/litmus/accounting/model.py#L459-L473)) falls in exactly one category. The two PGC presets map them as follows (codes before padding):

    | # | Category | Engine line | The owner's 2025 sheet | Inventories preset | Intangible preset |
    |---|---|---|---|---|---|
    | 1 | `holding.crypto` | `holding` of a `crypto` asset, any compartment class but `liability` | Criptomonedas | 3000 Criptomonedas | 2060 Criptomonedas |
    | 2 | `holding.stablecoin` | `holding` of a `stablecoin` | Stablecoins | 3001 Stablecoins | 2061 Stablecoins |
    | 3 | `holding.fiat` | `holding` of a `fiat` asset (EUR, USD at a venue) | — | 5729 Saldos en plataformas | 5729 |
    | 4 | `liability.debt` | `liability` in a `liability` compartment | Deuda DeFi (USDe) | 5210 Deuda DeFi | 5210 |
    | 5 | `liability.settlement` | `liability` of a negative settlement balance | — | 5211 Deuda en plataformas | 5211 |
    | 6 | `realized.gain`, `realized.loss` | `realized` of a `crypto` or `stablecoin` asset, by side | Trading | 769 / 669 Resultado trading | 771 / 671 |
    | 7 | `realized.fx_gain`, `realized.fx_loss` | `realized` of a `fiat` asset, by side | — | 768 / 668 Diferencias de cambio | 768 / 668 |
    | 8 | `income.trading`, `expense.trading` | flows labelled `realized_pnl`, `funding`, `performance` | Trading | 769 / 669 | 769 / 669 |
    | 9 | `expense.fee` | flows labelled `fee`, `gas`, `transfer_fee`; `fee_rebate` credits it | Comisiones | 626 Comisiones | 626 |
    | 10 | `expense.interest` | flows labelled `interest` | — | 6624 Intereses de deudas | 6624 |
    | 11 | `income.yield` | flows labelled `yield` | — | 7691 Rendimientos | 7691 |
    | 12 | `income.other`, `expense.other` | flows labelled `bonus` and `unsolicited`, by side (a clawback is an expense) | — | 778 / 678 Ingresos / Gastos excepcionales | 778 / 678 |
    | 13 | `adjustment.gain`, `adjustment.loss` | flows labelled `adjustment`, by side | — | 7781 / 6781 Ajustes sin detalle | 7781 / 6781 |
    | 14 | `rounding.gain`, `rounding.loss` | `rounding` | — | 769 / 669 | 769 / 669 |
    | 15 | `boundary` | `external` of a boundary transfer's event | Préstamo socio; bank | the counterparty's code, else the clearing account | as left |
    | 16 | `external.other` | any other `external` line (a cost not paid in the ledger) | — | the clearing account | as left |

    1. An open position books nothing and has no line. Collateral is `holding` by its asset.
    2. A category whose lines are of both signs has a code per side, because a PGC account is either income (group 7) or expense (group 6).
    3. The preset codes are proposals for the accountant to confirm. Changing one is a custom chart, not an amendment of this page.

    *Why:* the sheet's six accounts are this table at its coarsest: two asset classes, two liabilities, one trading result and one fee account. A closed set of categories keeps every chart complete: a line can never fall through unmapped. Splitting by asset class, compartment class and label is enough to reproduce the sheet and finer charts; splitting by Litmus account is an override.
4. **Asset classes come from the catalogue.**
    1. `fiat`: a fiat currency of the catalogue (the functional currency included).
    2. `stablecoin`: an asset whose `pegged_to` chain reaches a fiat currency: USDC, USDT, USDe, EURC, and receipts on them such as aEURC.
    3. `crypto`: everything else, including claims and NFT positions.
    4. A chart may override one asset's class (for example to treat a synthetic dollar as `crypto`).

    *Why:* the sheet separates stablecoins from other crypto, and the peg is already in the catalogue for prices; using it again means no list to maintain.

### Journal export

5. **The journal export maps the engine's journal; it computes nothing.** Each line of the revision's journal takes its category's code from the profile's chart. A boundary transfer's `external` line takes its counterparty's code in the chart; an untagged transfer, or a counterparty the chart does not map, takes the clearing account, with a warning. Portfolio only labels and sums the rows.

    *Why:* the journal already balances per event; the export must not open a second place where figures are made. Which events are boundary transfers, and their tags, are in the revision's inputs, so no engine change is needed for the mapping.
6. **Timezone and dates** (owner, 2026-10-02).
    1. Each profile has a timezone (IANA name; `UTC` by default). It decides which calendar day and month a line falls in, and so the month cuts of aggregation.
    2. **Exported dates carry their UTC offset by default** (owner, 2026-10-02): `2025-11-06T13:14:23+01:00`. A custom format may choose a date layout without an offset (`format_date(line.date, "%d/%m/%Y")`); that choice is part of the format, so it is recorded in the profile and its versions, and the export's `README` names the timezone. Holded's date column follows its template (a calendar date); the full timestamp with offset goes in the line's notes.
    3. An export covers `from`–`to` (default: the revision's whole span), read in the profile's timezone. A month cut by `from`, `to` or `as_of` is labelled partial, and the export warns when `as_of` is not at the end of a day in that timezone.

    *Why:* the company's books are kept in Spanish dates: a transfer at 23:30 UTC on 31 December is 1 January in Madrid, and so belongs to the next year. A timestamp with its offset is unambiguous wherever it is read, so one export can be checked against Litmus's UTC history line by line. Some targets want a bare date, so the offset is the default, not a law: what a profile chose is on record.
7. **Aggregation.**
    1. **`event`:** one entry per event, dated at the event, with one line per code and side (lines of one code on one side of one event are summed). The entry's description is the record's nature and summary; its sources are its record ids.
    2. **`month`:** one entry per calendar month in the profile's timezone, dated at the month's last moment and described by the profile's template (default "Operaciones {month} {year}", in the chart's language). Each code nets to one line on the side of its balance; a code that nets to zero has no line. A gain and loss pair nets to one line, on its gain or its loss code. Each line's sources are the events it sums, also written to a companion file (`sources.csv`: entry, code, event id, debit, credit).
    3. Both balance by construction: each event balances after rounding, and an aggregate is a sum of balanced events.

    *Why:* the accountant posts the month as one entry, as the sheet does; an auditor or the owner traces a line back to its events. The netted pairs reproduce the sheet exactly: November shows a trading loss, December a trading gain.
8. **Formats: built-in and custom** (owner, 2026-10-02).
    1. **Built-in formats:**
       1. `journal`: a neutral CSV: entry, date, code, account name, debit, credit, description, reference, sources, revision id;
       2. `holded`: Holded's *libro diario* import template (Contabilidad › Libro diario › Importar), an Excel file with entry number, date, account name, account number, debit, credit, description, tags, notes, entry type (blank: an ordinary entry) and dotted (`0`); decimal comma, no thousands separator; split at entry boundaries to stay under Holded's 800-line advice, and zipped;
       3. `koinly`: Koinly's universal CSV. It exports transactions, not journal lines, so it ignores the chart and the aggregation; it uses the timezone.
    2. **Custom formats** describe a file as data:

       ```yaml
       file: {type: csv, separator: ";", header: true, encoding: utf-8, max_lines: 0}
       columns:
         - {name: Fecha,    expr: 'line.date'}
         - {name: Cuenta,   expr: 'line.account'}
         - {name: Debe,     expr: 'format_decimal(line.debit, ",", "")'}
         - {name: Haber,    expr: 'format_decimal(line.credit, ",", "")'}
         - {name: Concepto, expr: 'line.description + (line.reference != "" ? " · " + line.reference : "")'}
         - {name: Origen,   expr: 'line.sources.join(" ")'}
       ```

       1. `file`: `csv` or `xlsx`; for CSV, the separator, quoting, encoding and whether to write a header; `max_lines` splits files at entry boundaries (0: never).
       2. Each column is a CEL expression over one journal line, `line`, with fields: `entry` (int), `date` (string, RFC 3339 with offset, in the profile's timezone), `period` (`2025-11`), `level`, `account` and `account_name`, `categories` (list of the categories in the line), `debit` and `credit` (decimal strings with `.`, rounded), `description`, `note`, `reference`, `counterparty` and `counterparty_label` (boundary lines; empty when untagged), `tracked_account`, `compartment`, `asset`, `quantity`, `sources` (record or event ids), `revision`, `profile`. The entry's other lines are not in scope. The [examples](https://github.com/litmus-tax/portfolio/blob/main/docs/journal-export-examples.md) list the fields with their types and show real lines.
       3. A timestamp is written as ISO 8601 with its offset unless the expression formats it otherwise; the format's columns are stored in the profile, so a layout without an offset is an explicit, recorded choice.
       4. `filter` is an optional CEL predicate; a line it rejects is left out, and the export's `README` counts what was left out and states that the file no longer balances if it did.
    3. **One CEL, one set of bounds.** Custom formats use the CEL library and helper functions of the cex venue specs, plus two pure formatting helpers: `format_date(date, pattern)` and `format_decimal(amount, decimal_mark, group_mark)`. CEL has no loops; helpers are pure and linear; a format's size and an export's lines are limited, and an export runs in a job with a time limit, because the library has no cost limit of its own. An expression that fails on a line fails the export with the line and the error, never a silently empty cell.
    4. A preview renders the first lines of any format, saved or not, with every error, so a format can be written by trial.
    5. **Every export states its basis:** the header of every tax-ready export (portfolio's [exports](https://github.com/litmus-tax/portfolio/blob/main/docs/exports.md); in a `README` beside the files where the format has no room for one), the period, and **the profile in full**: an extra sheet named `profile` in an Excel file, a `profile.json` sidecar beside a CSV (owner, 2026-10-02). A custom format may also put the revision id in a column. An export can therefore be checked, or repeated, from the file alone, though the profile may have changed since.
    6. Posting to Holded's API is later, not v1.

    *Why:* every ledger, auditor and spreadsheet wants its own columns, and Litmus cannot ship them all. A format as declarative data is safe to run in a hosted service, and CEL is already how cex describes venue files, with its library and bounds already specified, so one expression language covers both. It is also what makes formats cheap to add: the user's AI can write a custom format from a sample of the target file (an exported Holded journal, an auditor's template) and check it against the preview, as agents already write cex override specs. Holded's built-in follows its help pages: one number and one date per entry, debits equal to credits, and imports above about 800 lines fail ([Holded: import and export your accounting entries](https://help.holded.com/en/articles/6895995-import-and-export-your-accounting-entries); [importar libro diario: preguntas frecuentes](https://help.holded.com/es/articles/10006986-importar-libro-diario-preguntas-frecuentes)). Holded imports its Excel template only, so the built-in is Excel, not CSV.
9. **Routes and CLI.** Under `{P}`:

    | Method | Path | Scope | Response |
    |---|---|---|---|
    | GET | `/books/presets` | read | the preset charts and built-in formats |
    | GET | `/books/profiles` | read | the portfolio's profiles |
    | POST | `/books/profiles` `{name, chart, timezone, aggregation, format}` | correct | the new profile; `chart` is a preset id, or a full chart (custom, copied from a preset); `format` is a built-in id or a custom format |
    | GET, PUT, DELETE | `/books/profiles/{id}` | read, correct, correct | one profile |
    | POST | `/books/{rev}/export/preview` `{profile or profile id, from?, to?, limit?}` | read | the first `limit` lines (default 50), rendered, and any errors |
    | GET | `/books/{rev}/export?profile={id}&from&to` | read | a file (a zip when several) |
    | GET | `/books/{rev}/rows?kind=boundary` | read | the boundary report (portfolio) |
    | GET, POST | `/counterparties` `{label, mine, note?, reference?}` | read, correct | the portfolio's counterparties; a new one |
    | GET, PATCH | `/counterparties/{id}` `{label?, mine?, note?, reference?}` | read, correct | one counterparty; a change queues a build |
    | DELETE | `/counterparties/{id}` (`?dry_run=true` lists the tags it would withdraw) | correct | deletes it and withdraws its tags |

    `litmus counterparties list | add --label L [--mine] | edit ID … | delete ID [--dry-run]`, `litmus books profiles list | show NAME | create --from-preset ID | edit NAME --file FILE | delete NAME`, `litmus books preview REV --profile NAME|--file FILE`, and `litmus books export REV --profile NAME [--from --to] [--out]`. `GET /books/{rev}/export?format=package|reports` stays as it is; `format=koinly` is the built-in profile format.

## Profiles on one revision

The [export examples](https://github.com/litmus-tax/portfolio/blob/main/docs/journal-export-examples.md) (in the private portfolio repository, as they use real books) run three profiles ("Holded monthly", "Auditor detail" and a custom CEL format) on the same November and December 2025 books. Changing a profile, or adding one, stales nothing: the next export simply states the new profile version.
