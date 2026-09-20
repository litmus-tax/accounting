"""Shared helpers: leg, event and policy builders plus the cost-method and lot-scope matrix."""

from datetime import datetime, timezone
from decimal import Decimal
from typing_extensions import Any
import pytest
from litmus.accounting.model import (
  Leg,
  Event,
  Link,
  Policy,
  LegTag,
  CostMethod,
  LotScope,
)

METHODS: tuple[CostMethod, ...] = ('fifo', 'lifo', 'hifo', 'average')
SCOPES: tuple[LotScope, ...] = ('global', 'compartment')


def t(day: int, hour: int = 12) -> datetime:
  """A UTC timestamp in January 2026."""
  return datetime(2026, 1, day, hour, tzinfo=timezone.utc)


def leg(
  asset: str,
  qty: str,
  comp: str = 'A',
  tag: LegTag = 'trade',
  *,
  fee: bool = False,
  label: str | None = None,
) -> Leg:
  """Build a leg."""
  return Leg(
    asset=asset, quantity=Decimal(qty), compartment=comp, tag=tag, fee=fee, label=label
  )


def event(id: str, day: int, *legs: Leg, hour: int = 12) -> Event:
  """Build an event."""
  return Event(id=id, time=t(day, hour), legs=legs)


def link(src: str, dst: str) -> Link:
  """Build a link."""
  return Link(src=src, dst=dst)


def policy(
  method: CostMethod = 'fifo', scope: LotScope = 'global', fc: str = 'EUR', **extra: Any
) -> Policy:
  """Build a policy; `cash` and `position_assets` default to what the tests use."""
  base: dict[str, Any] = {
    'cost_method': method,
    'lot_scope': scope,
    'functional_currency': fc,
    'cash': ('USDC', 'USD', 'EUR'),
    'position_assets': ('BTC-PERP',),
  }
  return Policy(**{**base, **extra})


@pytest.fixture(params=METHODS)
def method(request: pytest.FixtureRequest) -> CostMethod:
  """Every cost method."""
  return request.param


@pytest.fixture(params=SCOPES)
def scope(request: pytest.FixtureRequest) -> LotScope:
  """Both lot scopes."""
  return request.param
