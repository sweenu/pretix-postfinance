"""
Pytest fixtures and configuration for pretix-postfinance tests.
"""

import inspect
import os

# Set testing environment
os.environ["PRETIX_POSTFINANCE_TESTING"] = "1"

import pytest
from django.utils import translation
from django_scopes import scopes_disabled


@pytest.hookimpl(hookwrapper=True)
def pytest_fixture_setup(fixturedef, request):
    """
    This hack automatically disables django-scopes for all fixtures which are not yield fixtures.
    This saves us a *lot* of decorators.
    """
    if inspect.isgeneratorfunction(fixturedef.func):
        yield
    else:
        with scopes_disabled():
            yield


@pytest.fixture(autouse=True)
def reset_locale():
    """Reset locale to English for each test."""
    translation.activate("en")


@pytest.fixture(autouse=True)
def no_messages(monkeypatch):
    """Patch out messages for performance improvements."""
    monkeypatch.setattr("django.contrib.messages.api.add_message", lambda *args, **kwargs: None)


# Mock fixtures for API tests
from decimal import Decimal
from unittest.mock import MagicMock


@pytest.fixture
def mock_postfinance_config():
    """Mock PostFinance configuration settings."""
    return {
        "space_id": "12345",
        "user_id": "67890",
        "api_secret": "test-secret-key",
        "environment": "sandbox",
    }


@pytest.fixture
def mock_transaction():
    """Mock PostFinance Transaction object."""
    transaction = MagicMock()
    transaction.id = 123456
    transaction.state = MagicMock()
    transaction.state.value = "COMPLETED"
    transaction.created_on = "2026-01-13T10:00:00Z"
    transaction.payment_connector_configuration = MagicMock()
    transaction.payment_connector_configuration.name = "TWINT"
    transaction.amount = 100.00
    return transaction


@pytest.fixture
def mock_refund():
    """Mock PostFinance Refund object."""
    refund = MagicMock()
    refund.id = 789012
    refund.state = MagicMock()
    refund.state.value = "SUCCESSFUL"
    refund.amount = 50.00
    refund.created_on = "2026-01-13T11:00:00Z"
    return refund


@pytest.fixture
def mock_space():
    """Mock PostFinance Space object."""
    space = MagicMock()
    space.id = 12345
    space.name = "Test Space"
    return space


@pytest.fixture
def mock_order_payment():
    """Mock pretix OrderPayment object."""
    payment = MagicMock()
    payment.pk = 1
    payment.amount = Decimal("100.00")
    payment.state = "created"
    payment.info_data = {}
    payment.order = MagicMock()
    payment.order.code = "ABC12"
    payment.order.event = MagicMock()
    payment.order.event.currency = "CHF"
    payment.order.event.slug = "test-event"
    payment.payment_provider = MagicMock()
    return payment


@pytest.fixture
def mock_request():
    """Mock Django HttpRequest object."""
    request = MagicMock()
    request.session = {}
    request.META = {"CSRF_COOKIE": "test-csrf-token"}
    request.POST = {}
    request.headers = {}
    request.body = b"{}"
    request.content_type = "application/json"
    return request


@pytest.fixture
def mock_event():
    """Mock pretix Event object."""
    event = MagicMock()
    event.slug = "test-event"
    event.currency = "CHF"
    event.organizer = MagicMock()
    event.organizer.slug = "test-org"
    event.settings = MagicMock()
    return event
