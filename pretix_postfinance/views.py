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

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        """
        Process incoming webhook notification from PostFinance.

        Args:
            request: The HTTP request containing the webhook payload.

        Returns:
            HttpResponse with status 200 on success, 400 on malformed requests.
        """
        try:
            payload = self._parse_payload(request)
        except ValueError as e:
            logger.warning("PostFinance webhook: malformed request - %s", e)
            return JsonResponse(
                {"error": "Malformed request", "detail": str(e)},
                status=400,
            )

        logger.info(
            "PostFinance webhook received: entityId=%s, listenerEntityId=%s, state=%s",
            payload.get("entityId"),
            payload.get("listenerEntityId"),
            payload.get("state"),
        )

        # Return 200 OK to acknowledge receipt
        # Actual state processing will be implemented in US-012
        return HttpResponse(status=200)

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
