"""
Tests for pretix_postfinance.api module.
"""

from unittest.mock import MagicMock, patch

import pytest

from pretix_postfinance.api import (
    PostFinanceClient,
    PostFinanceError,
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


class TestPostFinanceClient:
    """Tests for PostFinanceClient class."""

    def test_client_initialization(self):
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
        """Client should have 30 second default timeout."""
        assert PostFinanceClient.DEFAULT_TIMEOUT == 30

    @patch("pretix_postfinance.api.SpacesService")
    @patch("pretix_postfinance.api.TransactionsService")
    @patch("pretix_postfinance.api.RefundsService")
    @patch("pretix_postfinance.api.WebhookEncryptionKeysService")
    @patch("pretix_postfinance.api.Configuration")
    def test_get_space_success(
        self,
        _mock_config,
        _mock_webhook_service,
        _mock_refunds_service,
        _mock_transactions_service,
        mock_spaces_service,
        mock_space,
    ):
        """get_space should return space details."""
        mock_spaces_instance = MagicMock()
        mock_spaces_instance.get_spaces_id.return_value = mock_space
        mock_spaces_service.return_value = mock_spaces_instance

        client = PostFinanceClient(
            space_id=12345,
            user_id=67890,
            api_secret="test-secret",
        )

        result = client.get_space()

        assert result == mock_space
        mock_spaces_instance.get_spaces_id.assert_called_once_with(id=12345)

    @patch("pretix_postfinance.api.SpacesService")
    @patch("pretix_postfinance.api.TransactionsService")
    @patch("pretix_postfinance.api.RefundsService")
    @patch("pretix_postfinance.api.WebhookEncryptionKeysService")
    @patch("pretix_postfinance.api.Configuration")
    def test_get_space_api_exception(
        self,
        _mock_config,
        _mock_webhook_service,
        _mock_refunds_service,
        _mock_transactions_service,
        mock_spaces_service,
    ):
        """get_space should raise PostFinanceError on API exception."""
        from postfinancecheckout.exceptions import ApiException

        mock_spaces_instance = MagicMock()
        mock_api_error = ApiException(status=401, reason="Unauthorized")
        mock_spaces_instance.get_spaces_id.side_effect = mock_api_error
        mock_spaces_service.return_value = mock_spaces_instance

        client = PostFinanceClient(
            space_id=12345,
            user_id=67890,
            api_secret="test-secret",
        )

        with pytest.raises(PostFinanceError) as exc_info:
            client.get_space()

        assert exc_info.value.status_code == 401

    @patch("pretix_postfinance.api.SpacesService")
    @patch("pretix_postfinance.api.TransactionsService")
    @patch("pretix_postfinance.api.RefundsService")
    @patch("pretix_postfinance.api.WebhookEncryptionKeysService")
    @patch("pretix_postfinance.api.Configuration")
    def test_get_transaction_success(
        self,
        _mock_config,
        _mock_webhook_service,
        _mock_refunds_service,
        mock_transactions_service,
        _mock_spaces_service,
        mock_transaction,
    ):
        """get_transaction should return transaction details."""
        mock_transactions_instance = MagicMock()
        mock_transactions_instance.get_payment_transactions_id.return_value = mock_transaction
        mock_transactions_service.return_value = mock_transactions_instance

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

    @patch("pretix_postfinance.api.SpacesService")
    @patch("pretix_postfinance.api.TransactionsService")
    @patch("pretix_postfinance.api.RefundsService")
    @patch("pretix_postfinance.api.WebhookEncryptionKeysService")
    @patch("pretix_postfinance.api.Configuration")
    def test_get_refund_success(
        self,
        _mock_config,
        _mock_webhook_service,
        mock_refunds_service,
        _mock_transactions_service,
        _mock_spaces_service,
        mock_refund,
    ):
        """get_refund should return refund details."""
        mock_refunds_instance = MagicMock()
        mock_refunds_instance.get_payment_refunds_id.return_value = mock_refund
        mock_refunds_service.return_value = mock_refunds_instance

        client = PostFinanceClient(
            space_id=12345,
            user_id=67890,
            api_secret="test-secret",
        )

        result = client.get_refund(789012)

        assert result == mock_refund
        mock_refunds_instance.get_payment_refunds_id.assert_called_once_with(id=789012, space=12345)
