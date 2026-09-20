"""
Command line: `accounting [--json] {run,validate,value,schema} ...`.

A `typer` app. `--json` is a callback option on the app and an option on every
verb, so it is accepted before or after the verb. Completed calculations report
findings in their result. Runtime failures exit one; parser usage errors exit two.
"""

import sys
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing_extensions import Annotated, Sequence
import pydantic
import typer
from litmus.accounting import codec, schema
from litmus.accounting.engine import run, validate
from litmus.accounting.model import Result, Policy, Valuation, ExceptionItem, Ledger
from litmus.accounting.pricing import TablePricing, PriceGap
from litmus.accounting.valuation import value

app = typer.Typer(
  name='accounting',
  help='Pure accounting engine over a ledger-input JSON file.',
  add_completion=False,
  pretty_exceptions_enable=False,
  context_settings={'help_option_names': ['-h', '--help']},
)


class Usage(Exception):
  """Invalid input or usage; the CLI exits 2 with the message on stderr."""


def fail(message: str, code: int) -> typer.Exit:
  """Print `message` on stderr and build the exit."""
  typer.echo(f'error: {message}', err=True)
  return typer.Exit(code)


def read(path: str) -> str:
  """Read a file or raise `Usage`."""
  try:
    return Path(path).read_text()
  except OSError as e:
    raise Usage(f'cannot read {path}: {e.strerror}') from e


def parse_duration(text: str) -> timedelta:
  """An ISO 8601 duration (`P1D`, `PT36H`) or a number of seconds."""
  adapter = pydantic.TypeAdapter(timedelta)
  try:
    return adapter.validate_strings(text)
  except pydantic.ValidationError:
    pass
  try:
    return timedelta(seconds=float(text))
  except ValueError as e:
    raise Usage(
      f'invalid duration {text!r}: use ISO 8601 (P1D, PT36H) or seconds'
    ) from e


def load(
  ledger: str, policy: str | None, prices: str | None, max_age: str | None
) -> Ledger:
  """Parse the ledger file, applying the `--policy`, `--prices` and `--max-age` overrides."""
  try:
    doc = codec.parse_ledger(read(ledger))
    if policy:
      doc = replace(doc, policy=codec.parse_policy(read(policy)))
    if prices:
      doc = replace(doc, prices=tuple(codec.parse_prices(read(prices))))
  except pydantic.ValidationError as e:
    raise Usage(f'invalid input\n{e}') from e
  if max_age is not None:
    doc = replace(doc, max_age=parse_duration(max_age))
  return doc


def policy_of(ledger: Ledger) -> Policy:
  """The ledger's policy or a usage error."""
  if ledger.policy is None:
    raise Usage('no policy: pass --policy or embed "policy" in the ledger')
  return ledger.policy


def exception_lines(items: Sequence[ExceptionItem]) -> list[str]:
  """One line per exception."""
  return [f'exceptions: {len(items)}'] + [
    f'  [{x.code}] {x.event or "-"}: {x.message}' for x in items
  ]


def summary(result: Result) -> str:
  """Human-readable digest of a result."""
  p = result.policy
  fc = p.functional_currency
  pnl = sum((r.pnl for r in result.realized), Decimal(0))
  income = sum((f.value for f in result.flows if f.kind == 'income'), Decimal(0))
  expense = sum((f.value for f in result.flows if f.kind == 'expense'), Decimal(0))
  lines = [
    f'policy: {p.cost_method} / {p.lot_scope} lots / {fc} / fees {p.fee_treatment}',
    f'realized pnl: {pnl} {fc} over {len(result.realized)} disposals',
    f'income: {income} {fc}   expense: {expense} {fc}',
    f'open lots: {len(result.lots)}',
  ]
  for lot in result.lots:
    where = f' @ {lot.compartment}' if lot.compartment else ''
    lines.append(
      f'  {lot.id}: {lot.quantity} {lot.asset}{where} cost {lot.cost} since {lot.acquired.date()}'
    )
  if result.liabilities:
    lines.append(f'liabilities: {len(result.liabilities)}')
    for l in result.liabilities:
      facility = f' ({l.label})' if l.label else ''
      lines.append(
        f'  {l.id}: {l.quantity} {l.asset} @ {l.compartment}{facility} basis {l.cost} since {l.opened.date()}'
      )
  lines.append(f'prices used: {len(result.prices)}   complete: {result.complete}')
  return '\n'.join([*lines, *exception_lines(result.exceptions)])


def valuation_summary(v: Valuation, policy: Policy) -> str:
  """Human-readable digest of a valuation."""
  fc = policy.functional_currency
  total = sum((p.value for p in v.positions if p.value is not None), Decimal(0))
  unrealized = sum(
    (p.unrealized for p in v.positions if p.unrealized is not None), Decimal(0)
  )
  lines = [
    f'valuation at {v.at.isoformat()}: {total} {fc}, unrealized {unrealized} {fc}'
  ]
  for p in v.positions:
    where = f' @ {p.compartment}' if p.compartment else ''
    lines.append(
      f'  {p.lot}: {p.quantity} {p.asset}{where} cost {p.cost} value {p.value} unrealized {p.unrealized}'
    )
  if v.liabilities:
    owed = sum((p.value for p in v.liabilities if p.value is not None), Decimal(0))
    lines.append(f'liabilities: {owed} {fc}')
    for p in v.liabilities:
      lines.append(
        f'  {p.liability}: {p.quantity} {p.asset} @ {p.compartment} cost {p.cost} value {p.value} unrealized {p.unrealized}'
      )
  lines.append(f'prices used: {len(v.prices)}   complete: {v.complete}')
  return '\n'.join([*lines, *exception_lines(v.exceptions)])


JsonFlag = Annotated[
  bool | None, typer.Option('--json', help='Emit JSON instead of the human summary.')
]
LedgerArg = Annotated[
  str,
  typer.Argument(
    metavar='LEDGER',
    help='JSON file with "events", optional "links", "policy", "prices", "max_age".',
  ),
]
PolicyOpt = Annotated[
  str | None,
  typer.Option('--policy', help="JSON policy file (overrides the ledger's)."),
]
PricesOpt = Annotated[
  str | None,
  typer.Option('--prices', help="JSON list of price records (overrides the ledger's)."),
]
MaxAgeOpt = Annotated[
  str | None,
  typer.Option(
    '--max-age',
    help="Oldest usable price relative to the requested time, ISO 8601 (P1D) or seconds (overrides the ledger's).",
  ),
]
StrictOpt = Annotated[
  bool, typer.Option('--strict', help='Stop at the first price gap.')
]


def wants_json(ctx: typer.Context, flag: bool | None) -> bool:
  """`--json` after the verb wins; otherwise the value given before it."""
  return bool(ctx.obj) if flag is None else flag


@app.callback()
def main_options(ctx: typer.Context, json: JsonFlag = None):
  """Pure accounting engine over a ledger-input JSON file."""
  ctx.obj = bool(json)


@app.command('run')
def run_(
  ctx: typer.Context,
  ledger: LedgerArg,
  policy: PolicyOpt = None,
  prices: PricesOpt = None,
  max_age: MaxAgeOpt = None,
  strict: StrictOpt = False,
  json: JsonFlag = None,
):
  """Compute lots, liabilities, realized PnL, flows and exceptions."""
  doc = load(ledger, policy, prices, max_age)
  pol = policy_of(doc)
  try:
    result = run(
      doc.events,
      links=doc.links,
      policy=pol,
      pricing=TablePricing(doc.prices, max_age=doc.max_age),
      strict=strict,
    )
  except PriceGap as e:
    raise fail(str(e), 1) from e
  typer.echo(codec.dump_result(result) if wants_json(ctx, json) else summary(result))
  raise typer.Exit(0)


@app.command('validate')
def validate_(
  ctx: typer.Context,
  ledger: LedgerArg,
  policy: PolicyOpt = None,
  prices: PricesOpt = None,
  json: JsonFlag = None,
):
  """Check the ledger shape and structure without prices."""
  doc = load(ledger, policy, prices, None)
  problems = validate(doc.events, doc.links)
  if wants_json(ctx, json):
    typer.echo(codec.dump_exceptions(problems))
  else:
    typer.echo(
      '\n'.join(['valid' if not problems else 'invalid', *exception_lines(problems)])
    )
  raise typer.Exit(0)


@app.command('value')
def value_(
  ctx: typer.Context,
  ledger: LedgerArg,
  at: Annotated[
    str, typer.Option('--at', help='Valuation time, ISO 8601 with timezone.')
  ],
  policy: PolicyOpt = None,
  prices: PricesOpt = None,
  max_age: MaxAgeOpt = None,
  strict: StrictOpt = False,
  json: JsonFlag = None,
):
  """Period-end valuation of the open lots and liabilities."""
  doc = load(ledger, policy, prices, max_age)
  pol = policy_of(doc)
  try:
    when = datetime.fromisoformat(at)
  except ValueError as e:
    raise Usage(f'invalid --at: {e}') from e
  if when.tzinfo is None:
    raise Usage('invalid --at: timestamp must carry a timezone')
  pricing = TablePricing(doc.prices, max_age=doc.max_age)
  try:
    result = run(
      doc.events, links=doc.links, policy=pol, pricing=pricing, strict=strict
    )
    valuation = value(
      result.lots,
      liabilities=result.liabilities,
      at=when,
      policy=pol,
      pricing=pricing,
      strict=strict,
    )
  except PriceGap as e:
    raise fail(str(e), 1) from e
  typer.echo(
    codec.dump_valuation(valuation)
    if wants_json(ctx, json)
    else valuation_summary(valuation, pol)
  )
  raise typer.Exit(0)


@app.command('schema')
def schema_(
  ctx: typer.Context,
  name: Annotated[
    str,
    typer.Argument(help='Which schema to print: ledger, result or valuation.'),
  ] = 'ledger',
  out: Annotated[
    str | None,
    typer.Option('--out', metavar='DIR', help='Write all schema files into DIR.'),
  ] = None,
  check: Annotated[
    str | None,
    typer.Option(
      '--check', metavar='DIR', help='Fail if the files in DIR differ from the code.'
    ),
  ] = None,
  json: JsonFlag = None,
):
  """Print, write or check the JSON schema files."""
  if name != 'ledger' and name != 'result' and name != 'valuation':
    raise Usage(f'unknown schema {name!r}; one of {", ".join(schema.NAMES)}')
  if check:
    stale = schema.stale(Path(check))
    if stale:
      typer.echo(
        '\n'.join(['stale schema files:', *(f'  {p}' for p in stale)]), err=True
      )
      raise typer.Exit(1)
    typer.echo('[]' if wants_json(ctx, json) else 'schema files up to date')
    raise typer.Exit(0)
  if out:
    for p in schema.write(Path(out)):
      typer.echo(str(p))
    raise typer.Exit(0)
  typer.echo(schema.render(name), nl=False)
  raise typer.Exit(0)


def main(argv: list[str] | None = None) -> int:
  """
  Run the app on `argv` (default `sys.argv[1:]`) and return the exit code.

  Non-standalone mode: `typer.Exit` comes back as the code, parser errors
  (`TyperException`, exit code 2) and our own `Usage` map to 2, anything else
  unexpected to 1.
  """
  try:
    code = app(args=argv, standalone_mode=False)
  except typer.TyperException as e:
    show = getattr(e, 'show', None)
    if show is not None:
      show()
    else:
      typer.echo(f'error: {e.format_message()}', err=True)
    return e.exit_code
  except typer.Abort:
    return 1
  except Usage as e:
    typer.echo(f'error: {e}', err=True)
    return 2
  except Exception as e:  # noqa: BLE001
    typer.echo(f'error: {type(e).__name__}: {e}', err=True)
    return 1
  return code if isinstance(code, int) else 0


def entry():
  """Console-script entry point."""
  sys.exit(main())


if __name__ == '__main__':
  entry()
