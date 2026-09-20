"""Structural checks and stable causal ordering for generic basis operations."""

from decimal import Decimal
import heapq
from litmus.accounting.model import Event


def rollover_problems(event: Event) -> list[str]:
  """Validate leg ownership and conservation-safe allocation declarations."""
  operation = event.rollover
  owned = [i for i, leg in enumerate(event.legs) if leg.tag == 'rollover']
  if operation is None:
    return ['rollover legs require a rollover operation'] if owned else []
  issues: list[str] = []
  if not operation.inputs:
    issues.append('rollover requires inputs')
  indices = [x.leg for x in (*operation.inputs, *operation.outputs)]
  if len(indices) != len(set(indices)) or sorted(indices) != owned:
    issues.append('each rollover leg must be referenced exactly once')
  for item in operation.inputs:
    if item.leg < 0 or item.leg >= len(event.legs):
      continue
    leg = event.legs[item.leg]
    if leg.quantity >= 0 or leg.fee:
      issues.append('rollover inputs must be negative non-fee legs')
    if len(item.lots) != len(set(item.lots)):
      issues.append('duplicate selected lot reference')
  for item in operation.outputs:
    if 0 <= item.leg < len(event.legs):
      leg = event.legs[item.leg]
      if leg.quantity <= 0 or leg.fee:
        issues.append('rollover outputs must be positive non-fee legs')
  selected = [lot for item in operation.inputs for lot in item.lots]
  if len(selected) != len(set(selected)):
    issues.append('selected lots must be referenced once per operation')
  fee_indices = operation.capitalized_fee_legs
  if len(fee_indices) != len(set(fee_indices)):
    issues.append('duplicate capitalized fee leg')
  for index in fee_indices:
    if (
      index < 0
      or index >= len(event.legs)
      or not event.legs[index].fee
      or event.legs[index].tag == 'rollover'
    ):
      issues.append('capitalized fee reference requires a negative ordinary fee leg')
  if operation.write_off:
    if (
      operation.outputs or operation.capitalized_costs or operation.capitalized_fee_legs
    ):
      issues.append('write-off requires no outputs or capitalized costs')
  elif not operation.outputs:
    issues.append('missing outputs require explicit write_off')
  elif len(operation.outputs) == 1:
    if operation.outputs[0].allocation not in (None, Decimal(1)):
      issues.append('single output allocation must be 1')
  else:
    allocations = [item.allocation for item in operation.outputs]
    if any(value is None or value < 0 for value in allocations):
      issues.append('multiple outputs require nonnegative allocations')
    elif sum(value for value in allocations if value is not None) != 1:
      issues.append('output allocations must sum exactly to 1')
  if operation.capitalized_costs < 0 or (
    operation.capitalized_costs and not operation.cost_reference
  ):
    issues.append(
      'capitalized costs must be nonnegative and reference supporting evidence/policy'
    )
  return issues


def ordered_events(
  events: tuple[Event, ...],
) -> tuple[tuple[Event, ...], tuple[Event, ...]]:
  """Stable topological order; unresolved/cyclic dependencies remain unbooked."""
  by_id = {event.id: event for event in events}
  ranks = {event.id: index for index, event in enumerate(events)}
  required = {event.id: len(event.depends_on) for event in events}
  children: dict[str, list[str]] = {}
  for event in events:
    for dependency in event.depends_on:
      children.setdefault(dependency, []).append(event.id)
  ready = [
    (event.time, ranks[event.id], event.id) for event in events if not event.depends_on
  ]
  heapq.heapify(ready)
  ordered: list[Event] = []
  while ready:
    _, _, event_id = heapq.heappop(ready)
    event = by_id[event_id]
    ordered.append(event)
    for child_id in children.get(event_id, []):
      required[child_id] -= 1
      if required[child_id] == 0:
        child = by_id[child_id]
        heapq.heappush(ready, (child.time, ranks[child.id], child.id))
  done = {event.id for event in ordered}
  return tuple(ordered), tuple(event for event in events if event.id not in done)
