"""
Tests for pretix_postfinance.api module.
"""

import os
from unittest.mock import MagicMock, patch

import pytest

import pretix_postfinance.api as api_module
from pretix_postfinance.api import (
    PostFinanceClient,
    PostFinanceError,
    _get_timeout,
)


class TestPostFinanceError:
    """Tests for PostFinanceError exception class."""

    def test_error_with_message_only(self):
        """Create error with message only."""
        error = PostFinanceError("Test error")
        assert str(error) == "Test error"
        assert error.message == "Test error"
        assert error.status_code is None
        assert error.error_code is None

    def test_error_with_status_code(self):
        """Create error with status code."""
        error = PostFinanceError("Auth failed", status_code=401)
        assert error.status_code == 401
        assert error.message == "Auth failed"

    def test_error_with_all_attributes(self):
        """Create error with all attributes."""
        error = PostFinanceError("Not found", status_code=404, error_code="RESOURCE_NOT_FOUND")
        assert error.status_code == 404
        assert error.error_code == "RESOURCE_NOT_FOUND"
        assert error.message == "Not found"


@pytest.fixture
def mock_services():
    """Mock all PostFinance SDK services to allow client instantiation."""
    mocks = {
        "Configuration": MagicMock(),
        "SpacesService": MagicMock(),
        "TransactionsService": MagicMock(),
        "RefundsService": MagicMock(),
        "WebhookEncryptionKeysService": MagicMock(),
        "PaymentMethodConfigurationsService": MagicMock(),
        "WebhookURLsService": MagicMock(),
        "WebhookListenersService": MagicMock(),
    }
    with (
        patch.object(api_module, "Configuration", mocks["Configuration"]),
        patch.object(api_module, "SpacesService", mocks["SpacesService"]),
        patch.object(api_module, "TransactionsService", mocks["TransactionsService"]),
        patch.object(api_module, "RefundsService", mocks["RefundsService"]),
        patch.object(
            api_module, "WebhookEncryptionKeysService", mocks["WebhookEncryptionKeysService"]
        ),
        patch.object(
            api_module,
            "PaymentMethodConfigurationsService",
            mocks["PaymentMethodConfigurationsService"],
        ),
        patch.object(api_module, "WebhookURLsService", mocks["WebhookURLsService"]),
        patch.object(api_module, "WebhookListenersService", mocks["WebhookListenersService"]),
    ):
        yield mocks


class TestPostFinanceClient:
    """Tests for PostFinanceClient class."""

    def test_client_initialization(self, mock_services):  # noqa: ARG002
        """Client should initialize with correct attributes."""
        client = PostFinanceClient(
            space_id=12345,
            user_id=67890,
            api_secret="test-secret",
        )
        assert client.space_id == 12345
        assert client.user_id == 67890
        assert client.api_secret == "test-secret"

    def test_default_timeout(self):
        """Client should have 15 second default timeout (from env or default)."""
        # Default is 15 when env var is not set
        with patch.dict(os.environ, {}, clear=True):
            if "PRETIX_POSTFINANCE_API_TIMEOUT" in os.environ:
                del os.environ["PRETIX_POSTFINANCE_API_TIMEOUT"]
            assert _get_timeout() == 15

    def test_get_space_success(self, mock_services, mock_space):
        """get_space should return space details."""
        mock_spaces_instance = MagicMock()
        mock_spaces_instance.get_spaces_id.return_value = mock_space
        mock_services["SpacesService"].return_value = mock_spaces_instance

        client = PostFinanceClient(
            space_id=12345,
            user_id=67890,
            api_secret="test-secret",
        )

        result = client.get_space()

        assert result == mock_space
        mock_spaces_instance.get_spaces_id.assert_called_once_with(id=12345)

    def test_get_space_api_exception(self, mock_services):
        """get_space should raise PostFinanceError on API exception."""
        from postfinancecheckout.exceptions import ApiException

        mock_spaces_instance = MagicMock()
        mock_api_error = ApiException(status=401, reason="Unauthorized")
        mock_spaces_instance.get_spaces_id.side_effect = mock_api_error
        mock_services["SpacesService"].return_value = mock_spaces_instance

        client = PostFinanceClient(
            space_id=12345,
            user_id=67890,
            api_secret="test-secret",
        )

        with pytest.raises(PostFinanceError) as exc_info:
            client.get_space()

        assert exc_info.value.status_code == 401

    def test_get_transaction_success(self, mock_services, mock_transaction):
        """get_transaction should return transaction details."""
        mock_transactions_instance = MagicMock()
        mock_transactions_instance.get_payment_transactions_id.return_value = mock_transaction
        mock_services["TransactionsService"].return_value = mock_transactions_instance

        client = PostFinanceClient(
            space_id=12345,
            user_id=67890,
            api_secret="test-secret",
        )

        result = client.get_transaction(123456)

        assert result == mock_transaction
        mock_transactions_instance.get_payment_transactions_id.assert_called_once_with(
            id=123456, space=12345
        )

    def test_get_refund_success(self, mock_services, mock_refund):
        """get_refund should return refund details."""
        mock_refunds_instance = MagicMock()
        mock_refunds_instance.get_payment_refunds_id.return_value = mock_refund
        mock_services["RefundsService"].return_value = mock_refunds_instance

        client = PostFinanceClient(
            space_id=12345,
            user_id=67890,
            api_secret="test-secret",
        )

        result = client.get_refund(789012)

        assert result == mock_refund
        mock_refunds_instance.get_payment_refunds_id.assert_called_once_with(id=789012, space=12345)


class TestGetTimeout:
    """Tests for _get_timeout function."""

    def test_default_when_not_set(self):
        """Should return 15 when env var is not set."""
        with patch.dict(os.environ, {}, clear=True):
            assert _get_timeout() == 15

    def test_valid_value(self):
        """Should return the configured value when valid."""
        with patch.dict(os.environ, {"PRETIX_POSTFINANCE_API_TIMEOUT": "20"}):
            assert _get_timeout() == 20

    def test_invalid_non_integer(self):
        """Should return default when value is not an integer."""
        with patch.dict(os.environ, {"PRETIX_POSTFINANCE_API_TIMEOUT": "abc"}):
            assert _get_timeout() == 15

    def test_invalid_zero(self):
        """Should return default when value is zero."""
        with patch.dict(os.environ, {"PRETIX_POSTFINANCE_API_TIMEOUT": "0"}):
            assert _get_timeout() == 15

    def test_invalid_negative(self):
        """Should return default when value is negative."""
        with patch.dict(os.environ, {"PRETIX_POSTFINANCE_API_TIMEOUT": "-5"}):
            assert _get_timeout() == 15

    def test_capped_at_300(self):
        """Should cap at 300 seconds when value exceeds maximum."""
        with patch.dict(os.environ, {"PRETIX_POSTFINANCE_API_TIMEOUT": "500"}):
            assert _get_timeout() == 300

    def test_boundary_300(self):
        """Should accept 300 as valid maximum."""
        with patch.dict(os.environ, {"PRETIX_POSTFINANCE_API_TIMEOUT": "300"}):
            assert _get_timeout() == 300

    def test_boundary_1(self):
        """Should accept 1 as valid minimum."""
        with patch.dict(os.environ, {"PRETIX_POSTFINANCE_API_TIMEOUT": "1"}):
            assert _get_timeout() == 1
