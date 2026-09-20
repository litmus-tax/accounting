"""
JSON schema for the ledger input, the result and the valuation, generated from
the model dataclasses. Use `accounting schema` to print a schema or
`accounting schema --out DIR` to export all three on demand.
"""

import json
from pathlib import Path
from typing_extensions import Literal
from pydantic import JsonValue, TypeAdapter
from litmus.accounting import codec

SchemaName = Literal['ledger', 'result', 'valuation']
NAMES: tuple[SchemaName, ...] = ('ledger', 'result', 'valuation')
BASE_ID = 'https://litmus-tax.github.io/accounting/schema/'


json_object = TypeAdapter(dict[str, JsonValue])


def schema(name: SchemaName) -> dict[str, JsonValue]:
  """The JSON schema document for one of the three shapes."""
  if name == 'ledger':
    body = codec.ledger_adapter.json_schema(mode='validation')
    title = 'Ledger input'
  elif name == 'result':
    body = codec.result_adapter.json_schema(mode='serialization')
    title = 'Accounting result'
  else:
    body = codec.valuation_adapter.json_schema(mode='serialization')
    title = 'Period-end valuation'
  return {
    **json_object.validate_python(body),
    '$schema': 'https://json-schema.org/draft/2020-12/schema',
    '$id': f'{BASE_ID}{name}.schema.json',
    'title': title,
  }


def render(name: SchemaName) -> str:
  """Schema as formatted JSON text."""
  return json.dumps(schema(name), indent=2, sort_keys=True) + '\n'


def path(directory: Path, name: SchemaName) -> Path:
  """File path of one schema under `directory`."""
  return directory / f'{name}.schema.json'


def write(directory: Path) -> list[Path]:
  """Write all schema files under `directory`; returns the paths written."""
  directory.mkdir(parents=True, exist_ok=True)
  out: list[Path] = []
  for name in NAMES:
    p = path(directory, name)
    p.write_text(render(name))
    out.append(p)
  return out


def stale(directory: Path) -> list[Path]:
  """Schema files under `directory` that are missing or differ from the code."""
  return [
    path(directory, n)
    for n in NAMES
    if not path(directory, n).exists() or path(directory, n).read_text() != render(n)
  ]
