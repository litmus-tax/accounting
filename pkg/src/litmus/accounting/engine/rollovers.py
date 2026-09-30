"""
Rollovers: atomic basis carries across operations, with allocation by market
value across several outputs (policy 05 positions rule 5.1).
"""

from dataclasses import replace
from decimal import Decimal

from litmus.accounting.engine.arithmetic import difference, exact_sum
from litmus.accounting.engine.lots import split_origins
from litmus.accounting.engine.transfers import Transfers
from litmus.accounting.model import (
  Event,
  Leg,
  Origin,
  Realized,
  Rollover,
  RolloverRecord,
  RolloverSlice,
)
from litmus.accounting.pricing import PriceGap


class Rollovers(Transfers):
  """Rollover operations."""

  def allocations(
    self, event: Event, operation: Rollover
  ) -> tuple[Decimal, ...] | None:
    """
    Each rollover output's share of the carried basis: as given, or, when every
    allocation of several outputs is omitted, by the outputs' market value at the
    operation (policy 05 positions rule 5.1). `None` when a value is missing.
    """
    outputs = operation.outputs
    if len(outputs) < 2 or any(item.allocation is not None for item in outputs):
      return tuple(
        item.allocation if item.allocation is not None else Decimal(1)
        for item in outputs
      )
    try:
      values = [abs(self.market(event, event.legs[item.leg])) for item in outputs]
    except PriceGap as error:
      self.gap(event, error)
      return None
    total = sum(values, Decimal(0))
    if not total:
      return None
    return tuple(value / total for value in values)

  def rollover(self, event: Event):
    """Atomically carry selected basis, retaining a slice for every acquisition."""
    operation = event.rollover
    if operation is None:
      return
    shares = self.allocations(event, operation)
    if shares is None:
      for leg in event.legs:
        if leg.tag == 'rollover':
          self.unbooked(event, leg, 'outputs cannot be valued to allocate basis')
      return

    fee_values: list[tuple[Leg, Decimal]] = []
    try:
      for index in operation.capitalized_fee_legs:
        leg = event.legs[index]
        fee_values.append((leg, self.market(event, leg)))
    except PriceGap as error:
      self.gap(event, error)
      for leg in event.legs:
        self.unbooked(event, leg, 'capitalized fee price gap')
      return
    if exact_sum(-value for _, value in fee_values) > operation.capitalized_costs:
      for leg in event.legs:
        self.unbooked(event, leg, 'capitalized_costs does not cover paid fee value')
      return
    # The operation is atomic: it works on a fork of the keys it touches.
    book = self.book.fork(
      self.key(event.legs[item.leg]) for item in (*operation.inputs, *operation.outputs)
    )
    consumed: list[RolloverSlice] = []
    created: list[RolloverSlice] = []
    reason: str | None = None
    for item in operation.inputs:
      leg = event.legs[item.leg]
      key = self.key(leg)
      candidates = book.lots.get(key, [])
      if leg.asset == self.fc:
        if item.lots:
          reason = 'functional currency has no selectable lots'
          break
        cost = -leg.quantity
        consumed.append(
          RolloverSlice(
            item.leg,
            f'cash:{event.id}:{item.leg}',
            cost,
            cost,
            event.time,
            (Origin(event.id, event.time, cost),),
          )
        )
        continue
      if item.lots and any(
        lot_id not in {lot.id for lot in candidates} for lot_id in item.lots
      ):
        reason = 'selected lot does not exist under the input identity'
        break
      available = sum(
        (lot.quantity for lot in candidates if not item.lots or lot.id in item.lots),
        Decimal(0),
      )
      if available < -leg.quantity or any(lot.quantity < 0 for lot in candidates):
        reason = 'insufficient positive input lots'
        break
      for lot, quantity, cost in book.take(
        key, -leg.quantity, selected=item.lots, exact_basis=True
      ):
        consumed.append(
          RolloverSlice(item.leg, lot.id, quantity, cost, lot.acquired, lot.origins)
        )
    for item in operation.outputs:
      leg = event.legs[item.leg]
      if leg.asset == self.fc or book.sign(self.key(leg)) < 0:
        reason = 'output requires a non-functional-currency, non-short holding'
    if reason:
      for leg in event.legs:
        if leg.tag == 'rollover':
          self.unbooked(event, leg, reason)
      return
    basis_in = exact_sum(piece.cost for piece in consumed)
    allocations = shares
    inherited_remaining = [piece.cost for piece in consumed]
    origin_remaining = [piece.origins for piece in consumed]
    capital_remaining = operation.capitalized_costs
    for output_index, item in enumerate(operation.outputs):
      leg = event.legs[item.leg]
      allocation = allocations[output_index]
      final_output = output_index == len(operation.outputs) - 1
      capital = (
        capital_remaining if final_output else operation.capitalized_costs * allocation
      )
      capital_remaining = difference(capital_remaining, capital)
      capital_piece_remaining = capital
      quantity_remaining = leg.quantity
      # The largest basis share receives quantity residue, so a tiny final
      # source slice can never lose its nonzero basis through a zero quantity.
      largest = max(range(len(consumed)), key=lambda index: consumed[index].cost)
      indices = [index for index in range(len(consumed)) if index != largest] + [
        largest
      ]
      for part_index, index in enumerate(indices):
        piece = consumed[index]
        # Basis-weighted receipt units retain each acquisition's date and basis.
        # With all-zero basis every consumed slice gets an equal unit share.
        fraction = piece.cost / basis_in if basis_in else Decimal(1) / len(consumed)
        final_piece = part_index == len(consumed) - 1
        quantity = quantity_remaining if final_piece else leg.quantity * fraction
        addition = capital_piece_remaining if final_piece else capital * fraction
        inherited = (
          inherited_remaining[index] if final_output else piece.cost * allocation
        )
        origins = (
          origin_remaining[index]
          if final_output
          else split_origins(piece.origins, allocation, inherited)
        )
        origin_remaining[index] = tuple(
          replace(origin, cost=difference(origin.cost, used.cost))
          for origin, used in zip(origin_remaining[index], origins)
        )
        inherited_remaining[index] = difference(inherited_remaining[index], inherited)
        quantity_remaining = difference(quantity_remaining, quantity)
        capital_piece_remaining = difference(capital_piece_remaining, addition)
        cost = exact_sum((inherited, addition))
        if addition:
          origins += (Origin(event.id, event.time, addition),)
        acquired = (
          piece.acquired if operation.acquisition_date == 'carry' else event.time
        )
        if quantity == 0:
          continue
        lot = book.open(
          self.key(leg),
          quantity=quantity,
          cost=cost,
          time=acquired,
          event=event.id,
          origins=origins,
          exact_basis=True,
        )
        created.append(
          RolloverSlice(item.leg, lot.id, quantity, cost, acquired, origins)
        )
    if operation.write_off:
      for item in operation.inputs:
        leg = event.legs[item.leg]
        pieces = [piece for piece in consumed if piece.leg == item.leg]
        cost = sum((piece.cost for piece in pieces), Decimal(0))
        index = len(self.realized)
        self.journal.add(
          event,
          'holding',
          -cost,
          compartment=self.key(leg)[0],
          asset=leg.asset,
          ref=('realized_cost', index),
        )
        self.journal.add(
          event,
          'realized',
          cost,
          compartment=leg.compartment,
          asset=leg.asset,
          ref=('realized_pnl', index),
        )
        self.realized.append(
          Realized(
            event.id,
            event.time,
            leg.asset,
            leg.compartment,
            leg.quantity,
            Decimal(0),
            cost,
            -cost,
            tuple(piece.lot for piece in pieces),
          )
        )
    # Zero-basis consumed slices have no basis weight but their provenance must
    # survive mixed-basis transformations as well as all-zero transformations.
    zero_origins = tuple(
      origin for piece in consumed if piece.cost == 0 for origin in piece.origins
    )
    if basis_in and zero_origins and created:
      first = created[0]
      created[0] = replace(first, origins=first.origins + zero_origins)
      for lots in book.lots.values():
        for lot in lots:
          if lot.id == first.lot:
            lot.origins += zero_origins
    self.book = book
    for pieces, sign in (([] if operation.write_off else consumed, -1), (created, 1)):
      for piece in pieces:
        leg = event.legs[piece.leg]
        self.journal.add(
          event,
          'holding',
          sign * piece.cost,
          compartment=self.key(leg)[0],
          asset=leg.asset,
        )
    for leg, value in fee_values:
      self.book_leg(event, leg, value)
    unpaid = exact_sum(
      (operation.capitalized_costs, *(value for _, value in fee_values))
    )
    self.journal.add(event, 'external', -unpaid)
    for leg in event.legs:
      if leg.tag == 'rollover':
        scope = (leg.compartment, leg.asset)
        self.scope_positions[scope] = (
          self.scope_positions.get(scope, Decimal(0)) + leg.quantity
        )
    self.rollovers.append(
      RolloverRecord(
        event.id,
        tuple(consumed),
        tuple(created),
        allocations,
        basis_in,
        operation.capitalized_costs,
        exact_sum(piece.cost for piece in created),
        basis_in if operation.write_off else Decimal(0),
        operation.cost_reference,
      )
    )
