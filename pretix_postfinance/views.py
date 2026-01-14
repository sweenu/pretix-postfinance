"""
Views for PostFinance payment plugin.

Handles return URLs from PostFinance payment page, webhook callbacks,
and admin actions like capture.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from django.contrib import messages
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.utils.decorators import method_decorator
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from postfinancecheckout.models import TransactionState
from pretix.base.models import Event, Order, OrderPayment
from pretix.control.permissions import EventPermissionRequiredMixin
from pretix.multidomain.urlreverse import eventreverse

from ._types import PretixHttpRequest
from .api import PostFinanceClient, PostFinanceError
from .payment import FAILURE_STATES, SUCCESS_STATES

logger = logging.getLogger(__name__)


class PostFinanceReturnView(View):
    """
    Handle return from PostFinance payment page.

    This view is called when the customer returns from the PostFinance
    payment page after completing or cancelling payment.
    """

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        """
        Process the return from PostFinance payment page.

        Fetches the transaction status from PostFinance and updates the
        payment accordingly, then redirects to the appropriate page.
        """
        organizer = kwargs.get("organizer")
        event_slug = kwargs.get("event")
        order_code = kwargs.get("order")
        payment_pk = kwargs.get("payment")
        secret = kwargs.get("hash")

        event = get_object_or_404(
            Event,
            slug__iexact=event_slug,
            organizer__slug__iexact=organizer,
        )
        order = get_object_or_404(
            Order,
            code=order_code,
            event=event,
        )

        if not order.secret.startswith(secret):
            logger.warning(
                "PostFinance return with invalid secret for order %s",
                order_code,
            )
            messages.error(request, str(_("Invalid request.")))
            return redirect(
                eventreverse(event, "presale:event.index")
            )

        payment = get_object_or_404(
            OrderPayment,
            pk=payment_pk,
            order=order,
        )

        transaction_id = payment.info_data.get("transaction_id") if payment.info_data else None

        if not transaction_id:
            logger.warning(
                "PostFinance return without transaction ID for payment %s",
                payment.pk,
            )
            messages.error(
                request,
                str(_("Payment information not found. Please try again.")),
            )
            return redirect(
                eventreverse(
                    event, "presale:event.checkout", kwargs={"step": "payment"}
                )
            )

        try:
            client = self._get_client(payment)
            transaction = client.get_transaction(int(transaction_id))

            state = transaction.state
            logger.info(
                "PostFinance transaction %s state: %s for payment %s",
                transaction_id,
                state,
                payment.pk,
            )

            payment_method = None
            if transaction.payment_connector_configuration:
                payment_method = transaction.payment_connector_configuration.name

            payment.info_data = payment.info_data or {}
            payment.info_data.update({
                "transaction_id": transaction_id,
                "state": state.value if state else None,
                "payment_method": payment_method,
            })
            payment.save(update_fields=["info"])

            if state in SUCCESS_STATES:
                return self._handle_success(request, event, order, payment, state)
            elif state in FAILURE_STATES:
                return self._handle_failure(request, event, order, payment, state)
            else:
                return self._handle_pending(request, event, order, payment, state)

        except PostFinanceError as e:
            logger.exception(
                "PostFinance API error checking transaction %s: %s",
                transaction_id,
                e,
            )
            messages.error(
                request,
                str(_("Could not verify payment status. Please contact support.")),
            )
            return redirect(
                eventreverse(event, "presale:event.order", kwargs={
                    "order": order.code,
                    "secret": order.secret,
                })
            )

    def _get_client(self, payment: OrderPayment) -> PostFinanceClient:
        """Create PostFinance client from payment provider settings."""
        provider = payment.payment_provider
        return PostFinanceClient(
            space_id=int(provider.settings.get("space_id", 0)),
            user_id=int(provider.settings.get("user_id", 0)),
            api_secret=str(provider.settings.get("api_secret", "")),
            environment=provider.settings.get("environment", "sandbox"),
        )

    def _handle_success(
        self,
        request: HttpRequest,
        event: Event,
        order: Order,
        payment: OrderPayment,
        state: TransactionState,
    ) -> HttpResponse:
        """
        Handle successful payment states (AUTHORIZED, COMPLETED, FULFILL, etc).

        Marks the payment as confirmed if not already done.
        """
        if payment.state in (
            OrderPayment.PAYMENT_STATE_CONFIRMED,
            OrderPayment.PAYMENT_STATE_REFUNDED,
        ):
            logger.info(
                "Payment %s already confirmed, redirecting to order page",
                payment.pk,
            )
        elif payment.state in (
            OrderPayment.PAYMENT_STATE_CREATED,
            OrderPayment.PAYMENT_STATE_PENDING,
        ):
            try:
                payment.confirm()
                logger.info(
                    "Payment %s confirmed for order %s",
                    payment.pk,
                    order.code,
                )
            except Exception as e:
                logger.exception(
                    "Error confirming payment %s: %s",
                    payment.pk,
                    e,
                )
                messages.warning(
                    request,
                    str(
                        _(
                            "Your payment was received, but there was an issue "
                            "processing your order. Please contact support."
                        )
                    ),
                )

        return redirect(
            eventreverse(event, "presale:event.order", kwargs={
                "order": order.code,
                "secret": order.secret,
            }) + "?paid=yes"
        )

    def _handle_failure(
        self,
        request: HttpRequest,
        event: Event,
        order: Order,
        payment: OrderPayment,
        state: TransactionState,
    ) -> HttpResponse:
        """
        Handle failed payment states (FAILED, DECLINE, VOIDED).

        Marks the payment as failed and shows error message.
        """
        if payment.state not in (
            OrderPayment.PAYMENT_STATE_FAILED,
            OrderPayment.PAYMENT_STATE_CANCELED,
        ):
            payment.fail(info={"state": state.value if state else None})
            logger.info(
                "Payment %s marked as failed (state: %s) for order %s",
                payment.pk,
                state,
                order.code,
            )

        messages.error(
            request,
            str(
                _(
                    "Your payment could not be completed. "
                    "Please try again or choose a different payment method."
                )
            ),
        )

        return redirect(
            eventreverse(
                event, "presale:event.checkout", kwargs={"step": "payment"}
            )
        )

    def _handle_pending(
        self,
        request: HttpRequest,
        event: Event,
        order: Order,
        payment: OrderPayment,
        state: TransactionState | None,
    ) -> HttpResponse:
        """
        Handle pending/unknown payment states.

        Sets payment to pending state and informs user to wait.
        """
        if payment.state == OrderPayment.PAYMENT_STATE_CREATED:
            payment.state = OrderPayment.PAYMENT_STATE_PENDING
            payment.save(update_fields=["state"])
            logger.info(
                "Payment %s set to pending (PostFinance state: %s) for order %s",
                payment.pk,
                state,
                order.code,
            )

        messages.info(
            request,
            str(
                _(
                    "Your payment is being processed. "
                    "You will receive a confirmation once the payment is complete."
                )
            ),
        )

        return redirect(
            eventreverse(event, "presale:event.order", kwargs={
                "order": order.code,
                "secret": order.secret,
            })
        )


@method_decorator(csrf_exempt, name="dispatch")
class PostFinanceWebhookView(View):
    """
    Handle webhook notifications from PostFinance.

    PostFinance sends webhook notifications when transaction states change.
    This endpoint receives and processes those notifications.
    """

    SIGNATURE_HEADER = "X-Signature"

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        """
        Process incoming webhook notification from PostFinance.

        Args:
            request: The HTTP request containing the webhook payload.

        Returns:
            HttpResponse with status 200 on success, 400 on malformed requests,
            401 on invalid signature.
        """
        try:
            payload = self._parse_payload(request)
        except ValueError as e:
            logger.warning("PostFinance webhook: malformed request - %s", e)
            return JsonResponse(
                {"error": "Malformed request", "detail": str(e)},
                status=400,
            )

        # Get signature header
        signature_header = request.headers.get(self.SIGNATURE_HEADER)

        # Get space_id from payload
        space_id = payload.get("spaceId")
        if not space_id:
            logger.warning(
                "PostFinance webhook: missing spaceId in payload"
            )
            return JsonResponse(
                {"error": "Missing spaceId in payload"},
                status=400,
            )

        # If signature header is present, validate it
        if signature_header:
            is_valid = self._validate_signature(
                signature_header=signature_header,
                content=request.body.decode("utf-8"),
                space_id=space_id,
            )
            if not is_valid:
                if len(signature_header) > 50:
                    sig_preview = signature_header[:50] + "..."
                else:
                    sig_preview = signature_header
                logger.warning(
                    "PostFinance webhook: invalid signature for spaceId=%s, "
                    "entityId=%s. Signature header: %s",
                    space_id,
                    payload.get("entityId"),
                    sig_preview,
                )
                return JsonResponse(
                    {"error": "Invalid signature"},
                    status=401,
                )
        else:
            # Log warning but still process - signature may not be configured yet
            logger.warning(
                "PostFinance webhook: no signature header present for spaceId=%s, "
                "entityId=%s. Consider enabling webhook payload signing.",
                space_id,
                payload.get("entityId"),
            )

        entity_id = payload.get("entityId")
        listener_entity_id = payload.get("listenerEntityId")
        state = payload.get("state")

        logger.info(
            "PostFinance webhook received: entityId=%s, listenerEntityId=%s, "
            "spaceId=%s, state=%s",
            entity_id,
            listener_entity_id,
            space_id,
            state,
        )

        # Try to process as transaction webhook first
        result = self._process_transaction_state(
            entity_id=entity_id,
            space_id=space_id,
            state=state,
        )

        if result is None:
            # No payment found by transaction_id, try processing as refund webhook
            result = self._process_refund_state(
                entity_id=entity_id,
                space_id=space_id,
                state=state,
            )

        if result is None:
            # Could not find or process payment - return 200 to prevent retries
            # (webhook is valid but payment may not exist yet or listener type unknown)
            logger.debug(
                "PostFinance webhook: no matching payment found for entityId=%s",
                entity_id,
            )
            return HttpResponse(status=200)

        return HttpResponse(status=200)

    def _validate_signature(
        self,
        signature_header: str,
        content: str,
        space_id: int,
    ) -> bool:
        """
        Validate the webhook signature using the PostFinance SDK.

        Finds a payment provider configuration with the matching space_id
        and uses its credentials to validate the signature.

        Args:
            signature_header: The value of the X-Signature header.
            content: The raw request body as a string.
            space_id: The space ID from the webhook payload.

        Returns:
            True if the signature is valid, False otherwise.
        """
        # Find a payment provider with this space_id
        client = self._get_client_for_space(space_id)
        if not client:
            logger.warning(
                "PostFinance webhook: no payment provider found for spaceId=%s",
                space_id,
            )
            # If we can't find credentials for this space, reject the webhook
            return False

        try:
            return client.is_webhook_signature_valid(
                signature_header=signature_header,
                content=content,
            )
        except PostFinanceError as e:
            logger.error(
                "PostFinance webhook: signature validation error for spaceId=%s: %s",
                space_id,
                e,
            )
            return False

    def _get_client_for_space(self, space_id: int) -> PostFinanceClient | None:
        """
        Find and return a PostFinanceClient for the given space ID.

        Searches through configured payment providers to find one with
        matching space_id credentials.

        Args:
            space_id: The PostFinance space ID.

        Returns:
            A configured PostFinanceClient, or None if no matching
            provider is found.
        """
        from pretix.base.models import Event
        from pretix.base.settings import GlobalSettingsObject

        # First, check if there's a global PostFinance configuration
        # (pretix supports both global and per-event payment settings)
        try:
            global_settings = GlobalSettingsObject()
            configured_space = global_settings.settings.get("payment_postfinance_space_id")
            if configured_space and str(configured_space) == str(space_id):
                gs = global_settings.settings
                return PostFinanceClient(
                    space_id=int(configured_space),
                    user_id=int(gs.get("payment_postfinance_user_id", 0)),
                    api_secret=str(gs.get("payment_postfinance_api_secret", "")),
                    environment=gs.get("payment_postfinance_environment", "sandbox"),
                )
        except Exception as e:
            logger.debug("Could not check global settings: %s", e)

        # Search through events for a matching space_id
        # This is not ideal for performance, but webhooks are infrequent
        for event in Event.objects.filter(live=True):
            try:
                event_space_id = event.settings.get("payment_postfinance_space_id")
                if str(event_space_id) == str(space_id):
                    es = event.settings
                    return PostFinanceClient(
                        space_id=int(event_space_id),
                        user_id=int(es.get("payment_postfinance_user_id", 0)),
                        api_secret=str(es.get("payment_postfinance_api_secret", "")),
                        environment=es.get("payment_postfinance_environment", "sandbox"),
                    )
            except Exception as e:
                logger.debug(
                    "Could not check event %s settings: %s",
                    event.slug,
                    e,
                )
                continue

        return None

    def _process_transaction_state(
        self,
        entity_id: int | None,
        space_id: int,
        state: str | None,
    ) -> bool | None:
        """
        Process transaction state update from webhook.

        Finds the payment matching the PostFinance transaction ID, fetches
        the current transaction state from PostFinance, and updates the
        payment state accordingly. Operations are idempotent.

        Args:
            entity_id: The PostFinance transaction ID (entityId from webhook).
            space_id: The PostFinance space ID.
            state: The transaction state from the webhook (may be None).

        Returns:
            True if the payment was updated, False if already in final state,
            None if payment not found or could not be processed.
        """
        if not entity_id:
            logger.warning("PostFinance webhook: missing entityId")
            return None

        # Find payment by transaction ID
        payment = self._find_payment_by_transaction_id(entity_id)
        if not payment:
            logger.warning(
                "PostFinance webhook: no payment found for transaction %s",
                entity_id,
            )
            return None

        # Get client for this space
        client = self._get_client_for_space(space_id)
        if not client:
            logger.error(
                "PostFinance webhook: could not get client for spaceId=%s",
                space_id,
            )
            return None

        # Fetch full transaction details from PostFinance
        try:
            transaction = client.get_transaction(int(entity_id))
        except PostFinanceError as e:
            logger.error(
                "PostFinance webhook: failed to fetch transaction %s: %s",
                entity_id,
                e,
            )
            return None

        transaction_state = transaction.state
        logger.info(
            "PostFinance webhook: processing transaction %s state=%s for payment %s "
            "(current pretix state=%s)",
            entity_id,
            transaction_state,
            payment.pk,
            payment.state,
        )

        # Update payment info with latest transaction data
        payment_method = None
        if transaction.payment_connector_configuration:
            payment_method = transaction.payment_connector_configuration.name

        payment.info_data = payment.info_data or {}
        payment.info_data.update({
            "transaction_id": entity_id,
            "state": transaction_state.value if transaction_state else None,
            "payment_method": payment_method,
        })
        payment.save(update_fields=["info"])

        # Process state transition (idempotent)
        return self._update_payment_state(payment, transaction_state)

    def _find_payment_by_transaction_id(
        self,
        transaction_id: int,
    ) -> OrderPayment | None:
        """
        Find a payment record by PostFinance transaction ID.

        Searches for payments that have the given transaction ID stored
        in their info_data.

        Args:
            transaction_id: The PostFinance transaction ID.

        Returns:
            The OrderPayment if found, None otherwise.
        """
        # Search for payment with this transaction ID in info_data
        # info_data is stored as JSON in the 'info' field
        payments = OrderPayment.objects.filter(
            provider="postfinance",
            info__icontains=str(transaction_id),
        )

        for payment in payments:
            info_data = payment.info_data or {}
            if str(info_data.get("transaction_id")) == str(transaction_id):
                return payment

        return None

    def _update_payment_state(
        self,
        payment: OrderPayment,
        transaction_state: TransactionState | None,
    ) -> bool:
        """
        Update payment state based on PostFinance transaction state.

        This method is idempotent - calling it multiple times with the
        same state will have the same result.

        Args:
            payment: The OrderPayment to update.
            transaction_state: The PostFinance transaction state.

        Returns:
            True if a state transition occurred, False if already in final state.
        """
        if transaction_state is None:
            logger.warning(
                "PostFinance webhook: no transaction state for payment %s",
                payment.pk,
            )
            return False

        # Check if payment is already in a final state (idempotent check)
        if payment.state in (
            OrderPayment.PAYMENT_STATE_CONFIRMED,
            OrderPayment.PAYMENT_STATE_REFUNDED,
        ):
            logger.info(
                "PostFinance webhook: payment %s already confirmed/refunded, "
                "skipping state update (transaction state: %s)",
                payment.pk,
                transaction_state,
            )
            return False

        # For failed/canceled payments, only allow recovery to success states
        if (
            payment.state
            in (OrderPayment.PAYMENT_STATE_FAILED, OrderPayment.PAYMENT_STATE_CANCELED)
            and transaction_state not in SUCCESS_STATES
        ):
            logger.info(
                "PostFinance webhook: payment %s already failed/canceled, "
                "skipping non-success state update (transaction state: %s)",
                payment.pk,
                transaction_state,
            )
            return False

        # Handle success states (AUTHORIZED, COMPLETED, FULFILL, CONFIRMED, PROCESSING)
        if transaction_state in SUCCESS_STATES:
            try:
                payment.confirm()
                logger.info(
                    "PostFinance webhook: payment %s confirmed via webhook "
                    "(transaction state: %s)",
                    payment.pk,
                    transaction_state,
                )
                return True
            except Exception as e:
                logger.exception(
                    "PostFinance webhook: error confirming payment %s: %s",
                    payment.pk,
                    e,
                )
                return False

        # Handle failure states (FAILED, DECLINE, VOIDED)
        if transaction_state in FAILURE_STATES:
            payment.fail(info={"state": transaction_state.value})
            logger.info(
                "PostFinance webhook: payment %s failed via webhook "
                "(transaction state: %s)",
                payment.pk,
                transaction_state,
            )
            return True

        # Handle pending/intermediate states (CREATE, PENDING)
        if payment.state == OrderPayment.PAYMENT_STATE_CREATED:
            payment.state = OrderPayment.PAYMENT_STATE_PENDING
            payment.save(update_fields=["state"])
            logger.info(
                "PostFinance webhook: payment %s set to pending via webhook "
                "(transaction state: %s)",
                payment.pk,
                transaction_state,
            )
            return True

        logger.debug(
            "PostFinance webhook: no state change for payment %s "
            "(transaction state: %s, payment state: %s)",
            payment.pk,
            transaction_state,
            payment.state,
        )
        return False

    def _process_refund_state(
        self,
        entity_id: int | None,
        space_id: int,
        state: str | None,
    ) -> bool | None:
        """
        Process refund state update from webhook.

        Finds the payment that has a refund with the given refund ID in its
        refund_history, fetches the current refund state from PostFinance,
        and updates the payment info accordingly.

        Args:
            entity_id: The PostFinance refund ID (entityId from webhook).
            space_id: The PostFinance space ID.
            state: The refund state from the webhook (may be None).

        Returns:
            True if the refund status was updated, False if already processed,
            None if payment/refund not found or could not be processed.
        """
        if not entity_id:
            return None

        # Find payment by refund ID in refund_history
        payment = self._find_payment_by_refund_id(entity_id)
        if not payment:
            logger.debug(
                "PostFinance webhook: no payment found with refund ID %s",
                entity_id,
            )
            return None

        # Get client for this space
        client = self._get_client_for_space(space_id)
        if not client:
            logger.error(
                "PostFinance webhook: could not get client for spaceId=%s "
                "while processing refund %s",
                space_id,
                entity_id,
            )
            return None

        # Fetch full refund details from PostFinance
        try:
            refund = client.get_refund(int(entity_id))
        except PostFinanceError as e:
            logger.error(
                "PostFinance webhook: failed to fetch refund %s: %s",
                entity_id,
                e,
            )
            return None

        refund_state = refund.state
        refund_amount = float(refund.amount) if refund.amount else None
        refund_date = str(refund.created_on) if refund.created_on else None

        logger.info(
            "PostFinance webhook: processing refund %s state=%s amount=%s date=%s "
            "for payment %s",
            entity_id,
            refund_state,
            refund_amount,
            refund_date,
            payment.pk,
        )

        # Update refund entry in payment info
        info_data = payment.info_data or {}
        refund_history = info_data.get("refund_history", [])

        # Find and update the matching refund entry
        updated = False
        for entry in refund_history:
            if entry.get("refund_id") == entity_id:
                old_state = entry.get("refund_state")
                new_state = refund_state.value if refund_state else None
                entry["refund_state"] = new_state
                if refund_amount is not None:
                    entry["refund_amount"] = refund_amount
                if refund_date and not entry.get("refund_date"):
                    entry["refund_date"] = refund_date
                updated = True
                logger.info(
                    "PostFinance webhook: refund %s state updated from %s to %s "
                    "for payment %s",
                    entity_id,
                    old_state,
                    new_state,
                    payment.pk,
                )
                break

        if not updated:
            # Refund ID not in history - might be a new refund created externally
            # Add it to the history
            new_entry = {
                "refund_id": entity_id,
                "refund_state": refund_state.value if refund_state else None,
                "refund_amount": refund_amount,
                "refund_date": refund_date,
            }
            refund_history.append(new_entry)
            logger.info(
                "PostFinance webhook: added new refund %s to history for payment %s "
                "(state=%s, amount=%s)",
                entity_id,
                payment.pk,
                refund_state,
                refund_amount,
            )

            # Update total refunded amount if this is a successful refund
            if refund_state and refund_state.value == "SUCCESSFUL" and refund_amount:
                total_refunded = float(info_data.get("total_refunded_amount", 0))
                info_data["total_refunded_amount"] = total_refunded + refund_amount

        # Save updated info
        info_data["refund_history"] = refund_history
        # Also update the last refund fields for backwards compatibility
        info_data["refund_id"] = entity_id
        info_data["refund_state"] = refund_state.value if refund_state else None
        payment.info_data = info_data
        payment.save(update_fields=["info"])

        return True

    def _find_payment_by_refund_id(
        self,
        refund_id: int,
    ) -> OrderPayment | None:
        """
        Find a payment record by PostFinance refund ID.

        Searches for payments that have the given refund ID in their
        refund_history stored in info_data.

        Args:
            refund_id: The PostFinance refund ID.

        Returns:
            The OrderPayment if found, None otherwise.
        """
        # Search for payment with this refund ID in info_data
        # info_data is stored as JSON in the 'info' field
        payments = OrderPayment.objects.filter(
            provider="postfinance",
            info__icontains=str(refund_id),
        )

        for payment in payments:
            info_data = payment.info_data or {}

            # Check refund_history list
            refund_history = info_data.get("refund_history", [])
            for entry in refund_history:
                if str(entry.get("refund_id")) == str(refund_id):
                    return payment

            # Also check legacy refund_id field
            if str(info_data.get("refund_id")) == str(refund_id):
                return payment

        return None

    def _parse_payload(self, request: HttpRequest) -> dict[str, Any]:
        """
        Parse and validate the webhook payload.

        Args:
            request: The HTTP request containing the webhook payload.

        Returns:
            The parsed JSON payload as a dictionary.

        Raises:
            ValueError: If the payload cannot be parsed or is invalid.
        """
        content_type = request.content_type or ""

        if "application/json" not in content_type:
            raise ValueError(f"Invalid content type: {content_type}")

        if not request.body:
            raise ValueError("Empty request body")

        try:
            payload = json.loads(request.body.decode("utf-8"))
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON: {e}") from e

        if not isinstance(payload, dict):
            raise ValueError("Payload must be a JSON object")

        return payload


class PostFinanceCaptureView(EventPermissionRequiredMixin, View):
    """
    Handle manual capture requests from the admin panel.

    This view is called when an administrator clicks the "Capture Payment"
    button for an AUTHORIZED payment. It completes the transaction via
    the PostFinance API.
    """

    permission = "can_change_orders"

    def post(self, request: PretixHttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        """
        Process the capture request.

        Args:
            request: The HTTP request.

        Returns:
            Redirect to the order page with success or error message.
        """
        order_code = kwargs.get("order")
        payment_pk = kwargs.get("payment")

        order = get_object_or_404(
            Order,
            code=order_code,
            event=request.event,
        )
        payment = get_object_or_404(
            OrderPayment,
            pk=payment_pk,
            order=order,
            provider="postfinance",
        )

        # Get the payment provider and execute capture
        provider = payment.payment_provider
        success, error_message = provider.execute_capture(payment)

        if success:
            messages.success(
                request,
                str(_("Payment captured successfully.")),
            )
            logger.info(
                "Admin capture successful for payment %s by user %s",
                payment.pk,
                request.user.pk if request.user else "anonymous",
            )
        else:
            messages.error(
                request,
                error_message or str(_("Failed to capture payment.")),
            )
            logger.warning(
                "Admin capture failed for payment %s: %s",
                payment.pk,
                error_message,
            )

        # Redirect back to the order page
        return redirect(
            "control:event.order",
            organizer=request.event.organizer.slug,
            event=request.event.slug,
            code=order.code,
        )


class PostFinanceVoidView(EventPermissionRequiredMixin, View):
    """
    Handle void requests from the admin panel.

    This view is called when an administrator clicks the "Void Payment"
    button for an AUTHORIZED payment. It voids the transaction via
    the PostFinance API, releasing the authorization hold.
    """

    permission = "can_change_orders"

    def post(self, request: PretixHttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        """
        Process the void request.

        Args:
            request: The HTTP request.

        Returns:
            Redirect to the order page with success or error message.
        """
        order_code = kwargs.get("order")
        payment_pk = kwargs.get("payment")

        order = get_object_or_404(
            Order,
            code=order_code,
            event=request.event,
        )
        payment = get_object_or_404(
            OrderPayment,
            pk=payment_pk,
            order=order,
            provider="postfinance",
        )

        # Get the payment provider and execute void
        provider = payment.payment_provider
        success, error_message = provider.execute_void(payment)

        if success:
            messages.success(
                request,
                str(_("Payment voided successfully.")),
            )
            logger.info(
                "Admin void successful for payment %s by user %s",
                payment.pk,
                request.user.pk if request.user else "anonymous",
            )
        else:
            messages.error(
                request,
                error_message or str(_("Failed to void payment.")),
            )
            logger.warning(
                "Admin void failed for payment %s: %s",
                payment.pk,
                error_message,
            )

        # Redirect back to the order page
        return redirect(
            "control:event.order",
            organizer=request.event.organizer.slug,
            event=request.event.slug,
            code=order.code,
        )


class PostFinanceRefundView(EventPermissionRequiredMixin, View):
    """
    Handle refund requests from the admin panel.

    This view is called when an administrator clicks the "Refund Payment"
    button for a COMPLETED payment. It creates a full or partial refund via
    the PostFinance API.
    """

    permission = "can_change_orders"

    def post(self, request: PretixHttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        """
        Process the refund request.

        Supports both full and partial refunds. If an amount is provided in the
        POST data, it will be used for a partial refund. Otherwise, the full
        remaining refundable amount will be refunded.

        Args:
            request: The HTTP request.

        Returns:
            Redirect to the order page with success or error message.
        """
        from decimal import Decimal, InvalidOperation

        order_code = kwargs.get("order")
        payment_pk = kwargs.get("payment")

        order = get_object_or_404(
            Order,
            code=order_code,
            event=request.event,
        )
        payment = get_object_or_404(
            OrderPayment,
            pk=payment_pk,
            order=order,
            provider="postfinance",
        )

        # Parse refund amount from POST data (optional for partial refunds)
        refund_amount: Decimal | None = None
        amount_str = request.POST.get("amount", "").strip()
        if amount_str:
            try:
                refund_amount = Decimal(amount_str)
            except InvalidOperation:
                messages.error(
                    request,
                    str(_("Invalid refund amount.")),
                )
                return redirect(
                    "control:event.order",
                    organizer=request.event.organizer.slug,
                    event=request.event.slug,
                    code=order.code,
                )

        # Get the payment provider and execute refund
        provider = payment.payment_provider
        success, error_message = provider.execute_refund(payment, amount=refund_amount)

        if success:
            if refund_amount:
                messages.success(
                    request,
                    str(
                        _("Refund of {amount} {currency} initiated successfully.").format(
                            amount=refund_amount,
                            currency=request.event.currency,
                        )
                    ),
                )
            else:
                messages.success(
                    request,
                    str(_("Refund initiated successfully.")),
                )
            logger.info(
                "Admin refund successful for payment %s (amount=%s) by user %s",
                payment.pk,
                refund_amount or "full",
                request.user.pk if request.user else "anonymous",
            )
        else:
            messages.error(
                request,
                error_message or str(_("Failed to process refund.")),
            )
            logger.warning(
                "Admin refund failed for payment %s: %s",
                payment.pk,
                error_message,
            )

        # Redirect back to the order page
        return redirect(
            "control:event.order",
            organizer=request.event.organizer.slug,
            event=request.event.slug,
            code=order.code,
        )
