"""
PostFinance Checkout API client.

Provides a wrapper around the official PostFinance Checkout Python SDK
for use with the pretix payment plugin.
"""

import logging
from decimal import Decimal
from typing import Any, Dict, List, Literal, Optional

from postfinancecheckout import Configuration
from postfinancecheckout.exceptions import ApiException
from postfinancecheckout.models import (
    LineItemCreate,
    LineItemType,
    Space,
    Transaction,
    TransactionCreate,
)
from postfinancecheckout.postfinancecheckout_sdk_exception import (
    PostFinanceCheckoutSdkException,
)
from postfinancecheckout.service import SpacesService, TransactionsService

logger = logging.getLogger(__name__)


class PostFinanceError(Exception):
    """Base exception for PostFinance API errors."""

    def __init__(
        self,
        message: str,
        status_code: Optional[int] = None,
        error_code: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.error_code = error_code


class PostFinanceClient:
    """
    Client for PostFinance Checkout API using the official SDK.

    Provides a simplified interface to the PostFinance Checkout SDK
    for common payment operations.

    Attributes:
        space_id: The PostFinance space ID.
        user_id: The PostFinance user ID for authentication.
        api_secret: The API secret (authentication key).
        environment: Either 'sandbox' or 'production' (for reference only,
            SDK uses same endpoint for both).
    """

    DEFAULT_TIMEOUT = 30  # seconds

    def __init__(
        self,
        space_id: int,
        user_id: int,
        api_secret: str,
        environment: Literal["sandbox", "production"] = "production",
    ) -> None:
        """
        Initialize the PostFinance API client.

        Args:
            space_id: The PostFinance space ID.
            user_id: The PostFinance user ID for authentication.
            api_secret: The API secret (authentication key).
            environment: Either 'sandbox' or 'production'. Defaults to 'production'.
                Note: Both use the same API endpoint; environment is determined
                by space configuration.
        """
        self.space_id = space_id
        self.user_id = user_id
        self.api_secret = api_secret
        self.environment = environment

        self._configuration = Configuration(
            user_id=user_id,
            authentication_key=api_secret,
            request_timeout=self.DEFAULT_TIMEOUT,
        )
        self._spaces_service = SpacesService(self._configuration)
        self._transactions_service = TransactionsService(self._configuration)

    def get_space(self) -> Space:
        """
        Get details about the configured space.

        This is useful for testing the connection and verifying credentials.

        Returns:
            The Space object with id, name, and other details.

        Raises:
            PostFinanceError: If the request fails or credentials are invalid.
        """
        try:
            return self._spaces_service.get_spaces_id(id=self.space_id)
        except ApiException as e:
            logger.error("PostFinance API error getting space: %s", e)
            raise PostFinanceError(
                message=str(e),
                status_code=e.status,
                error_code=str(e.status),
            ) from e
        except PostFinanceCheckoutSdkException as e:
            logger.error("PostFinance SDK error getting space: %s", e)
            raise PostFinanceError(message=str(e)) from e

    def create_transaction(
        self,
        currency: str,
        line_items: List[LineItemCreate],
        success_url: str,
        failed_url: str,
        merchant_reference: Optional[str] = None,
        language: Optional[str] = None,
    ) -> Transaction:
        """
        Create a new payment transaction.

        Args:
            currency: The three-letter currency code (e.g., 'CHF', 'EUR').
            line_items: List of LineItemCreate objects for the transaction.
            success_url: URL to redirect to on successful payment.
            failed_url: URL to redirect to on failed/cancelled payment.
            merchant_reference: Optional merchant reference for this transaction.
            language: Optional language code for the payment page (e.g., 'en-US').

        Returns:
            The created Transaction object.

        Raises:
            PostFinanceError: If the request fails.
        """
        transaction_create = TransactionCreate(
            currency=currency,
            lineItems=line_items,
            successUrl=success_url,
            failedUrl=failed_url,
            merchantReference=merchant_reference,
            language=language,
        )

        try:
            return self._transactions_service.post_payment_transactions(
                space=self.space_id,
                transaction_create=transaction_create,
            )
        except ApiException as e:
            logger.error("PostFinance API error creating transaction: %s", e)
            raise PostFinanceError(
                message=str(e),
                status_code=e.status,
                error_code=str(e.status),
            ) from e
        except PostFinanceCheckoutSdkException as e:
            logger.error("PostFinance SDK error creating transaction: %s", e)
            raise PostFinanceError(message=str(e)) from e

    def get_payment_page_url(self, transaction_id: int) -> str:
        """
        Get the URL for the payment page for a transaction.

        Args:
            transaction_id: The ID of the transaction.

        Returns:
            The URL to redirect the customer to for payment.

        Raises:
            PostFinanceError: If the request fails.
        """
        try:
            return self._transactions_service.get_payment_transactions_id_payment_page_url(
                id=transaction_id,
                space=self.space_id,
            )
        except ApiException as e:
            logger.error("PostFinance API error getting payment page URL: %s", e)
            raise PostFinanceError(
                message=str(e),
                status_code=e.status,
                error_code=str(e.status),
            ) from e
        except PostFinanceCheckoutSdkException as e:
            logger.error("PostFinance SDK error getting payment page URL: %s", e)
            raise PostFinanceError(message=str(e)) from e

    def get_transaction(self, transaction_id: int) -> Transaction:
        """
        Retrieve a transaction by its ID.

        Args:
            transaction_id: The ID of the transaction.

        Returns:
            The Transaction object.

        Raises:
            PostFinanceError: If the request fails.
        """
        try:
            return self._transactions_service.get_payment_transactions_id(
                id=transaction_id,
                space=self.space_id,
            )
        except ApiException as e:
            logger.error("PostFinance API error getting transaction: %s", e)
            raise PostFinanceError(
                message=str(e),
                status_code=e.status,
                error_code=str(e.status),
            ) from e
        except PostFinanceCheckoutSdkException as e:
            logger.error("PostFinance SDK error getting transaction: %s", e)
            raise PostFinanceError(message=str(e)) from e


def build_line_item(
    name: str,
    quantity: float,
    amount_including_tax: float,
    unique_id: str,
    item_type: LineItemType = LineItemType.PRODUCT,
) -> LineItemCreate:
    """
    Build a line item for a transaction.

    Args:
        name: The name of the product.
        quantity: The number of items.
        amount_including_tax: The total amount including tax.
        unique_id: A unique identifier for this line item.
        item_type: The type of line item (default: PRODUCT).

    Returns:
        A LineItemCreate object for use with create_transaction.
    """
    return LineItemCreate(
        name=name,
        quantity=quantity,
        amountIncludingTax=amount_including_tax,
        uniqueId=unique_id,
        type=item_type,
    )
