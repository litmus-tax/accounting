# litmus-accounting

Pure accounting engine for crypto and trading books. Input: dated legs grouped
into events, links between events, a policy, and a caller-implemented price
source. Output: open lots, open liabilities, open perpetual positions, realized
PnL, income and expense flows, internal moves, a balanced double-entry journal,
the open-lot rows at the end of the run (what is held at cost at its `as_of`),
every price used, and an exceptions report. It values nothing at market: it asks
for a price only where an event needs a value, and checks its books with the
cost identity, which needs no price (policy 05 rules 1.5 and 39.5.1). No venue
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
from litmus.accounting import run, FixedPricing
from litmus.accounting.model import Event, Leg, Policy

policy = Policy(cost_method='fifo', lot_scope='global', functional_currency='EUR', cash=('USDC',))
events = [
  Event('buy', datetime(2026, 1, 1, tzinfo=timezone.utc), (
    Leg('BTC', Decimal('1'), 'cex:spot', 'trade'),
    Leg('EUR', Decimal('-10000'), 'cex:spot', 'trade'),
  )),
]
result = run(events, policy=policy, pricing=FixedPricing({('BTC', 'EUR'): '30000'}))
result.lots, result.liabilities, result.realized, result.flows, result.open_rows, result.prices, result.exceptions, result.complete
```

Every type is a frozen dataclass in `litmus.accounting.model`. Implement the
`Pricing` protocol (`price(asset, quote, time, *, source) -> Decimal | None`)
to plug in a real price store; return `None` for a gap, never zero.
`TablePricing(records)` is the in-memory table the CLI uses. It is daily: a row
prices its own UTC day from its time on, and nothing is carried to a later day.
Which price a day takes (a stale one, a peg) is the caller's choice.

The model reference is in [docs/model.md](docs/model.md), the policy in
[docs/policy.md](docs/policy.md), worked examples in
[docs/examples.md](docs/examples.md), and the CLI in [docs/cli.md](docs/cli.md).

## CLI

A `typer` app named `accounting` with three verbs. `--json` is accepted before
or after the verb.

```sh
accounting run examples/perp.json            # human summary
accounting run examples/perp.json --json     # full result
accounting --json run examples/perp.json     # --json before the verb works too
accounting run ledger.json --policy policy.json --prices prices.json --strict
accounting validate ledger.json              # shape and structure, no prices
accounting schema ledger                     # print one schema
accounting schema --out build/schema         # write both
accounting schema --check build/schema       # fails if they drifted
```

| Verb | Arguments and flags | Output with `--json` |
|---|---|---|
| `run` | `LEDGER [--policy FILE] [--prices FILE] [--strict]` | `Result` (`accounting schema result`) |
| `validate` | `LEDGER [--policy FILE] [--prices FILE]` | list of `ExceptionItem` |
| `schema` | `[ledger\|result] [--out DIR] [--check DIR]` | one schema document; `[]` for `--check` |

The ledger file holds `events`, optional `links`, and optionally an embedded
`policy` and `prices` table (`--policy` and `--prices` override). Prices are
looked up as the latest record at or before the requested time for the pair on
the same UTC day; a day without one is a gap.

Completed calculations return zero, including results with findings or missing
prices. Read `complete` and `exceptions` in the JSON result. Runtime failures,
strict price-gap failures, and stale `schema --check` files return one; usage or
invalid input returns two.

## JSON schema

The frozen dataclasses in `litmus.accounting.model` define the contract.
Pydantic adapters in `codec.py` validate JSON and generate schemas from those
same types. Generated schema files are build artifacts and are not committed.

Use `accounting schema ledger` or `accounting schema result` to print a
schema. Export both with
`accounting schema --out build/schema` when a consumer needs files. A future
documentation site can generate these schemas during its build.

## Contract in one paragraph

A `Leg` is `{asset, quantity, compartment, tag, fee?, label?, liability?, price?, settles_in?}` with
`tag ∈ {trade, transfer, income, expense, borrow, repay, rollover, position, notional, contents}`. An `Event` is
`{id, time, legs}`. A `Link` is `{src, dst}` over event ids and marks one
internal movement, booked when the earlier of the two events is processed.
Within an event borrows are booked first, then trades, then perpetual fills,
then income, then transfers, then repays, then expenses, then fees; across
events with the same
timestamp, input order wins. Events come in atomic groups (`Event[][]`, a bare
event being a group of one): a group's events are applied in order, and
shortness (`negative_position`, a rollover into a short holding,
`negative_liability`) is checked only after the whole group (policy 05 rule 6.3). A `borrow` opens a lot at market value and a
liability of the same quantity under `(liability compartment, asset)`; a `repay`
is a disposal at market that reduces the liability and realizes its value change;
until then the liability is carried at cost. Accrued interest is an expense. The
caller supplies the cash list, the fiat list, income vs expense by sign, the
`fee` flag, globally unique event ids, perpetual fills as `position` size legs
(with their `notional` cash leg, or the venue's price on `pnl` venues), and the
compartment granularity. A perpetual fill books nothing but the P&L its
reduction realizes on a `notional` compartment (policy 05 rule 8). A compartment
in `Policy.opaque_compartments` is one position by value (policy 05 rule 14):
coins moved in carry their basis into it, coins taken out are a redemption at
market value that releases cost up to that value, the excess is `performance`
income, and `contents` legs that leave it empty book the cost left as a
`performance` loss.

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
`schema_version: "0.12"`; generated schemas reject unknown JSON fields. Result
schema versions and their consumer impact are listed in
[docs/migration.md](docs/migration.md).

## Python checks

Install development tools with `python -m pip install -e './pkg[dev]'`, then run
`python scripts/check.py`. The command checks Ruff lint, formatting, and Pyright
for `pkg/src` and `pkg/tests` using that Python interpreter. In the shared Litmus
environment, use `../platform/.venv/bin/python scripts/check.py` (or
`.venv/bin/python scripts/check.py` from platform).

`scripts/check.py` runs `scripts/structure.py` first. It checks the package against
the module map in `structure.toml`, rendered as tables in
[docs/architecture.md](docs/architecture.md): every module needs an entry, no module
may exceed 400 lines unless its entry says `split_pending = N`, and a module may
import only the layers its own layer allows unless its entry says `layering = N`.
Adding a module means adding its entry; `python scripts/structure.py --print-unlisted`
drafts one, and `python scripts/structure.py --render` refreshes the tables, which
the check requires to be current.

Ruff and Pyright settings live in the repository-root `pyproject.toml` under
`[tool.ruff]` and `[tool.pyright]`. Package metadata and dependencies remain in
`pkg/pyproject.toml`. Shared tooling sections are synchronized manually across the
Litmus repositories. Choose the installed development interpreter in your editor.
See the [tooling policy](../platform/docs/technical/python-tooling.md) for details.

## Repository structure

- `pkg/`: installable Python package, including the models, engine, CLI, and tests.
  Synthetic regression inputs and expected results live in `pkg/tests/fixtures/`;
  policy 05's worked examples are in `pkg/tests/fixtures/policy05/`.
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
rounding. CLI presentation remains separate. This is a pure library;
it has no database or HTTP API. Schema exports are generated from its dataclasses
and remain available to offline consumers.

The engine, models, codecs, pricing, and schema generation pass strict
Pyright. No known accounting values use `Any`.
