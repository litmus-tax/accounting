"""
Structural checks of a ledger that need no prices: invalid and duplicate
events, dependencies, and links (unknown events, conflicts, mismatches); and
the atomic groups the valid events are booked in.
"""

from dataclasses import dataclass
from datetime import datetime

from litmus.accounting.engine.arithmetic import fixed_context
from litmus.accounting.engine.operations import ordered_events, rollover_problems
from litmus.accounting.model import (
  Event,
  Events,
  ExceptionItem,
  Group,
  Leg,
  Link,
)
from typing_extensions import Sequence

Linked = dict[str, tuple[Link, Event, Event]]
"""Event id to the link it takes part in, with the resolved source and destination."""


@dataclass(frozen=True)
class Checked:
  """Outcome of the structural checks that need no prices."""

  events: tuple[Event, ...]
  """Valid events, in booking order."""
  groups: tuple[Group, ...]
  """The valid events in booking order, as the atomic groups they are booked in."""
  linked: Linked
  exceptions: tuple[ExceptionItem, ...]
  complete: bool


def problems(event: Event) -> list[str]:
  """Reasons an event is invalid, empty when fine."""
  out: list[str] = rollover_problems(event)
  if len(event.depends_on) != len(set(event.depends_on)):
    out.append('duplicate dependency reference')
  if event.time.tzinfo is None:
    out.append('naive timestamp')
  if not event.legs:
    out.append('no legs')
  for i, leg in enumerate(event.legs):
    if leg.quantity == 0:
      out.append(f'leg {i}: zero quantity')
    elif leg.fee and leg.quantity > 0:
      out.append(f'leg {i}: fee must be negative')
    elif leg.tag == 'income' and leg.quantity < 0:
      out.append(f'leg {i}: income must be positive')
    elif leg.tag == 'expense' and leg.quantity > 0:
      out.append(f'leg {i}: expense must be negative')
    elif (
      leg.tag == 'borrow' and leg.quantity < 0 and (leg.label != 'interest' or leg.fee)
    ):
      out.append(f'leg {i}: borrow must be positive')
    elif leg.tag == 'repay' and leg.quantity > 0:
      out.append(f'leg {i}: repay must be negative')
    elif leg.liability is not None and leg.tag not in ('borrow', 'repay'):
      out.append(f'leg {i}: only borrow and repay legs name a liability')
    elif leg.fee and leg.tag in ('position', 'notional'):
      out.append(f'leg {i}: a {leg.tag} leg is never a fee')
    elif (leg.price is not None or leg.settles_in is not None) and (
      leg.tag != 'position' or leg.price is None or leg.settles_in is None
    ):
      out.append(f'leg {i}: price and settles_in go together, on position legs only')
    elif leg.price is not None and leg.price <= 0:
      out.append(f'leg {i}: price must be positive')
    elif leg.basis != 'unclassified' and leg.tag != 'transfer':
      out.append(f'leg {i}: only transfer legs have a basis')
    elif (leg.cost is not None or leg.acquired is not None) and (
      leg.basis != 'carried' or leg.quantity < 0
    ):
      out.append(f'leg {i}: cost and acquired go on inbound carried transfers only')
    elif leg.basis == 'carried' and leg.quantity > 0 and leg.cost is None:
      out.append(f'leg {i}: an inbound carried transfer needs its cost')
    elif leg.cost is not None and leg.cost < 0:
      out.append(f'leg {i}: cost must not be negative')
    elif leg.acquired is not None and leg.acquired.tzinfo is None:
      out.append(f'leg {i}: naive acquisition time')
  return out


def members(events: Events) -> list[tuple[int, Event]]:
  """Each event with the index of the atomic group it came in, in input order."""
  out: list[tuple[int, Event]] = []
  for index, item in enumerate(events):
    if isinstance(item, Event):
      out.append((index, item))
    else:
      out.extend((index, event) for event in item)
  return out


def regroup(ordered: Sequence[Event], group_of: dict[str, int]) -> tuple[Group, ...]:
  """
  The atomic groups of events in booking order (policy 05 rule 6.3): consecutive
  events of one input group and one time. A group that time order or a
  dependency splits is booked as several, each atomic.
  """
  groups: list[list[Event]] = []
  last: tuple[int, datetime] | None = None
  for event in ordered:
    key = (group_of[event.id], event.time)
    if key != last:
      groups.append([])
      last = key
    groups[-1].append(event)
  return tuple(tuple(group) for group in groups)


def check(events: Events, links: Sequence[Link] = ()) -> Checked:
  """
  Structural validation: duplicate ids, invalid events, links to unknown events
  and links that do not conserve quantity per asset. No prices are needed.
  `events` are atomic groups (`model.Events`); a bare event is a group of one.
  """
  exceptions: list[ExceptionItem] = []
  complete = True
  by_id: dict[str, Event] = {}
  valid: list[Event] = []
  group_of: dict[str, int] = {}
  for group, e in members(events):
    if e.id in by_id:
      complete = False
      exceptions.append(
        ExceptionItem(
          'duplicate_id', e.id, f'duplicate event id {e.id!r}; later one skipped'
        )
      )
      continue
    by_id[e.id] = e
    group_of[e.id] = group
    found = problems(e)
    if found:
      complete = False
      exceptions.append(ExceptionItem('invalid_event', e.id, '; '.join(found)))
    else:
      valid.append(e)
  for event in tuple(valid):
    for dependency in event.depends_on:
      parent = by_id.get(dependency)
      if parent is None or parent.time > event.time:
        complete = False
        exceptions.append(
          ExceptionItem(
            'invalid_event', event.id, f'unknown or later dependency {dependency!r}'
          )
        )
        valid.remove(event)
        break
  ordered, blocked = ordered_events(tuple(valid))
  for event in blocked:
    complete = False
    exceptions.append(
      ExceptionItem(
        'invalid_event', event.id, 'cyclic or invalid dependency; operation not booked'
      )
    )
  valid = list(ordered)
  linked: Linked = {}
  for link in links:
    src, dst = by_id.get(link.src), by_id.get(link.dst)
    if src is None or dst is None:
      exceptions.append(
        ExceptionItem(
          'unknown_event',
          None,
          f'link references unknown event: {link.src} -> {link.dst}',
          {'src': link.src, 'dst': link.dst},
        )
      )
      continue
    taken = [e.id for e in (src, dst) if e.id in linked]
    if taken or src.id == dst.id:
      # policy 01 term 2: a record is a side of at most one link.
      complete = False
      exceptions.append(
        ExceptionItem(
          'link_conflict',
          src.id,
          f'link {src.id} -> {dst.id} not booked: '
          + (
            f'{", ".join(taken)} already in another link'
            if taken
            else 'an event cannot link to itself'
          ),
          {'src': src.id, 'dst': dst.id},
        )
      )
      continue
    linked[src.id] = linked[dst.id] = (link, src, dst)
    if link.kind == 'swap':
      given, received = sides(src, dst)
      if not given or not received:
        complete = False
        exceptions.append(
          ExceptionItem(
            'link_mismatch',
            src.id,
            f'swap link {src.id} -> {dst.id} needs an outflow and an inflow',
            {'src': src.id, 'dst': dst.id},
          )
        )
      continue
    for asset, a, b in pairs(src, dst):
      if a is None or b is None or a.quantity + b.quantity != 0:
        complete = False
        exceptions.append(
          ExceptionItem(
            'link_mismatch',
            src.id,
            f'link {src.id} -> {dst.id} does not conserve {asset}: out {a and a.quantity}, in {b and b.quantity}',
            {'src': src.id, 'dst': dst.id, 'asset': asset},
          )
        )
  return Checked(
    tuple(valid), regroup(valid, group_of), linked, tuple(exceptions), complete
  )


def sides(src: Event, dst: Event) -> tuple[list[Leg], list[Leg]]:
  """A linked pair's transfer legs: the outflow from `src` and the inflow into `dst`, fees excluded."""
  given = [l for l in src.legs if l.tag == 'transfer' and not l.fee and l.quantity < 0]
  received = [
    l for l in dst.legs if l.tag == 'transfer' and not l.fee and l.quantity > 0
  ]
  return given, received


def pairs(src: Event, dst: Event) -> list[tuple[str, Leg | None, Leg | None]]:
  """Transfer legs of a linked pair matched by asset: outflow from `src`, inflow into `dst`."""
  out = {
    l.asset: l for l in src.legs if l.tag == 'transfer' and not l.fee and l.quantity < 0
  }
  inn = {
    l.asset: l for l in dst.legs if l.tag == 'transfer' and not l.fee and l.quantity > 0
  }
  return [
    (asset, out.get(asset), inn.get(asset)) for asset in sorted(out.keys() | inn.keys())
  ]


@fixed_context
def validate(events: Events, links: Sequence[Link] = ()) -> list[ExceptionItem]:
  """Structural problems of a ledger, without prices. Empty when the ledger is well formed."""
  return list(check(events, links).exceptions)
