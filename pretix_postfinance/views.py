"""
Views for PostFinance payment plugin.

Handles return URLs from PostFinance payment page and webhook callbacks.
"""

import json
import logging
from typing import Any, Dict, Optional

from django.contrib import messages
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.utils.decorators import method_decorator
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from postfinancecheckout.models import TransactionState
from pretix.base.models import Event, Order, OrderPayment
from pretix.multidomain.urlreverse import eventreverse

from .api import PostFinanceClient, PostFinanceError
from .payment import FAILURE_STATES, SUCCESS_STATES

logger = logging.getLogger(__name__)


class PostFinanceReturnView(View):
    """
    Handle return from PostFinance payment page.

    This view is called when the customer returns from the PostFinance
    payment page after completing or cancelling payment.
    """

    def get(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
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
        state: Optional[TransactionState],
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

        logger.info(
            "PostFinance webhook received: entityId=%s, listenerEntityId=%s, "
            "spaceId=%s, state=%s",
            payload.get("entityId"),
            payload.get("listenerEntityId"),
            space_id,
            payload.get("state"),
        )

        # Return 200 OK to acknowledge receipt
        # Actual state processing will be implemented in US-012
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

    def _get_client_for_space(self, space_id: int) -> Optional[PostFinanceClient]:
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

    def _parse_payload(self, request: HttpRequest) -> Dict[str, Any]:
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
            raise ValueError(f"Invalid JSON: {e}")

        if not isinstance(payload, dict):
            raise ValueError("Payload must be a JSON object")

        return payload
