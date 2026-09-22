# Architecture

`litmus-accounting` is a pure calculation engine: callers hand it selected facts, policy and prices, and it returns lots, realized PnL, flows, valuations and an exceptions report without fetching, interpreting sources or choosing policy. `model` is the leaf contract every other module builds on; `codec` and `schema` are its JSON boundary; `pricing` is the protocol the caller implements; `engine/` books the ledger and `valuation` prices open positions through the same protocol; `cli` wraps all of that. The behavioral reference is the [technical documentation](index.md); this page holds the module map.

## Module map

`scripts/check.py` runs `scripts/structure.py` first. `structure.toml` is the source of truth: it declares the layers (top-level packages, or root modules named by their stem), the layers each may import, and one entry per module with its responsibility. The check fails when a module under `pkg/src/litmus/accounting` has no entry, when an entry names a module that no longer exists, when a module exceeds 400 lines without `split_pending = N`, when a module imports a layer outside its allowance without `layering = N` (issue N tracks the violation; remove the import and the marker together), or when the tables below are stale. Paths are relative to `pkg/src/litmus/accounting/`; an `__init__.py` holding nothing but a docstring needs no entry. `python scripts/structure.py --print-unlisted` drafts entries for unknown modules and `python scripts/structure.py --render` regenerates the tables below. Edit `structure.toml`, not the tables.

<!-- structure:begin -->
### Root

| Module | Responsibility | May import |
| --- | --- | --- |
| `__init__.py` | Exposes `calculation`, `valuation` and `pricing` as package attributes. | `engine`, `valuation`, `pricing` |
| `cli.py` | `accounting [--json] {run,validate,value,schema}` Typer app; findings in results, runtime failures exit 1, usage errors exit 2. | `codec`, `schema`, `engine`, `model`, `pricing`, `valuation` |
| `codec.py` | JSON in and out through `pydantic.TypeAdapter` over the model dataclasses (Decimal strings, ISO datetimes). | `model` |
| `model.py` | Frozen-dataclass ledger input (events, legs, links, liabilities, policy) and output records; the one schema for library, CLI and JSON. (split pending: #0) | — |
| `pricing.py` | The `Pricing` protocol, the policy-applying `Valuer`, recorded price answers and `PriceGap`. | `model` |
| `schema.py` | JSON Schema for ledger, result and valuation generated from the model, for `accounting schema`. | `codec` |
| `valuation.py` | Period-end valuation of open lots and liabilities at one point in time through the pricing protocol. | `model`, `pricing`, `engine` |

### engine

| Module | Responsibility | May import |
| --- | --- | --- |
| `engine/__init__.py` | Exposes `calculation` and re-exports `run` and `validate`. | `engine`, `model`, `pricing` |
| `engine/arithmetic.py` | Exact finite-decimal sums and differences for conservation after bounded division. | `engine`, `model`, `pricing` |
| `engine/calculation.py` | The pure engine: events, links, policy and pricing to lots, realized PnL, flows, internal moves, prices used and exceptions. (split pending: #0) | `engine`, `model`, `pricing` |
| `engine/lots.py` | `LotBook`: open lots per key, sign-crossing closes and FIFO/LIFO/HIFO/average consumption order. | `engine`, `model`, `pricing` |
| `engine/operations.py` | Structural checks and stable causal ordering for generic basis operations. | `engine`, `model`, `pricing` |
| `engine/rounding.py` | Rounds functional-currency money outputs to the minor unit, recomputing derived amounts from rounded parts. | `engine`, `model`, `pricing` |
<!-- structure:end -->
