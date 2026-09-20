# litmus-accounting

Pure accounting engine for crypto and trading books. Input: dated legs grouped
into events, links between events, a policy, and a caller-implemented price
source. Output: open lots, open liabilities, realized PnL, income and expense
flows, internal moves, every price used, and an exceptions report. No venue
concepts, no database, no fetching.

Distribution `litmus-accounting`, import `litmus.accounting`. Python 3.11+.
Licensed under the MIT license.

## Install

```sh
python3 -m venv .venv
.venv/bin/pip install -e './pkg[dev]'
.venv/bin/pytest pkg/tests -q
```

## Library

```python
from decimal import Decimal
from datetime import datetime, timezone
from litmus.accounting import run, value, FixedPricing
from litmus.accounting.model import Event, Leg, Policy

policy = Policy(cost_method='fifo', lot_scope='global', functional_currency='EUR', cash=('USDC',))
events = [
  Event('buy', datetime(2026, 1, 1, tzinfo=timezone.utc), (
    Leg('BTC', Decimal('1'), 'cex:spot', 'trade'),
    Leg('EUR', Decimal('-10000'), 'cex:spot', 'trade'),
  )),
]
result = run(events, policy=policy, pricing=FixedPricing({('BTC', 'EUR'): '30000'}))
result.lots, result.liabilities, result.realized, result.flows, result.prices, result.exceptions, result.complete
valuation = value(result.lots, liabilities=result.liabilities, at=datetime(2026, 12, 31, tzinfo=timezone.utc), policy=policy, pricing=FixedPricing({('BTC', 'EUR'): '40000'}))
```

Every type is a frozen dataclass in `litmus.accounting.model`. Implement the
`Pricing` protocol (`price(asset, quote, time, *, source) -> Decimal | None`)
to plug in a real price store; return `None` for a gap, never zero.
`TablePricing(records, max_age=timedelta(...))` is the in-memory table the CLI
uses; with `max_age` a price older than that is a gap instead of a carry-forward.

The model reference is in [docs/model.md](docs/model.md), the policy in
[docs/policy.md](docs/policy.md), worked examples in
[docs/examples.md](docs/examples.md), and the CLI in [docs/cli.md](docs/cli.md).

## CLI

A `typer` app named `accounting` with four verbs. `--json` is accepted before
or after the verb.

```sh
accounting run examples/perp.json            # human summary
accounting run examples/perp.json --json     # full result
accounting --json run examples/perp.json     # --json before the verb works too
accounting run ledger.json --policy policy.json --prices prices.json --max-age P1D --strict
accounting validate ledger.json              # shape and structure, no prices
accounting value ledger.json --at 2026-12-31T23:59:59Z
accounting schema ledger                     # print one schema
accounting schema --out build/schema         # write all three
accounting schema --check build/schema       # fails if they drifted
```

| Verb | Arguments and flags | Output with `--json` |
|---|---|---|
| `run` | `LEDGER [--policy FILE] [--prices FILE] [--max-age DURATION] [--strict]` | `Result` (`accounting schema result`) |
| `validate` | `LEDGER [--policy FILE] [--prices FILE]` | list of `ExceptionItem` |
| `value` | `LEDGER --at ISO8601 [--policy FILE] [--prices FILE] [--max-age DURATION] [--strict]` | `Valuation` (`accounting schema valuation`) |
| `schema` | `[ledger\|result\|valuation] [--out DIR] [--check DIR]` | one schema document; `[]` for `--check` |

The ledger file holds `events`, optional `links`, and optionally an embedded
`policy`, `prices` table and `max_age` (`--policy`, `--prices` and `--max-age`
override). Prices are looked up as the latest record at or before the
requested time for the pair, no older than `max_age` when one is set (ISO 8601
duration such as `P1D`, or seconds).

Completed calculations return zero, including results with findings or missing
prices. Read `complete` and `exceptions` in the JSON result. Runtime failures,
strict price-gap failures, and stale `schema --check` files return one; usage or
invalid input returns two.

## JSON schema

The frozen dataclasses in `litmus.accounting.model` define the contract.
Pydantic adapters in `codec.py` validate JSON and generate schemas from those
same types. Generated schema files are build artifacts and are not committed.

Use `accounting schema ledger`, `accounting schema result`, or
`accounting schema valuation` to print a schema. Export all three with
`accounting schema --out build/schema` when a consumer needs files. A future
documentation site can generate these schemas during its build.

## Contract in one paragraph

A `Leg` is `{asset, quantity, compartment, tag, fee?, label?}` with
`tag ∈ {trade, transfer, income, expense, borrow, repay}`. An `Event` is
`{id, time, legs}`. A `Link` is `{src, dst}` over event ids and marks one
internal movement, booked when the earlier of the two events is processed.
Within an event borrows are booked first, then trades, then transfers, then
repays, then income and expenses, then fees; across events with the same
timestamp, input order wins. A `borrow` opens a lot at market value and a
liability of the same quantity under `(compartment, asset)`; a `repay` is a
disposal at market that reduces the liability; `Policy.liability_valuation`
(`cost` or `market`) says whether the liability itself realizes anything. The
caller supplies the cash list, the fiat list, income vs expense by sign, the
`fee` flag, globally unique event ids, the `-size × price` cash-leg
decomposition of fills, and the compartment granularity.

## Basis rollover and pending claims

Generic basis rollover, pending claims, exact acquisition ancestry and scoped
notional settlement eligibility are documented in [docs/index.md](docs/index.md).
Existing ordinary ledger examples retain their financial results.

```sh
accounting --json run examples/rollover.json
accounting validate examples/rollover.json --json
```

The synthetic rollover example carries €1000 into a receipt and pending claim,
then realizes €300 on €1300 settlement, without a receipt price. New callers pin
`schema_version: "0.3"`; generated schemas reject unknown JSON fields.

## Python checks

Install development tools with `python -m pip install -e './pkg[dev]'`, then run
`python scripts/check.py`. The command checks Ruff lint, formatting, and Pyright
for `pkg/src` and `pkg/tests` using that Python interpreter. In the shared Litmus
environment, use `../platform/.venv/bin/python scripts/check.py` (or
`.venv/bin/python scripts/check.py` from platform).

Ruff and Pyright settings live in the repository-root `pyproject.toml` under
`[tool.ruff]` and `[tool.pyright]`. Package metadata and dependencies remain in
`pkg/pyproject.toml`. Shared tooling sections are synchronized manually across the
Litmus repositories. Choose the installed development interpreter in your editor.
See the [tooling policy](../platform/docs/technical/python-tooling.md) for details.

## Repository structure

- `pkg/`: installable Python package, including the models, engine, CLI, and tests.
  Synthetic regression inputs and expected results live in `pkg/tests/fixtures/`.
- `docs/`: technical reference and worked explanations.
- `examples/`: runnable ledger inputs for library and CLI users.
- `scripts/check.py`: contributor helper for lint, formatting, and type checks.
- `.github/workflows/`: continuous integration.

Generated schemas belong in `build/schema/` (ignored by Git). Release notes can
start with the first published release.

## Package organization

The public frozen models remain in `model.py`, JSON boundaries in `codec.py`,
and caller-supplied pricing interfaces in `pricing.py`. The `engine/` package
contains calculation, lot inventory, rollover ordering, exact arithmetic, and
rounding. Valuation and CLI presentation remain separate. This is a pure library;
it has no database or HTTP API. Schema exports are generated from its dataclasses
and remain available to offline consumers.

The engine, models, codecs, pricing, valuation, and schema generation pass strict
Pyright. No known accounting values use `Any`.
