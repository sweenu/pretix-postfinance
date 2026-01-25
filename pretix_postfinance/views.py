"""
Views for PostFinance payment plugin.

Handles webhook callbacks and admin capture action.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import timedelta
from typing import Any

from django.contrib import messages
from django.core.mail import send_mail
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.utils.timezone import now
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django_scopes import scopes_disabled
from pretix.base.models import Order, OrderPayment, OrderRefund
from pretix.control.permissions import EventPermissionRequiredMixin
from pretix.multidomain.urlreverse import build_absolute_uri

from ._types import PretixHttpRequest
from .api import PostFinanceClient, PostFinanceError
from .models import InstallmentSchedule
from .payment import FAILURE_STATES, SUCCESS_STATES
from .tasks import _send_organizer_failure_notification

logger = logging.getLogger(__name__)

WEBHOOK_STATUS_NOT_FOUND = "not_found"
WEBHOOK_STATUS_NO_CLIENT = "no_client"
WEBHOOK_STATUS_API_ERROR = "api_error"
WEBHOOK_STATUS_OK = "ok"


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

    signature_header = request.headers.get("X-Signature")

    # Security logging helper
    def _log_security_event(reason: str) -> None:
        """Log webhook signature failure as security event."""
        payload_hash = hashlib.sha256(request.body).hexdigest()
        client_ip = _get_client_ip(request)
        logger.error(
            "security.webhook.signature_failure: reason=%s, space_id=%s, entity_id=%s, "
            "client_ip=%s, payload_hash=%s",
            reason,
            space_id,
            entity_id,
            client_ip,
            payload_hash,
        )

    # Validate signature
    if signature_header:
        client = _get_client_for_space(space_id)
        if client:
            try:
                if not client.is_webhook_signature_valid(
                    signature_header=signature_header,
                    content=request.body.decode("utf-8"),
                ):
                    _log_security_event("invalid_signature")
                    return JsonResponse({"error": "Invalid signature"}, status=401)
            except PostFinanceError as e:
                logger.error("PostFinance webhook: signature validation error - %s", e)
                _log_security_event("validation_error")
                return JsonResponse({"error": "Signature validation error"}, status=401)
    else:
        # Signature is required but not present
        _log_security_event("missing_signature")
        return JsonResponse({"error": "Signature required"}, status=401)

    # Process webhook and return appropriate HTTP status code:
    # - 200: Success or entity not found in our DB (legitimate "not ours" case)
    # - 500: Configuration error (no client configured for space)
    # - 502: External API error (PostFinance API call failed, retriable)
    if entity_id:
        status, _ = _process_transaction_webhook(entity_id, space_id)

        if status == WEBHOOK_STATUS_NOT_FOUND:
            # Try installment processing if transaction not found
            status, _ = _process_installment_webhook(entity_id, space_id)

            if status == WEBHOOK_STATUS_NOT_FOUND:
                # Try refund processing if installment not found
                status, _ = _process_refund_webhook(entity_id, space_id)

        if status == WEBHOOK_STATUS_NO_CLIENT:
            return JsonResponse(
                {"error": "No PostFinance client configured for this space"},
                status=500,
            )

        if status == WEBHOOK_STATUS_API_ERROR:
            return JsonResponse(
                {"error": "Failed to fetch entity from PostFinance API"},
                status=502,
            )

    return HttpResponse(status=200)


def _get_client_ip(request: HttpRequest) -> str:
    """Extract client IP address, handling reverse proxy headers."""
    x_forwarded_for = request.headers.get("X-Forwarded-For")
    if x_forwarded_for:
        # Take the first IP in the chain (original client)
        return x_forwarded_for.split(",")[0].strip()
    remote_addr = request.META.get("REMOTE_ADDR")
    return remote_addr if remote_addr else "unknown"


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


def _process_transaction_webhook(entity_id: int, space_id: int) -> tuple[str, bool | None]:
    """
    Process a transaction state update from webhook.

    Returns:
        tuple[str, bool | None]: A tuple of (status, processed) where:
            - status: WEBHOOK_STATUS_NOT_FOUND (entity not in our DB),
                      WEBHOOK_STATUS_NO_CLIENT (configuration error),
                      WEBHOOK_STATUS_API_ERROR (PostFinance API failed),
                      WEBHOOK_STATUS_OK (processed successfully)
            - processed: True if state changed, False if no change, None if not applicable
    """
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
        # Entity not found in our database - this webhook isn't for us
        return (WEBHOOK_STATUS_NOT_FOUND, None)

    client = _get_client_for_space(space_id)
    if not client:
        # Configuration error - no client configured for this space
        logger.error(
            "PostFinance webhook: no client configured for spaceId=%s, transaction=%s",
            space_id,
            entity_id,
        )
        return (WEBHOOK_STATUS_NO_CLIENT, None)

    try:
        transaction = client.get_transaction(int(entity_id))
    except PostFinanceError as e:
        # External API error - PostFinance API call failed
        logger.error(
            "PostFinance webhook: failed to fetch transaction %s: %s (status=%s, code=%s)",
            entity_id,
            e.message,
            e.status_code,
            e.error_code,
        )
        return (WEBHOOK_STATUS_API_ERROR, None)

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
        return (WEBHOOK_STATUS_OK, False)

    if transaction_state in SUCCESS_STATES:
        try:
            payment.confirm()
            logger.info("PostFinance webhook: payment %s confirmed", payment.pk)
        except Exception as e:
            logger.exception("PostFinance webhook: error confirming payment %s: %s", payment.pk, e)
        return (WEBHOOK_STATUS_OK, True)

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
        return (WEBHOOK_STATUS_OK, True)

    # Handle pending/intermediate states
    if payment.state == OrderPayment.PAYMENT_STATE_CREATED:
        payment.state = OrderPayment.PAYMENT_STATE_PENDING
        payment.save(update_fields=["state"])
        logger.info("PostFinance webhook: payment %s set to pending", payment.pk)
        return (WEBHOOK_STATUS_OK, True)

    return (WEBHOOK_STATUS_OK, False)


def _process_refund_webhook(entity_id: int, space_id: int) -> tuple[str, bool | None]:
    """
    Process a refund state update from webhook.

    Returns:
        tuple[str, bool | None]: A tuple of (status, processed) where:
            - status: "not_found" (entity not in our DB),
                      "no_client" (configuration error),
                      "api_error" (PostFinance API failed),
                      "ok" (processed successfully)
            - processed: True if state changed, False if no change, None if not applicable
    """
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
        # Entity not found in our database - this webhook isn't for us
        return (WEBHOOK_STATUS_NOT_FOUND, None)

    client = _get_client_for_space(space_id)
    if not client:
        # Configuration error - no client configured for this space
        logger.error(
            "PostFinance webhook: no client configured for spaceId=%s, refund=%s",
            space_id,
            entity_id,
        )
        return (WEBHOOK_STATUS_NO_CLIENT, None)

    try:
        pf_refund = client.get_refund(int(entity_id))
    except PostFinanceError as e:
        # External API error - PostFinance API call failed
        logger.error(
            "PostFinance webhook: failed to fetch refund %s: %s (status=%s, code=%s)",
            entity_id,
            e.message,
            e.status_code,
            e.error_code,
        )
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
        return (WEBHOOK_STATUS_API_ERROR, None)

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
        return (WEBHOOK_STATUS_OK, True)

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
        return (WEBHOOK_STATUS_OK, True)

    return (WEBHOOK_STATUS_OK, False)


def _process_installment_webhook(entity_id: int, space_id: int) -> tuple[str, bool | None]:
    """
    Process an installment-related transaction from webhook.

    This function handles webhook notifications for installment payments that were
    initiated by the background tasks (process_due_installments, retry_failed_installments).

    Returns:
        tuple[str, bool | None]: A tuple of (status, processed) where:
            - status: WEBHOOK_STATUS_NOT_FOUND (entity not in our DB),
                      WEBHOOK_STATUS_NO_CLIENT (configuration error),
                      WEBHOOK_STATUS_API_ERROR (PostFinance API failed),
                      WEBHOOK_STATUS_OK (processed successfully)
            - processed: True if state changed, False if no change, None if not applicable
    """
    # Try to find the installment by parsing the merchant reference
    # Merchant reference format: pretix-installment-{order.code}-{installment_number}
    installment = None
    order_code = None
    installment_number = None

    # Parse merchant reference to extract order code and installment number
    merchant_reference_pattern = "pretix-installment-"
    if str(entity_id).startswith(merchant_reference_pattern):
        # Extract the parts after "pretix-installment-"
        parts = str(entity_id).split("-")
        if len(parts) >= 3:
            order_code = parts[-2]  # Second to last part
            try:
                installment_number = int(parts[-1])  # Last part
            except ValueError:
                installment_number = None

    if not order_code or not installment_number:
        # Try alternative format: pretix-{event.slug}-installment-{installment_number}
        # This is used by the background tasks
        parts = str(entity_id).split("-")
        if len(parts) >= 4 and parts[-2] == "installment":
            try:
                installment_number = int(parts[-1])
                # We need to find the order by searching for installments with this number
                installments = InstallmentSchedule.objects.filter(
                    installment_number=installment_number
                )
                if installments.exists():
                    installment = installments.first()
            except ValueError:
                pass
    else:
        # Try to find the order by code
        from pretix.base.models import Order
        try:
            order = Order.objects.get(code=order_code)
            installment = InstallmentSchedule.objects.filter(
                order=order,
                installment_number=installment_number
            ).first()
        except Order.DoesNotExist:
            pass

    if not installment:
        # Entity not found in our database - this webhook isn't for us
        return (WEBHOOK_STATUS_NOT_FOUND, None)

    client = _get_client_for_space(space_id)
    if not client:
        # Configuration error - no client configured for this space
        logger.error(
            "PostFinance webhook: no client configured for spaceId=%s, installment transaction=%s",
            space_id,
            entity_id,
        )
        return (WEBHOOK_STATUS_NO_CLIENT, None)

    try:
        transaction = client.get_transaction(int(entity_id))
    except PostFinanceError as e:
        # External API error - PostFinance API call failed
        logger.error(
            "PostFinance webhook: failed to fetch installment transaction %s: %s "
            "(status=%s, code=%s)",
            entity_id,
            e.message,
            e.status_code,
            e.error_code,
        )
        return (WEBHOOK_STATUS_API_ERROR, None)

    transaction_state = transaction.state
    if not transaction_state:
        logger.warning(
            "PostFinance webhook: installment transaction %s has no state",
            entity_id,
        )
        return (WEBHOOK_STATUS_OK, False)

    logger.info(
        "PostFinance webhook: processing installment transaction %s for installment %s, state=%s",
        entity_id,
        installment.pk,
        transaction_state.value,
    )

    # Update installment status based on transaction state
    processed = False
    if transaction_state.value in SUCCESS_STATES:
        # Payment successful
        if installment.status != InstallmentSchedule.Status.PAID:
            installment.status = InstallmentSchedule.Status.PAID
            installment.paid_at = now()
            installment.failure_reason = None
            installment.grace_period_ends = None
            installment.save()

            # Create OrderPayment record for this installment
            from pretix.base.models import OrderPayment
            order_payment = OrderPayment(
                order=installment.order,
                amount=installment.amount,
                payment_date=now(),
                provider="postfinance",
                state=OrderPayment.PAYMENT_STATE_CONFIRMED,
                info_data={
                    "transaction_id": transaction.id,
                    "state": transaction_state.value,
                    "installment_number": installment.installment_number,
                    "installment_id": installment.pk,
                    "type": "installment",
                },
            )
            order_payment.save()

            # Link the payment to the installment
            installment.payment = order_payment
            installment.save()

            # Log the successful payment
            installment.order.log_action(
                "pretix_postfinance.installment.paid",
                data={
                    "installment_number": installment.installment_number,
                    "amount": str(installment.amount),
                    "transaction_id": transaction.id,
                    "payment_id": order_payment.pk,
                },
            )

            logger.info(
                "PostFinance webhook: installment %s for order %s marked as paid",
                installment.installment_number,
                installment.order.code,
            )

            # Send success email to customer
            _send_installment_webhook_success_email(installment)

            processed = True

    elif transaction_state.value in FAILURE_STATES:
        # Payment failed
        if installment.status != InstallmentSchedule.Status.FAILED:
            installment.status = InstallmentSchedule.Status.FAILED
            installment.failure_reason = f"PostFinance transaction state: {transaction_state.value}"
            installment.grace_period_ends = now() + timedelta(days=3)
            installment.save()

            # Log the failed payment
            installment.order.log_action(
                "pretix_postfinance.installment.failed",
                data={
                    "installment_number": installment.installment_number,
                    "amount": str(installment.amount),
                    "transaction_id": transaction.id,
                    "failure_reason": installment.failure_reason,
                },
            )

            logger.warning(
                "PostFinance webhook: installment %s for order %s marked as failed",
                installment.installment_number,
                installment.order.code,
            )

            # Send failure email to customer
            _send_installment_webhook_failed_email(installment)

            # Send failure notification to organizer
            _send_organizer_failure_notification(installment)

            processed = True

    else:
        # Intermediate/pending state - don't change installment status
        logger.info(
            "PostFinance webhook: installment transaction %s in intermediate state %s, "
            "no action taken",
            entity_id,
            transaction_state.value,
        )

    return (WEBHOOK_STATUS_OK, processed)


def _send_installment_webhook_success_email(installment: InstallmentSchedule) -> None:
    """Send email to customer when installment payment succeeds via webhook."""
    try:
        from django.template.loader import render_to_string

        order = installment.order
        event = order.event

        # Get remaining installments for the template
        remaining_installments = InstallmentSchedule.objects.filter(
            order=order,
            status__in=[InstallmentSchedule.Status.SCHEDULED, InstallmentSchedule.Status.PENDING]
        ).order_by('installment_number')

        # Render email template
        subject = f"Installment Payment Successful - {event.name}"

        context = {
            'order': order,
            'event': event,
            'installment': installment,
            'remaining_installments': remaining_installments,
        }

        html_message = render_to_string(
            'pretixplugins/postfinance/installment_payment_success.html',
            context
        )

        send_mail(
            subject,
            '',  # HTML email, no plain text version
            f"noreply@{event.organizer.slug}.pretix.example.com",
            [order.email],
            html_message=html_message,
            fail_silently=True,
        )

        logger.info(
            "Sent installment success email via webhook for installment %s",
            installment.installment_number,
        )

    except Exception as e:
        logger.error(
            "Failed to send installment success email via webhook for installment %s: %s",
            installment.installment_number,
            e,
        )


def _send_installment_webhook_failed_email(installment: InstallmentSchedule) -> None:
    """Send email to customer when installment payment fails via webhook."""
    try:
        from django.template.loader import render_to_string

        order = installment.order
        event = order.event

        subject = f"Installment Payment Failed - {event.name}"

        # Render email template
        context = {
            'order': order,
            'event': event,
            'installment': installment,
        }

        html_message = render_to_string(
            'pretixplugins/postfinance/installment_payment_failed.html',
            context
        )

        send_mail(
            subject,
            '',  # HTML email, no plain text version
            f"noreply@{event.organizer.slug}.pretix.example.com",
            [order.email],
            html_message=html_message,
            fail_silently=True,
        )

        logger.info(
            "Sent installment failure email via webhook for installment %s",
            installment.installment_number,
        )

    except Exception as e:
        logger.error(
            "Failed to send installment failure email via webhook for installment %s: %s",
            installment.installment_number,
            e,
        )


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
                            "Transaction updates will be received automatically."
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
        user = (
            getattr(request.user, "email", None)
            or getattr(request.user, "username", None)
            or str(request.user.pk)
        )
        success, error_message = provider.execute_capture(payment, user=user)

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


class PostFinanceRetryInstallmentView(EventPermissionRequiredMixin, View):
    """
    Handle manual retry requests for failed installment payments.

    This view allows event organizers to manually retry a failed installment payment
    by attempting to charge the saved token again.
    """

    permission = "can_change_orders"

    def post(self, request: PretixHttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        """
        Retry a failed installment payment.
        """
        order = get_object_or_404(Order, code=kwargs["order"], event=request.event)
        installment = get_object_or_404(
            InstallmentSchedule,
            pk=kwargs["installment"],
            order=order
        )

        # Check if this installment can be retried
        if installment.status != InstallmentSchedule.Status.FAILED:
            messages.error(
                request,
                str(_("Only failed installments can be retried."))
            )
            return redirect(
                "control:event.order",
                organizer=request.event.organizer.slug,
                event=request.event.slug,
                code=order.code,
            )

        if not installment.token_id:
            messages.error(
                request,
                str(_("No payment token available for this installment."))
            )
            return redirect(
                "control:event.order",
                organizer=request.event.organizer.slug,
                event=request.event.slug,
                code=order.code,
            )

        try:
            # Get the payment provider
            providers = request.event.get_payment_providers()
            provider = providers.get("postfinance")

            if not provider:
                messages.error(
                    request,
                    str(_("PostFinance payment provider not found."))
                )
                return redirect(
                    "control:event.order",
                    organizer=request.event.organizer.slug,
                    event=request.event.slug,
                    code=order.code,
                )

            client = provider._get_client()

            # Attempt to charge the token
            from postfinancecheckout.models import LineItemCreate, LineItemType
            line_item = LineItemCreate(
                name=(
                    f"Installment {installment.installment_number} "
                    f"of {installment.num_installments}"
                ),
                quantity=1,
                amountIncludingTax=float(installment.amount),
                type=LineItemType.PRODUCT,
                uniqueId=f"installment-{installment.pk}",
            )

            merchant_reference = f"pretix-installment-{order.code}-{installment.installment_number}"

            # Charge the token
            transaction = client.charge_token(
                token_id=int(installment.token_id),
                amount=float(installment.amount),
                currency=request.event.currency,
                merchant_reference=merchant_reference,
                line_items=[line_item],
            )

            if not transaction or not transaction.id:
                messages.error(
                    request,
                    str(_("Failed to create transaction for installment retry."))
                )
                return redirect(
                    "control:event.order",
                    organizer=request.event.organizer.slug,
                    event=request.event.slug,
                    code=order.code,
                )

            transaction_state = transaction.state
            success_states = {
                "AUTHORIZED",
                "COMPLETED",
                "FULFILL",
                "CONFIRMED",
                "PROCESSING",
            }
            if transaction_state and transaction_state.value in success_states:
                # Payment successful - update installment status
                installment.status = InstallmentSchedule.Status.PAID
                installment.paid_at = now()
                installment.failure_reason = None
                installment.grace_period_ends = None
                installment.save()

                # Create OrderPayment for this installment
                from pretix.base.models import OrderPayment
                order_payment = OrderPayment(
                    order=order,
                    amount=installment.amount,
                    provider="postfinance",
                    payment_date=now(),
                    state=OrderPayment.PAYMENT_STATE_CONFIRMED,
                    info_data={
                        "transaction_id": transaction.id,
                        "state": transaction_state.value if transaction_state else None,
                        "installment_number": installment.installment_number,
                        "installment_id": installment.pk,
                    },
                )
                order_payment.save()

                # Link the payment to the installment
                installment.payment = order_payment
                installment.save()

                # Log the action
                user = (
                    getattr(request.user, "email", None)
                    or getattr(request.user, "username", None)
                    or str(request.user.pk)
                )
                order.log_action(
                    "pretix_postfinance.installment.retry.success",
                    data={
                        "installment_number": installment.installment_number,
                        "amount": str(installment.amount),
                        "transaction_id": transaction.id,
                        "user": user,
                    },
                )

                messages.success(
                    request,
                    str(
                        _(
                            "Installment {number} payment retried successfully. "
                            "Transaction ID: {transaction_id}"
                        ).format(
                            number=installment.installment_number,
                            transaction_id=transaction.id,
                        )
                    ),
                )

                # Send success email to customer
                self._send_installment_success_email(order, installment, transaction)

            else:
                # Payment failed - update failure reason
                failure_reason = str(_("Manual retry failed"))
                if transaction_state:
                    failure_reason = f"{failure_reason}: {transaction_state.value}"

                installment.failure_reason = failure_reason
                installment.save()

                # Log the failure
                user = (
                    getattr(request.user, "email", None)
                    or getattr(request.user, "username", None)
                    or str(request.user.pk)
                )
                order.log_action(
                    "pretix_postfinance.installment.retry.failed",
                    data={
                        "installment_number": installment.installment_number,
                        "amount": str(installment.amount),
                        "failure_reason": failure_reason,
                        "user": user,
                    },
                )

                messages.error(
                    request,
                    str(
                        _(
                            "Installment {number} payment retry failed: {reason}"
                        ).format(
                            number=installment.installment_number,
                            reason=failure_reason,
                        )
                    ),
                )

        except PostFinanceError as e:
            logger.exception("PostFinance API error during installment retry: %s", e)
            messages.error(
                request,
                str(
                    _("Payment service error during installment retry: {error}")
                ).format(error=str(e)),
            )
        except Exception as e:
            logger.exception("Unexpected error during installment retry: %s", e)
            messages.error(
                request,
                str(_("An unexpected error occurred during installment retry.")),
            )

        return redirect(
            "control:event.order",
            organizer=request.event.organizer.slug,
            event=request.event.slug,
            code=order.code,
        )

    def _send_installment_success_email(
        self, order: Order, installment: InstallmentSchedule, transaction: Any
    ) -> None:
        """
        Send success email to customer about installment payment.
        """
        try:
            event = order.event
            subject = f"Installment Payment Successful - {event.name}"

            message = f"""Dear Customer,

Your installment payment for order {order.code} has been successfully processed.

Event: {event.name}
Order: {order.code}
Installment: {installment.installment_number} of {installment.num_installments}
Amount: {installment.amount} {event.currency}
Date: {now().strftime('%Y-%m-%d %H:%M:%S')}

Thank you for your payment.

Best regards,
{event.organizer.name}
"""

            send_mail(
                subject,
                message,
                f"noreply@{event.organizer.slug}.pretix.example.com",
                [order.email],
                fail_silently=True,
            )

            logger.info(
                "Sent installment success email for order %s, installment %s",
                order.code,
                installment.installment_number,
            )

        except Exception as e:
            logger.error(
                "Failed to send installment success email for order %s: %s",
                order.code,
                e,
            )


class PostFinanceUpdatePaymentMethodView(EventPermissionRequiredMixin, View):
    """
    Handle customer requests to update their payment method for future installments.

    This view creates a token update transaction with PostFinance and redirects
    the customer to update their payment method. After they return, it updates
    the token on all pending installments.
    """

    permission = "can_view_orders"

    def get(self, request: PretixHttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        """
        Create a token update transaction and redirect customer to PostFinance.
        """
        order = get_object_or_404(Order, code=kwargs["order"], event=request.event)

        # Check if this order has installments
        installments = InstallmentSchedule.objects.filter(order=order)
        if not installments.exists():
            messages.error(
                request,
                str(_("This order does not have installment payments."))
            )
            return redirect(
                "presale:event.order",
                organizer=request.event.organizer.slug,
                event=request.event.slug,
                code=order.code,
            )

        # Check if there are any pending installments that need a token
        pending_installments = installments.filter(
            status__in=[InstallmentSchedule.Status.SCHEDULED, InstallmentSchedule.Status.PENDING]
        )
        if not pending_installments.exists():
            messages.info(
                request,
                str(_("All installments have been processed. No payment method update needed."))
            )
            return redirect(
                "presale:event.order",
                organizer=request.event.organizer.slug,
                event=request.event.slug,
                code=order.code,
            )

        try:
            # Get the payment provider
            providers = request.event.get_payment_providers()
            provider = providers.get("postfinance")

            if not provider:
                messages.error(
                    request,
                    str(_("PostFinance payment provider not found."))
                )
                return redirect(
                    "presale:event.order",
                    organizer=request.event.organizer.slug,
                    event=request.event.slug,
                    code=order.code,
                )

            # Create a token update transaction
            client = provider._get_client()

            # Create a minimal line item with amount 0 for token update
            from postfinancecheckout.models import LineItemCreate, LineItemType
            line_item = LineItemCreate(
                name="Payment Method Update",
                quantity=1,
                amountIncludingTax=0.0,
                type=LineItemType.PRODUCT,
                uniqueId="payment-method-update",
            )

            # Create merchant reference for token update transaction
            merchant_reference = f"pretix-update-token-{order.code}"

            # Create transaction for token update
            from postfinancecheckout.models import TokenizationMode
            transaction = client.create_transaction(
                currency=request.event.currency,
                line_items=[line_item],
                success_url=build_absolute_uri(
                    request.event,
                    "plugins:pretix_postfinance:postfinance.update_payment_method_return",
                    kwargs={"order": order.code},
                ),
                failed_url=build_absolute_uri(
                    request.event,
                    "presale:event.order",
                    kwargs={
                        "organizer": request.event.organizer.slug,
                        "event": request.event.slug,
                        "code": order.code,
                    },
                ),
                merchant_reference=merchant_reference,
                # Force token creation for the update
                tokenization_mode=TokenizationMode.FORCE_CREATION,
            )

            if not transaction.id:
                messages.error(
                    request,
                    str(_("Failed to create token update transaction."))
                )
                return redirect(
                    "presale:event.order",
                    organizer=request.event.organizer.slug,
                    event=request.event.slug,
                    code=order.code,
                )

            # Store transaction ID in session for the return handler
            request.session["payment_postfinance_token_update_transaction_id"] = transaction.id
            request.session["payment_postfinance_token_update_order_code"] = order.code

            # Get payment page URL
            payment_page_url = client.get_payment_page_url(transaction.id)
            if not payment_page_url:
                messages.error(
                    request,
                    str(_("Failed to get payment page URL."))
                )
                return redirect(
                    "presale:event.order",
                    organizer=request.event.organizer.slug,
                    event=request.event.slug,
                    code=order.code,
                )

            logger.info(
                "Created token update transaction %s for order %s",
                transaction.id,
                order.code,
            )

            return redirect(payment_page_url)

        except PostFinanceError as e:
            logger.exception("PostFinance API error during token update: %s", e)
            messages.error(
                request,
                str(_("Payment service error. Please try again later."))
            )
            return redirect(
                "presale:event.order",
                organizer=request.event.organizer.slug,
                event=request.event.slug,
                code=order.code,
            )
        except Exception as e:
            logger.exception("Unexpected error during token update: %s", e)
            messages.error(
                request,
                str(_("An unexpected error occurred. Please try again."))
            )
            return redirect(
                "presale:event.order",
                organizer=request.event.organizer.slug,
                event=request.event.slug,
                code=order.code,
            )

    def post(self, request: PretixHttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        """
        Handle the return from PostFinance after token update.
        """
        order = get_object_or_404(Order, code=kwargs["order"], event=request.event)

        # Get transaction ID from session
        transaction_id = request.session.pop(
            "payment_postfinance_token_update_transaction_id", None
        )
        if not transaction_id:
            messages.error(
                request,
                str(_("Token update session expired. Please try again."))
            )
            return redirect(
                "presale:event.order",
                organizer=request.event.organizer.slug,
                event=request.event.slug,
                code=order.code,
            )

        try:
            # Get the payment provider
            providers = request.event.get_payment_providers()
            provider = providers.get("postfinance")

            if not provider:
                messages.error(
                    request,
                    str(_("PostFinance payment provider not found."))
                )
                return redirect(
                    "presale:event.order",
                    organizer=request.event.organizer.slug,
                    event=request.event.slug,
                    code=order.code,
                )

            client = provider._get_client()

            # Fetch the transaction to get the new token
            transaction = client.get_transaction(int(transaction_id))

            if not transaction.token or not transaction.token.id:
                messages.error(
                    request,
                    str(_("Failed to retrieve new payment token."))
                )
                return redirect(
                    "presale:event.order",
                    organizer=request.event.organizer.slug,
                    event=request.event.slug,
                    code=order.code,
                )

            new_token_id = str(transaction.token.id)

            # Update all pending installments with the new token
            pending_installments = InstallmentSchedule.objects.filter(
                order=order,
                status__in=[
                    InstallmentSchedule.Status.SCHEDULED,
                    InstallmentSchedule.Status.PENDING,
                ],
            )

            updated_count = pending_installments.update(token_id=new_token_id)

            logger.info(
                "Updated %s pending installments with new token %s for order %s",
                updated_count,
                new_token_id,
                order.code,
            )

            # Send confirmation email
            self._send_token_update_confirmation(order, new_token_id)

            messages.success(
                request,
                str(
                    _(
                        "Payment method updated successfully. "
                        "Future installments will use your new payment method."
                    )
                ),
            )

            return redirect(
                "presale:event.order",
                organizer=request.event.organizer.slug,
                event=request.event.slug,
                code=order.code,
            )

        except PostFinanceError as e:
            logger.exception("PostFinance API error during token update return: %s", e)
            messages.error(
                request,
                str(_("Payment service error. Please try again later."))
            )
            return redirect(
                "presale:event.order",
                organizer=request.event.organizer.slug,
                event=request.event.slug,
                code=order.code,
            )
        except Exception as e:
            logger.exception("Unexpected error during token update return: %s", e)
            messages.error(
                request,
                str(_("An unexpected error occurred. Please try again."))
            )
            return redirect(
                "presale:event.order",
                organizer=request.event.organizer.slug,
                event=request.event.slug,
                code=order.code,
            )

    def _send_token_update_confirmation(self, order: Order, new_token_id: str) -> None:
        """
        Send confirmation email to customer about payment method update.
        """
        try:
            event = order.event
            subject = f"Payment Method Updated - {event.name}"

            message = f"""Dear Customer,

Your payment method for order {order.code} has been successfully updated.

Event: {event.name}
Order: {order.code}

All future installment payments will be charged to your new payment method.

If you did not initiate this change, please contact our support team immediately.

Thank you,
{event.organizer.name}
"""

            send_mail(
                subject,
                message,
                f"noreply@{event.organizer.slug}.pretix.example.com",
                [order.email],
                fail_silently=True,
            )

            logger.info(
                "Sent token update confirmation email for order %s",
                order.code,
            )

        except Exception as e:
            logger.error(
                "Failed to send token update confirmation for order %s: %s",
                order.code,
                e,
            )
