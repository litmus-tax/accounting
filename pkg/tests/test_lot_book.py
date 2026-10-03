"""
The linear lot book against the pre-#17 lot book: same lots after every
operation, same engine results on randomized ledgers.
"""

import random
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
import pytest
from litmus.accounting import codec, run, FixedPricing
from litmus.accounting.engine import core, lots, perps
from litmus.accounting.model import (
  CostMethod,
  Event,
  Leg,
  Link,
  LotScope,
  Policy,
  Rollover,
  RolloverInput,
  RolloverOutput,
)
from tests import reference_lots

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
METHODS: tuple[CostMethod, ...] = ('fifo', 'lifo', 'hifo', 'average')
KEY = (None, 'aave-ethereum')


def state(book: lots.LotBook | reference_lots.LotBook) -> dict[str, tuple[D, D]]:
  """Open lots by id: quantity and cost."""
  return {
    lot.id: (lot.quantity, lot.cost) for group in book.lots.values() for lot in group
  }


def test_taking_what_is_held_empties_the_key_without_a_residue():
  """The owner's crash: lots needing more than 28 digits to add up are taken exactly, leaving no residue lot."""
  # policy 05 rule 20.3 (quantities are exact); regression for #17 on the owner's ledger
  quantities = [
    '0.01003307145546695169717495902',
    '0.01783806158285971680860112723',
    '0.48322662188320412349422391375',
    '0.000058334647796694',
  ]
  books = [lots.LotBook('fifo'), reference_lots.LotBook('fifo')]
  for book in books:
    for day, quantity in enumerate(quantities):
      book.open(
        KEY,
        quantity=D(quantity),
        cost=D(quantity),
        time=T0 + timedelta(days=day),
        event=f'e{day}',
      )
    book.take(KEY, -sum((D(q) for q in quantities), D(0)))
  new, old = books
  assert state(new) == state(old) == {}
  assert new.position(KEY) == 0 and new.residues == []


def fields(
  consumed: lots.Consumed | reference_lots.Consumed,
) -> tuple[D, D, tuple[str, ...]]:
  """A consumption as comparable fields."""
  return (consumed.quantity, consumed.cost, consumed.lots)


def quantity(rng: random.Random) -> D:
  """A token quantity below a million with up to 18 decimals (rebasing-sized included)."""
  decimals = rng.choice([2, 6, 18, 18])
  return D(rng.randint(1, 10**6 * 10**decimals)).scaleb(-decimals) / D(
    rng.choice([1, 1, 1, 7])
  )


@pytest.mark.parametrize('method', METHODS)
@pytest.mark.parametrize('seed', range(30))
def test_the_book_matches_the_reference_after_every_operation(
  method: CostMethod, seed: int
):
  """Opens, applies, takes and moves (recreating older lots) leave the same lots as the pre-#17 book."""
  rng = random.Random(seed)
  new, old = lots.LotBook(method), reference_lots.LotBook(method)
  keys = [(None, 'A'), ('x', 'A'), ('y', 'A')]
  for step in range(120):
    key = rng.choice(keys)
    when = T0 + timedelta(hours=rng.randint(0, 2000))
    action = rng.random()
    if action < 0.45:
      q = quantity(rng) * rng.choice([1, 1, 1, -1])
      value = q * D(rng.randint(1, 5000)) / D(7)
      a = new.apply(key, quantity=q, value=value, time=when, event=f's{step}')
      b = old.apply(key, quantity=q, value=value, time=when, event=f's{step}')
      assert fields(a.closed) + (a.position,) == fields(b.closed) + (b.position,)
    elif action < 0.75:
      held = old.position(key)
      if held <= 0:
        continue
      q = min(held, quantity(rng)) if rng.random() < 0.7 else held
      a = new.take(key, q)
      b = old.take(key, q)
      assert [(x.id, t, s) for x, t, s in a] == [(x.id, t, s) for x, t, s in b]
    else:
      dst = rng.choice([k for k in keys if k != key])
      held = old.position(key)
      if held <= 0:
        continue
      q = min(held, quantity(rng))
      a_taken, a_created = new.move(key, dst, q)
      b_taken, b_created = old.move(key, dst, q)
      assert fields(a_taken) == fields(b_taken)
      assert [x.id for x in a_created] == [x.id for x in b_created]
    assert state(new) == state(old), step
    for k in keys:
      assert all(lot.quantity != 0 for lot in new.lots.get(k, []))
      assert new.position(k) == old.position(k)
      assert new.totals.get(k, D(0)) == exact_total(new, k)
  assert new.open_lots() == old.open_lots()


def exact_total(book: lots.LotBook, key: lots.LotKey) -> D:
  """The exact sum of a key's lot quantities."""
  from litmus.accounting.engine.arithmetic import exact_sum

  return exact_sum(lot.quantity for lot in book.lots.get(key, []))


def ledger(seed: int) -> tuple[list[Event], list[Link]]:
  """A random ledger: trades, rebasing income, expenses, fees and linked transfers whose destination clock runs ahead."""
  rng = random.Random(seed)
  events: list[Event] = []
  links: list[Link] = []
  for i in range(150):
    when = T0 + timedelta(hours=i * 3)
    comp = rng.choice(['w', 'v'])
    action = rng.random()
    if action < 0.35:
      q = quantity(rng)
      side = rng.choice([1, -1])
      events.append(
        Event(
          f't{i}',
          when,
          (
            Leg('ETH', side * q, comp, 'trade'),
            Leg('EUR', -side * q * 3000, comp, 'trade'),
          ),
        )
      )
    elif action < 0.6:
      events.append(
        Event(
          f'r{i}',
          when,
          (Leg('aave-ethereum', quantity(rng), comp, 'income', label='rebasing'),),
        )
      )
    elif action < 0.75:
      events.append(
        Event(
          f'x{i}',
          when,
          (Leg('ETH', -quantity(rng), comp, 'expense', fee=True, label='gas'),),
        )
      )
    elif action < 0.9:
      asset = rng.choice(['ETH', 'aave-ethereum'])
      q = quantity(rng)
      other = 'v' if comp == 'w' else 'w'
      events.append(
        Event(f'o{i}', when + timedelta(hours=1), (Leg(asset, -q, comp, 'transfer'),))
      )
      events.append(Event(f'i{i}', when, (Leg(asset, q, other, 'transfer'),)))
      links.append(Link(f'o{i}', f'i{i}'))
    elif action < 0.95:
      events.append(
        Event(
          f'd{i}',
          when,
          (Leg('aave-ethereum', -quantity(rng), comp, 'expense', label='interest'),),
        )
      )
    elif action < 0.98:
      q = quantity(rng)
      events.append(
        Event(
          f'rv{i}',
          when,
          (
            Leg('aave-ethereum', -q, comp, 'rollover'),
            Leg('aToken', q * D('1.0003'), comp, 'rollover'),
          ),
          rollover=Rollover(inputs=(RolloverInput(0),), outputs=(RolloverOutput(1),)),
        )
      )
    else:
      events.append(
        Event(f'z{i}', when, (Leg('ETH', D(0), comp, 'income', label='zero'),))
      )
  return events, links


@pytest.mark.parametrize('scope', ['global', 'compartment'])
@pytest.mark.parametrize('method', METHODS)
@pytest.mark.parametrize('seed', range(12))
def test_engine_results_match_the_reference_book(
  monkeypatch: pytest.MonkeyPatch, method: CostMethod, scope: LotScope, seed: int
):
  """On randomized ledgers the engine's result is byte-identical with either lot book."""
  events, links = ledger(seed)
  policy = Policy(
    cost_method=method,
    lot_scope=scope,
    functional_currency='EUR',
    minor_unit=2,
    position_assets=('ETH', 'aave-ethereum'),
  )
  pricing = FixedPricing(
    {('ETH', 'EUR'): '3000.123456789', ('aave-ethereum', 'EUR'): '0.999876543'}
  )
  expected = codec.dump_result(run(events, links=links, policy=policy, pricing=pricing))
  monkeypatch.setattr(core, 'LotBook', reference_lots.LotBook)
  monkeypatch.setattr(perps, 'LotBook', reference_lots.LotBook)
  actual = codec.dump_result(run(events, links=links, policy=policy, pricing=pricing))
  if method == 'average':
    # The reference tracks origins; under `average` the engine no longer does
    # (policy 05 rule 18.2). Everything else must still match.
    actual, expected = without_origins(actual), without_origins(expected)
  assert actual == expected


def without_origins(result: str) -> object:
  """A result's JSON with every `origins` list dropped."""
  import json

  def strip(node: object) -> object:
    """Drop `origins` keys recursively."""
    if isinstance(node, dict):
      return {k: strip(v) for k, v in node.items() if k != 'origins'}  # type: ignore[union-attr]
    if isinstance(node, list):
      return [strip(v) for v in node]  # type: ignore[union-attr]
    return node

  return strip(json.loads(result))


def test_position_is_the_exact_sum_where_the_lots_need_more_digits():
  """Where lot quantities need more than 28 digits to add up, the position is still their exact sum."""
  # policy 05 rule 20.3: quantities are never rounded
  books = [lots.LotBook('fifo'), reference_lots.LotBook('fifo')]
  for book in books:
    for day, q in enumerate(
      ['0.0100330714554669516971749590', '12.48322662188320412349422391']
    ):
      book.open(
        KEY, quantity=D(q), cost=D(1), time=T0 + timedelta(days=day), event=f'e{day}'
      )
  new, old = books
  assert new.position(KEY) == old.position(KEY) == D('12.4932596933386710751913988690')
  assert new.totals[KEY] == new.position(KEY)


def test_an_average_pool_is_quantity_and_cost_only():
  """Under `average` lots carry no origins, so every operation is O(1) per pool."""
  # policy 05 rule 18.2
  events = [
    Event(
      f'b{i}',
      T0 + timedelta(hours=i),
      (Leg('ETH', D('0.1'), 'w', 'trade'), Leg('EUR', D(-300 - i), 'w', 'trade')),
    )
    for i in range(50)
  ] + [
    Event(
      'sell',
      T0 + timedelta(days=5),
      (Leg('ETH', D('-1'), 'w', 'trade'), Leg('EUR', D(4000), 'w', 'trade')),
    )
  ]
  policy = Policy(cost_method='average', lot_scope='global', functional_currency='EUR')
  r = run(events, policy=policy, pricing=FixedPricing({}))
  (pool,) = r.lots
  assert pool.origins == () and pool.quantity == D('4')
  assert pool.cost == sum((D(300 + i) for i in range(50)), D(0)) * D('0.8')
