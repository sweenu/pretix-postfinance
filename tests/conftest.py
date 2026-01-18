"""
Pytest fixtures and configuration for pretix-postfinance tests.
"""

import os
import sys

# Set testing environment before any imports
os.environ["PRETIX_POSTFINANCE_TESTING"] = "1"
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tests.settings")

# Add tests directory to path for settings import
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import django

django.setup()

from decimal import Decimal
from unittest.mock import MagicMock

import pytest


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
