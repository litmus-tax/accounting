"""
Exact finite-decimal sums for conservation after bounded decimal division, and
the engine's own decimal context.
"""

from decimal import (
  Context,
  Decimal,
  DivisionByZero,
  InvalidOperation,
  Overflow,
  ROUND_HALF_EVEN,
  localcontext,
)
from typing_extensions import Iterable, Callable, ParamSpec, TypeVar
import functools

CONTEXT = Context(
  prec=28,
  rounding=ROUND_HALF_EVEN,
  Emin=-999999,
  Emax=999999,
  capitals=1,
  clamp=0,
  flags=[],
  traps=[InvalidOperation, DivisionByZero, Overflow],
)
"""
The decimal context every engine computation runs in, whatever the caller's
(policy 05 rule 2.2). It equals Python's default context, so results computed
under the default context are unchanged.
"""

Params = ParamSpec('Params')
Returned = TypeVar('Returned')


def fixed_context(function: Callable[Params, Returned]) -> Callable[Params, Returned]:
  """Run `function` under `CONTEXT`, restoring the caller's context afterwards."""

  @functools.wraps(function)
  def wrapper(*args: Params.args, **kwargs: Params.kwargs) -> Returned:
    with localcontext(CONTEXT):
      return function(*args, **kwargs)

  return wrapper


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
