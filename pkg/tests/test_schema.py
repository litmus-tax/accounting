"""Model-generated schemas validate the examples, and results round-trip."""

import json
from pathlib import Path
import jsonschema
import pytest
from litmus.accounting import run, value, schema, codec
from litmus.accounting.pricing import TablePricing
from tests.conftest import t

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = sorted((ROOT / 'examples').glob('*.json'))


@pytest.mark.parametrize('name', schema.NAMES)
def test_generated_schemas_are_valid(name: schema.SchemaName):
  """Every model-generated document is valid JSON Schema."""
  jsonschema.Draft202012Validator.check_schema(schema.schema(name))


@pytest.mark.parametrize('example', EXAMPLES, ids=lambda p: p.stem)
def test_examples_validate_against_the_ledger_schema(example: Path):
  """Every example ledger validates under the model-generated ledger schema with an independent validator."""
  ledger_schema = schema.schema('ledger')
  jsonschema.Draft202012Validator.check_schema(ledger_schema)
  jsonschema.validate(json.loads(example.read_text()), ledger_schema)


@pytest.mark.parametrize('example', EXAMPLES, ids=lambda p: p.stem)
def test_result_round_trip(example: Path):
  """A result dumps to JSON that validates against the result schema and parses back to an equal result."""
  ledger = codec.parse_ledger(example.read_text())
  assert ledger.policy is not None
  pricing = TablePricing(ledger.prices)
  result = run(ledger.events, links=ledger.links, policy=ledger.policy, pricing=pricing)
  text = codec.dump_result(result)
  jsonschema.validate(json.loads(text), schema.schema('result'))
  assert codec.parse_result(text) == result
  valuation = value(result.lots, at=t(31), policy=ledger.policy, pricing=pricing)
  jsonschema.validate(
    json.loads(codec.dump_valuation(valuation)),
    schema.schema('valuation'),
  )


def test_ledger_round_trip():
  """A ledger parses, dumps and parses again to the same dataclasses (numbers accepted as strings or numbers)."""
  text = (ROOT / 'examples' / 'perp.json').read_text()
  ledger = codec.parse_ledger(text)
  again = codec.parse_ledger(codec.ledger_adapter.dump_json(ledger))
  assert again == ledger
  numeric = json.loads(text)
  numeric['events'][0]['legs'][0]['quantity'] = 100000
  assert codec.parse_ledger(json.dumps(numeric)) == ledger


def test_schema_rejects_bad_shapes():
  """The ledger schema rejects an unknown tag and a missing compartment."""
  ledger_schema = schema.schema('ledger')
  bad = {
    'events': [
      {
        'id': 'x',
        'time': '2026-01-01T00:00:00Z',
        'legs': [{'asset': 'BTC', 'quantity': '1', 'compartment': 'A', 'tag': 'fill'}],
      }
    ]
  }
  with pytest.raises(jsonschema.ValidationError):
    jsonschema.validate(bad, ledger_schema)
  bad['events'][0]['legs'][0] = {'asset': 'BTC', 'quantity': '1', 'tag': 'trade'}
  with pytest.raises(jsonschema.ValidationError):
    jsonschema.validate(bad, ledger_schema)
