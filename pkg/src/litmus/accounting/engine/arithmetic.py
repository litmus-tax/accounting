"""Exact finite-decimal sums for conservation after bounded decimal division."""

from decimal import Decimal, localcontext
from typing_extensions import Iterable


def exact_sum(values: Iterable[Decimal]) -> Decimal:
  """Sum finite decimals without losing low-order digits to the ambient context."""
  parts = tuple(values)
  if not parts:
    return Decimal(0)
  exponents: list[int] = []
  for value in parts:
    exponent = value.as_tuple().exponent
    if not isinstance(exponent, int):
      raise ValueError('Exact sums require finite decimal values')
    exponents.append(exponent)
  precision = (
    max(value.adjusted() for value in parts) - min(exponents) + len(str(len(parts))) + 2
  )
  with localcontext() as context:
    context.prec = max(context.prec, precision)
    return sum(parts, Decimal(0))


def difference(left: Decimal, right: Decimal) -> Decimal:
  """Subtract without rounding a finite decimal remainder."""
  return exact_sum((left, right.copy_negate()))
