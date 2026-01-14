"""
PostFinance Checkout API client.

Provides a wrapper around the official PostFinance Checkout Python SDK
for use with the pretix payment plugin.
"""

from __future__ import annotations

import logging
from typing import Literal

from postfinancecheckout import Configuration
from postfinancecheckout.exceptions import ApiException
from postfinancecheckout.models import (
    LineItemCreate,
    Refund,
    RefundCreate,
    RefundType,
    Space,
    Transaction,
    TransactionCompletion,
    TransactionCompletionBehavior,
    TransactionCreate,
    TransactionVoid,
)
from postfinancecheckout.postfinancecheckout_sdk_exception import (
    PostFinanceCheckoutSdkException,
)
from postfinancecheckout.service import (
    RefundsService,
    SpacesService,
    TransactionsService,
    WebhookEncryptionKeysService,
)

logger = logging.getLogger(__name__)


class PostFinanceError(Exception):
    """Base exception for PostFinance API errors."""

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        error_code: str | None = None,
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
        self._refunds_service = RefundsService(self._configuration)
        self._webhook_encryption_service = WebhookEncryptionKeysService(self._configuration)

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
        line_items: list[LineItemCreate],
        success_url: str,
        failed_url: str,
        merchant_reference: str | None = None,
        language: str | None = None,
        completion_behavior: TransactionCompletionBehavior | None = None,
        allowed_payment_method_configurations: list[int] | None = None,
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
            completion_behavior: Optional transaction completion behavior.
                COMPLETE_IMMEDIATELY for immediate capture,
                COMPLETE_DEFERRED for manual capture.
            allowed_payment_method_configurations: Optional list of payment method
                configuration IDs to restrict which payment methods are available.
                If not provided, all configured payment methods are available.

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
            completionBehavior=completion_behavior,
            allowedPaymentMethodConfigurations=allowed_payment_method_configurations,
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

    def complete_transaction(self, transaction_id: int) -> TransactionCompletion:
        """
        Complete (capture) an authorized transaction.

        This completes a transaction that is in the AUTHORIZED state,
        capturing the authorized funds.

        Args:
            transaction_id: The ID of the transaction to complete.

        Returns:
            The TransactionCompletion object with completion details.

        Raises:
            PostFinanceError: If the request fails (e.g., transaction not
                in AUTHORIZED state, already completed, etc.).
        """
        try:
            return self._transactions_service.post_payment_transactions_id_complete_online(
                id=transaction_id,
                space=self.space_id,
            )
        except ApiException as e:
            logger.error("PostFinance API error completing transaction: %s", e)
            raise PostFinanceError(
                message=str(e),
                status_code=e.status,
                error_code=str(e.status),
            ) from e
        except PostFinanceCheckoutSdkException as e:
            logger.error("PostFinance SDK error completing transaction: %s", e)
            raise PostFinanceError(message=str(e)) from e

    def void_transaction(self, transaction_id: int) -> TransactionVoid:
        """
        Void an authorized transaction.

        This voids a transaction that is in the AUTHORIZED state,
        releasing the authorized funds back to the customer.

        Args:
            transaction_id: The ID of the transaction to void.

        Returns:
            The TransactionVoid object with void details.

        Raises:
            PostFinanceError: If the request fails (e.g., transaction not
                in AUTHORIZED state, already voided, etc.).
        """
        try:
            return self._transactions_service.post_payment_transactions_id_void_online(
                id=transaction_id,
                space=self.space_id,
            )
        except ApiException as e:
            logger.error("PostFinance API error voiding transaction: %s", e)
            raise PostFinanceError(
                message=str(e),
                status_code=e.status,
                error_code=str(e.status),
            ) from e
        except PostFinanceCheckoutSdkException as e:
            logger.error("PostFinance SDK error voiding transaction: %s", e)
            raise PostFinanceError(message=str(e)) from e

    def refund_transaction(
        self,
        transaction_id: int,
        external_id: str,
        merchant_reference: str | None = None,
        amount: float | None = None,
    ) -> Refund:
        """
        Create a refund for a completed transaction.

        This creates a refund for a transaction that is in the COMPLETED or
        FULFILL state. If no amount is specified, a full refund is created.

        Args:
            transaction_id: The ID of the transaction to refund.
            external_id: A unique client-generated ID for this refund request.
                Subsequent requests with the same ID will not execute again.
            merchant_reference: Optional merchant reference for the refund.
            amount: Optional refund amount. If not provided, a full refund
                is created. For partial refunds, specify the amount to refund.

        Returns:
            The Refund object with refund details.

        Raises:
            PostFinanceError: If the request fails (e.g., transaction not
                in a refundable state, already fully refunded, etc.).
        """
        refund_create = RefundCreate(
            transaction=transaction_id,
            externalId=external_id,
            type=RefundType.MERCHANT_INITIATED_ONLINE,
            merchantReference=merchant_reference,
            amount=amount,
        )

        try:
            return self._refunds_service.post_payment_refunds(
                space=self.space_id,
                refund_create=refund_create,
            )
        except ApiException as e:
            logger.error("PostFinance API error creating refund: %s", e)
            raise PostFinanceError(
                message=str(e),
                status_code=e.status,
                error_code=str(e.status),
            ) from e
        except PostFinanceCheckoutSdkException as e:
            logger.error("PostFinance SDK error creating refund: %s", e)
            raise PostFinanceError(message=str(e)) from e

    def get_refund(self, refund_id: int) -> Refund:
        """
        Retrieve a refund by its ID.

        Args:
            refund_id: The ID of the refund.

        Returns:
            The Refund object with refund details.

        Raises:
            PostFinanceError: If the request fails.
        """
        try:
            return self._refunds_service.get_payment_refunds_id(
                id=refund_id,
                space=self.space_id,
            )
        except ApiException as e:
            logger.error("PostFinance API error getting refund: %s", e)
            raise PostFinanceError(
                message=str(e),
                status_code=e.status,
                error_code=str(e.status),
            ) from e
        except PostFinanceCheckoutSdkException as e:
            logger.error("PostFinance SDK error getting refund: %s", e)
            raise PostFinanceError(message=str(e)) from e

    def is_webhook_signature_valid(
        self,
        signature_header: str,
        content: str,
    ) -> bool:
        """
        Validate webhook signature using the SDK's encryption service.

        Uses the X-Signature header and raw request body to verify that
        the webhook payload was actually sent by PostFinance and hasn't
        been tampered with.

        Args:
            signature_header: The value of the X-Signature HTTP header.
            content: The raw request body as a string.

        Returns:
            True if the signature is valid, False otherwise.

        Raises:
            PostFinanceError: If there's an error validating the signature
                (e.g., invalid header format, unknown key ID).
        """
        try:
            result = self._webhook_encryption_service.is_content_valid(
                signature_header=signature_header,
                content_to_verify=content,
            )
            return bool(result)
        except ApiException as e:
            logger.error("PostFinance API error validating webhook signature: %s", e)
            raise PostFinanceError(
                message=str(e),
                status_code=e.status,
                error_code=str(e.status),
            ) from e
        except PostFinanceCheckoutSdkException as e:
            logger.error("PostFinance SDK error validating webhook signature: %s", e)
            raise PostFinanceError(message=str(e)) from e
