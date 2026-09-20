# CLI

Binary: `accounting` (installed by `pip install`).

```
accounting [--json] run      LEDGER [--policy FILE] [--prices FILE] [--max-age DURATION] [--strict] [--json]
accounting [--json] validate LEDGER [--policy FILE] [--prices FILE] [--json]
accounting [--json] value    LEDGER --at ISO8601 [--policy FILE] [--prices FILE] [--max-age DURATION] [--strict] [--json]
accounting [--json] schema   [ledger|result|valuation] [--out DIR] [--check DIR] [--json]
```

A `typer` app. `--json` is a callback option on the app and an option on every
verb, so it is accepted before or after the verb (the one after the verb
wins). `-h` and `--help` both work.

## Verbs

1. `run`: books the ledger. Prints a human summary or, with `--json`, the full `Result` (see `accounting schema result`). `--strict` stops at the first price gap.
2. `validate`: parses the ledger against the model and runs the structural checks that need no prices (duplicate ids, invalid legs, naive timestamps, links to unknown events, links that do not conserve quantity). Prints `valid` or the problems; `--json` prints the exceptions list.
3. `value`: books the ledger, then values every open lot and liability at `--at` through the ledger's price table. Prints a summary or, with `--json`, the `Valuation` (see `accounting schema valuation`).
4. `schema`: prints one schema (default `ledger`), or writes all three into `--out DIR`, or with `--check DIR` fails when the files there differ from what the code generates.

## Ledger file

```json
{
  "policy": { "cost_method": "fifo", "lot_scope": "compartment", "functional_currency": "EUR", "cash": ["USDC"] },
  "prices": [ {"asset": "USDC", "quote": "EUR", "time": "2026-01-01T00:00:00Z", "source": "market", "price": "0.9"} ],
  "events": [ {"id": "e1", "time": "2026-01-02T10:00:00Z", "legs": [ {"asset": "BTC-PERP", "quantity": "1", "compartment": "dydx:0", "tag": "trade"}, {"asset": "USDC", "quantity": "-50000", "compartment": "dydx:0", "tag": "trade"} ]} ],
  "links": [ {"src": "wd", "dst": "recv"} ]
}
```

`policy` and `prices` may be omitted from the file and given with `--policy` and `--prices`. `max_age` (optional, ISO 8601 duration such as `P1D` or `PT36H`, or a number of seconds) bounds how far a price carries forward; `--max-age` overrides it. Decimals are strings (numbers are accepted); timestamps are ISO 8601 with a timezone.

## JSON shapes

- `run --json`: `Result` per `accounting schema result`: `{policy, lots[], liabilities[], realized[], flows[], moves[], balances[], prices[], exceptions[], complete}`.
- `value --json`: `Valuation` per `accounting schema valuation`: `{at, positions[], liabilities[], prices[], exceptions[], complete}`.
- `validate --json`: a list of `ExceptionItem` `{code, event, message, detail}`.
- `schema --check DIR --json`: `[]` when up to date.

## Process status

Completed calculations return zero, including results containing exceptions or
price gaps. Inspect `complete` and `exceptions` in the result. `validate` returns
its findings as a list and also completes successfully.

Runtime failures, a price gap with `--strict`, and stale `schema --check` files
return one. Argument or input validation errors return two.
