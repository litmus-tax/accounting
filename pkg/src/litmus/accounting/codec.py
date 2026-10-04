"""
JSON in and out through `pydantic.TypeAdapter` over the model dataclasses.

Decimals are strings on the wire (accepted as numbers too), datetimes are ISO
8601. The same adapters generate JSON Schema exports, so what the CLI
validates and what `accounting schema` emits cannot drift apart.
"""

from typing_extensions import TypeVar
import pydantic
from litmus.accounting.model import (
  RESULT_VERSION,
  Ledger,
  Policy,
  PriceRecord,
  Result,
  ExceptionItem,
)

ledger_adapter = pydantic.TypeAdapter(Ledger)
policy_adapter = pydantic.TypeAdapter(Policy)
prices_adapter = pydantic.TypeAdapter(list[PriceRecord])
result_adapter = pydantic.TypeAdapter(Result)
exceptions_adapter = pydantic.TypeAdapter(list[ExceptionItem])
json_adapter = pydantic.TypeAdapter[pydantic.JsonValue](pydantic.JsonValue)


def parse_ledger(text: str | bytes) -> Ledger:
  """Validate a ledger document; raises `pydantic.ValidationError`."""
  return ledger_adapter.validate_json(text)


def parse_policy(text: str | bytes) -> Policy:
  """Validate a policy document; raises `pydantic.ValidationError`."""
  return policy_adapter.validate_json(text)


def parse_prices(text: str | bytes) -> list[PriceRecord]:
  """Validate a price table; raises `pydantic.ValidationError`."""
  return prices_adapter.validate_json(text)


class SchemaVersionError(ValueError):
  """A result written under a schema version this engine does not read."""


def parse_result(text: str | bytes) -> Result:
  """
  Validate a result document (the output of `accounting run --json`).

  Only the current result schema is read; a result sealed under another version
  is refused, never read best-effort (policy 05 rule 27.2).

  Raises:
    SchemaVersionError: The document carries another `schema_version`.
    pydantic.ValidationError: The document does not match the schema.
  """
  try:
    document = json_adapter.validate_json(text)
  except pydantic.ValidationError:
    document = None
  version = document.get('schema_version') if isinstance(document, dict) else None
  if isinstance(document, dict) and version != RESULT_VERSION:
    raise SchemaVersionError(
      f'result schema {version!r} is not readable by this engine, which reads '
      f'{RESULT_VERSION!r}; replay it with the engine version it was sealed with'
    )
  return result_adapter.validate_json(text)


def dump_result(result: Result) -> str:
  """Serialize a result as indented JSON."""
  return result_adapter.dump_json(result, indent=2).decode()


def dump_exceptions(items: list[ExceptionItem]) -> str:
  """Serialize an exceptions list as indented JSON."""
  return exceptions_adapter.dump_json(items, indent=2).decode()


Value = TypeVar('Value')


def to_jsonable(
  adapter: pydantic.TypeAdapter[Value], value: Value
) -> pydantic.JsonValue:
  """Plain JSON-compatible Python data for `value` (strings for decimals and datetimes)."""
  return json_adapter.validate_json(adapter.dump_json(value))
