"""
Type definitions for pretix-postfinance plugin.

This module provides type definitions and protocols for pretix-specific
types that are not available in the standard Django type stubs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

from django.http import HttpRequest

if TYPE_CHECKING:
    from pretix.base.models import Event, Organizer


class PretixHttpRequest(HttpRequest):
    """
    Extended HttpRequest type with pretix-specific attributes.

    Pretix adds additional attributes to the request object via middleware,
    including the current event and organizer.
    """

    event: Event
    organizer: Organizer


class OrderPaymentProtocol(Protocol):
    """Protocol for pretix OrderPayment model."""

    pk: int
    amount: Any
    state: str
    info: str
    info_data: dict[str, Any]
    order: Any
    payment_provider: Any

    def save(self, update_fields: list[str] | None = None) -> None: ...


class EventProtocol(Protocol):
    """Protocol for pretix Event model."""

    slug: str
    currency: str
    organizer: Any
    settings: Any


class OrderProtocol(Protocol):
    """Protocol for pretix Order model."""

    code: str
    event: EventProtocol
