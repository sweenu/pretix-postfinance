"""
Views for PostFinance payment plugin.

Handles webhook callbacks and admin capture action.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from django.contrib import messages
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django_scopes import scopes_disabled
from pretix.base.models import Order, OrderPayment, OrderRefund
from pretix.control.permissions import EventPermissionRequiredMixin

from ._types import PretixHttpRequest
from .api import PostFinanceClient, PostFinanceError
from .payment import FAILURE_STATES, SUCCESS_STATES

logger = logging.getLogger(__name__)


@csrf_exempt
@scopes_disabled()
def webhook(request: HttpRequest) -> HttpResponse:
    """
    Handle webhook notifications from PostFinance.

    PostFinance sends webhook notifications when transaction or refund states change.
    """
    if request.method != "POST":
        return HttpResponse(status=405)

    # Parse payload
    content_type = request.content_type or ""
    if "application/json" not in content_type:
        logger.warning("PostFinance webhook: invalid content type %s", content_type)
        return JsonResponse({"error": "Invalid content type"}, status=400)

    try:
        payload = json.loads(request.body.decode("utf-8"))
    except json.JSONDecodeError as e:
        logger.warning("PostFinance webhook: invalid JSON - %s", e)
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    if not isinstance(payload, dict):
        return JsonResponse({"error": "Payload must be a JSON object"}, status=400)

    space_id = payload.get("spaceId")
    entity_id = payload.get("entityId")

    if not space_id:
        logger.warning("PostFinance webhook: missing spaceId")
        return JsonResponse({"error": "Missing spaceId"}, status=400)

    logger.info(
        "PostFinance webhook: spaceId=%s, entityId=%s",
        space_id,
        entity_id,
    )

    # Validate signature if present
    signature_header = request.headers.get("X-Signature")
    if signature_header:
        client = _get_client_for_space(space_id)
        if client:
            try:
                if not client.is_webhook_signature_valid(
                    signature_header=signature_header,
                    content=request.body.decode("utf-8"),
                ):
                    logger.warning(
                        "PostFinance webhook: invalid signature for spaceId=%s", space_id
                    )
                    return JsonResponse({"error": "Invalid signature"}, status=401)
            except PostFinanceError as e:
                logger.error("PostFinance webhook: signature validation error - %s", e)
                return JsonResponse({"error": "Signature validation error"}, status=401)

    # Process webhook
    if entity_id:
        result = _process_transaction_webhook(entity_id, space_id)
        if result is None:
            _process_refund_webhook(entity_id, space_id)

    return HttpResponse(status=200)


def _get_client_for_space(space_id: int) -> PostFinanceClient | None:
    """Find and return a PostFinanceClient for the given space ID."""
    from pretix.base.models import Event
    from pretix.base.settings import GlobalSettingsObject

    try:
        global_settings = GlobalSettingsObject()
        configured_space = global_settings.settings.get("payment_postfinance_space_id")
        if configured_space and str(configured_space) == str(space_id):
            gs = global_settings.settings
            return PostFinanceClient(
                space_id=int(configured_space),
                user_id=int(gs.get("payment_postfinance_user_id", 0)),
                api_secret=str(gs.get("payment_postfinance_auth_key", "")),
            )
    except Exception as e:
        logger.debug("Could not check global settings: %s", e)

    for event in Event.objects.filter(live=True):
        try:
            event_space_id = event.settings.get("payment_postfinance_space_id")
            if str(event_space_id) == str(space_id):
                es = event.settings
                return PostFinanceClient(
                    space_id=int(event_space_id),
                    user_id=int(es.get("payment_postfinance_user_id", 0)),
                    api_secret=str(es.get("payment_postfinance_auth_key", "")),
                )
        except Exception as e:
            logger.debug("Could not check event %s settings: %s", event.slug, e)

    return None


def _process_transaction_webhook(entity_id: int, space_id: int) -> bool | None:
    """Process a transaction state update from webhook."""
    payment = None
    for p in OrderPayment.objects.filter(
        provider="postfinance",
        info__icontains=str(entity_id),
    ):
        info_data = p.info_data or {}
        if str(info_data.get("transaction_id")) == str(entity_id):
            payment = p
            break

    if not payment:
        return None

    client = _get_client_for_space(space_id)
    if not client:
        logger.error("PostFinance webhook: no client for spaceId=%s", space_id)
        return None

    try:
        transaction = client.get_transaction(int(entity_id))
    except PostFinanceError as e:
        logger.error("PostFinance webhook: failed to fetch transaction %s: %s", entity_id, e)
        return None

    transaction_state = transaction.state

    payment_method = None
    if transaction.payment_connector_configuration:
        payment_method = transaction.payment_connector_configuration.name

    payment.info_data = payment.info_data or {}
    payment.info_data.update(
        {
            "transaction_id": entity_id,
            "state": transaction_state.value if transaction_state else None,
            "payment_method": payment_method,
        }
    )
    payment.save(update_fields=["info"])

    payment.order.log_action(
        "pretix_postfinance.webhook",
        data={
            "transaction_id": entity_id,
            "state": transaction_state.value if transaction_state else None,
        },
    )

    if payment.state in (
        OrderPayment.PAYMENT_STATE_CONFIRMED,
        OrderPayment.PAYMENT_STATE_REFUNDED,
    ):
        return False

    if transaction_state in SUCCESS_STATES:
        try:
            payment.confirm()
            logger.info("PostFinance webhook: payment %s confirmed", payment.pk)
        except Exception as e:
            logger.exception("PostFinance webhook: error confirming payment %s: %s", payment.pk, e)
        return True

    if transaction_state in FAILURE_STATES:
        payment.state = OrderPayment.PAYMENT_STATE_FAILED
        payment.save(update_fields=["state"])
        payment.order.log_action(
            "pretix.event.order.payment.failed",
            {
                "local_id": payment.local_id,
                "provider": payment.provider,
            },
        )
        logger.info("PostFinance webhook: payment %s failed", payment.pk)
        return True

    # Handle pending/intermediate states
    if payment.state == OrderPayment.PAYMENT_STATE_CREATED:
        payment.state = OrderPayment.PAYMENT_STATE_PENDING
        payment.save(update_fields=["state"])
        logger.info("PostFinance webhook: payment %s set to pending", payment.pk)
        return True

    return False


def _process_refund_webhook(entity_id: int, space_id: int) -> bool | None:
    """Process a refund state update from webhook."""
    refund = None
    for r in OrderRefund.objects.filter(
        provider="postfinance",
        info__icontains=str(entity_id),
    ):
        info_data = r.info_data or {}
        if str(info_data.get("refund_id")) == str(entity_id):
            refund = r
            break

    if not refund:
        return None

    client = _get_client_for_space(space_id)
    if not client:
        return None

    try:
        pf_refund = client.get_refund(int(entity_id))
    except PostFinanceError as e:
        logger.error("PostFinance webhook: failed to fetch refund %s: %s", entity_id, e)
        # Store error details in refund.info for admin visibility
        info_data = refund.info_data or {}
        info_data.update(
            {
                "error": str(e),
                "error_code": e.error_code,
                "error_status_code": e.status_code,
            }
        )
        refund.info = json.dumps(info_data)
        refund.save(update_fields=["info"])
        return None

    refund_state = pf_refund.state

    info_data = refund.info_data or {}
    info_data["refund_id"] = entity_id
    info_data["state"] = refund_state.value if refund_state else None
    refund.info = json.dumps(info_data)
    refund.save(update_fields=["info"])

    refund.order.log_action(
        "pretix_postfinance.refund.webhook",
        data={
            "refund_id": entity_id,
            "state": refund_state.value if refund_state else None,
        },
    )

    if refund_state and refund_state.value == "SUCCESSFUL":
        if refund.state != OrderRefund.REFUND_STATE_DONE:
            refund.done()
            logger.info("PostFinance webhook: refund %s marked done", refund.pk)
        return True

    if refund_state and refund_state.value == "FAILED":
        if refund.state not in (OrderRefund.REFUND_STATE_DONE, OrderRefund.REFUND_STATE_FAILED):
            refund.state = OrderRefund.REFUND_STATE_FAILED
            refund.save(update_fields=["state"])
            refund.order.log_action(
                "pretix.event.order.refund.failed",
                {
                    "local_id": refund.local_id,
                    "provider": refund.provider,
                },
            )
            logger.info("PostFinance webhook: refund %s failed", refund.pk)
        return True

    return False


class PostFinanceTestConnectionView(EventPermissionRequiredMixin, View):
    """AJAX endpoint for testing PostFinance API connection."""

    permission = "can_change_event_settings"

    def post(self, request: PretixHttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        providers = request.event.get_payment_providers()
        provider = providers.get("postfinance")

        if not provider:
            return JsonResponse(
                {
                    "success": False,
                    "message": str(_("PostFinance payment provider not found.")),
                }
            )

        success, message = provider.test_connection()
        return JsonResponse({"success": success, "message": message})


class PostFinanceSetupWebhooksView(EventPermissionRequiredMixin, View):
    """AJAX endpoint for setting up PostFinance webhooks automatically."""

    permission = "can_change_event_settings"

    def post(self, request: PretixHttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        providers = request.event.get_payment_providers()
        provider = providers.get("postfinance")

        if not provider:
            return JsonResponse(
                {
                    "success": False,
                    "message": str(_("PostFinance payment provider not found.")),
                }
            )

        space_id = provider.settings.get("space_id")
        user_id = provider.settings.get("user_id")
        auth_key = provider.settings.get("auth_key")

        if not all([space_id, user_id, auth_key]):
            return JsonResponse(
                {
                    "success": False,
                    "message": str(
                        _(
                            "Please configure Space ID, User ID, and Authentication Key before "
                            "setting up webhooks."
                        )
                    ),
                }
            )

        from pretix.helpers.urls import build_absolute_uri as build_global_uri

        webhook_url = build_global_uri("plugins:pretix_postfinance:postfinance.webhook")

        try:
            client = PostFinanceClient(
                space_id=int(space_id),
                user_id=int(user_id),
                api_secret=str(auth_key),
            )
            result = client.setup_webhooks(webhook_url)

            return JsonResponse(
                {
                    "success": True,
                    "message": str(
                        _(
                            "Webhooks configured successfully! "
                            "Transaction updates will now be received automatically."
                        )
                    ),
                    "details": result,
                }
            )
        except PostFinanceError as e:
            return JsonResponse(
                {
                    "success": False,
                    "message": str(_("Failed to setup webhooks: {error}").format(error=str(e))),
                }
            )


class PostFinanceCaptureView(EventPermissionRequiredMixin, View):
    """Handle manual capture requests for AUTHORIZED payments."""

    permission = "can_change_orders"

    def post(self, request: PretixHttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        order = get_object_or_404(Order, code=kwargs["order"], event=request.event)
        payment = get_object_or_404(
            OrderPayment, pk=kwargs["payment"], order=order, provider="postfinance"
        )

        provider = payment.payment_provider
        success, error_message = provider.execute_capture(payment)

        if success:
            messages.success(request, str(_("Payment captured successfully.")))
        else:
            messages.error(request, error_message or str(_("Failed to capture payment.")))

        return redirect(
            "control:event.order",
            organizer=request.event.organizer.slug,
            event=request.event.slug,
            code=order.code,
        )
