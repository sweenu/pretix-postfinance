from __future__ import annotations

import json
import logging
from collections import OrderedDict
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from django import forms
from django.contrib import messages
from django.http import HttpRequest
from django.template.loader import get_template
from django.urls import reverse
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _
from postfinancecheckout.models import (
    LineItemCreate,
    LineItemType,
    TransactionCompletionBehavior,
    TransactionState,
)
from pretix.base.forms import SecretKeySettingsField
from pretix.base.models import OrderPayment, OrderRefund
from pretix.base.payment import BasePaymentProvider, PaymentException
from pretix.multidomain.urlreverse import build_absolute_uri

from .api import PostFinanceClient, PostFinanceError

if TYPE_CHECKING:
    from pretix.base.models import Order

logger = logging.getLogger(__name__)


# PostFinance transaction states that indicate successful payment
SUCCESS_STATES = {
    TransactionState.AUTHORIZED,
    TransactionState.COMPLETED,
    TransactionState.FULFILL,
    TransactionState.CONFIRMED,
    TransactionState.PROCESSING,
}

# PostFinance transaction states that indicate failed payment
FAILURE_STATES = {
    TransactionState.FAILED,
    TransactionState.DECLINE,
    TransactionState.VOIDED,
}

# Mapping of HTTP status codes to user-friendly error messages
ERROR_STATUS_MESSAGES = {
    400: _("Bad request. The payment data may be invalid."),
    401: _("Authentication failed. Check your User ID and API Secret in settings."),
    403: _("Access denied. Your API credentials may lack required permissions."),
    404: _("Resource not found. The transaction or space ID may be invalid."),
    409: _("Conflict. The transaction may have already been processed."),
    422: _("Invalid request. Check the payment amount and currency."),
    429: _("Rate limited. Too many requests to PostFinance API."),
    500: _("PostFinance server error. Please try again later."),
    502: _("PostFinance gateway error. Please try again later."),
    503: _("PostFinance service unavailable. Please try again later."),
}


class PostFinancePaymentProvider(BasePaymentProvider):
    """
    PostFinance Checkout payment provider for pretix.

    Enables Swiss payment methods including Card, E-Finance, and TWINT
    through the PostFinance Checkout API.
    """

    identifier = "postfinance"
    verbose_name = "PostFinance"
    abort_pending_allowed = False
    execute_payment_needs_user = True

    @property
    def public_name(self) -> str:
        """
        Return the name shown to customers during checkout.

        If a custom display name is configured in event settings, use that.
        Otherwise fall back to the default verbose name.
        """
        return str(self.settings.get("public_name")) or self.verbose_name

    @property
    def settings_form_fields(self) -> OrderedDict:
        """
        Return the form fields for the payment provider settings.

        These will be displayed in the event's payment settings.
        """
        d = OrderedDict(
            list(super().settings_form_fields.items())
            + [
                (
                    "space_id",
                    forms.CharField(
                        label=_("Space ID"),
                        help_text=_(
                            "Your PostFinance Checkout space ID. "
                            "You can find it next to your space name in your "
                            "PostFinance Checkout account."
                        ),
                        required=True,
                    ),
                ),
                (
                    "user_id",
                    forms.CharField(
                        label=_("User ID"),
                        help_text=_(
                            "Your PostFinance Checkout application user ID. "
                            "Create an application user in your PostFinance Checkout account "
                            "under Account > Users > Application Users."
                        ),
                        required=True,
                    ),
                ),
                (
                    "auth_key",
                    SecretKeySettingsField(
                        label=_("Authentication key"),
                        help_text=_(
                            "The authentication key for your application user. "
                            "This is shown only once when creating the application user."
                        ),
                        required=True,
                    ),
                ),
                (
                    "public_name",
                    forms.CharField(
                        label=_("Display Name"),
                        help_text=_(
                            "Custom name shown to customers during checkout. "
                            "Leave empty to use the default name 'PostFinance'."
                        ),
                        required=False,
                    ),
                ),
                (
                    "description",
                    forms.CharField(
                        label=_("Description"),
                        help_text=_(
                            "Custom description shown on the checkout page. "
                            "Leave empty to use the default message."
                        ),
                        widget=forms.Textarea(attrs={"rows": 3}),
                        required=False,
                    ),
                ),
                (
                    "capture_mode",
                    forms.ChoiceField(
                        label=_("Capture Mode"),
                        help_text=_(
                            "Choose when to capture (complete) payments. "
                            "'Immediate' captures automatically after authorization. "
                            "'Manual' keeps payments in authorized state until you "
                            "capture them manually."
                        ),
                        choices=[
                            ("immediate", _("Immediate (Recommended)")),
                            ("manual", _("Manual")),
                        ],
                        initial="immediate",
                        required=True,
                    ),
                ),
                (
                    "allowed_payment_methods",
                    forms.CharField(
                        label=_("Allowed Payment Methods"),
                        help_text=_(
                            "Restrict which payment methods are available to customers. "
                            "Enter comma-separated payment method configuration IDs from "
                            "your PostFinance space (e.g., '1234,5678'). "
                            "Leave empty to allow all payment methods."
                        ),
                        required=False,
                    ),
                ),
            ]
        )
        return d

    def settings_content_render(self, request: HttpRequest) -> str:
        """
        Render additional content below the settings form.

        Adds a "Test Connection" button that validates the configured
        PostFinance credentials via AJAX.
        """
        test_url = reverse(
            "plugins:pretix_postfinance:postfinance.test_connection",
            kwargs={
                "organizer": self.event.organizer.slug,
                "event": self.event.slug,
            },
        )
        template = get_template("postfinance/settings_test_connection_button.html")
        ctx = {
            "label": _("Connection Test"),
            "button_text": _("Test Connection"),
            "testing_text": _("Testing..."),
            "test_url": test_url,
            "csrf_token": request.META.get("CSRF_COOKIE", ""),
            "error_text": _("Connection test failed. Please try again."),
        }
        return template.render(ctx)

    def _get_client(self) -> PostFinanceClient:
        """
        Create and return a PostFinance API client using the configured settings.
        """
        return PostFinanceClient(
            space_id=int(self.settings.get("space_id", 0)),
            user_id=int(self.settings.get("user_id", 0)),
            api_secret=str(self.settings.get("api_secret", "")),
        )

    def test_connection(self) -> tuple[bool, str]:
        """
        Test the connection to PostFinance API using configured credentials.

        Returns:
            A tuple of (success: bool, message: str).
        """
        space_id = self.settings.get("space_id")
        user_id = self.settings.get("user_id")
        api_secret = self.settings.get("api_secret")

        if not all([space_id, user_id, api_secret]):
            return (
                False,
                str(
                    _(
                        "Please configure Space ID, User ID, and API Secret before "
                        "testing the connection."
                    )
                ),
            )

        try:
            client = self._get_client()
            space = client.get_space()
            space_name = space.name if space.name else str(_("Unknown"))
            return (
                True,
                str(
                    _("Connection successful! Connected to space: {space_name}").format(
                        space_name=space_name
                    )
                ),
            )
        except PostFinanceError as e:
            if e.status_code == 401:
                return (
                    False,
                    str(_("Authentication failed. Please check your User ID and API Secret.")),
                )
            elif e.status_code == 404:
                return (
                    False,
                    str(_("Space not found. Please check your Space ID.")),
                )
            return (False, str(_("Connection failed: {error}").format(error=str(e))))
        except Exception as e:
            return (False, str(_("Unexpected error: {error}").format(error=str(e))))

    def payment_is_valid_session(self, request: HttpRequest) -> bool:
        """
        Check if the user session contains valid payment information.

        For PostFinance, we need a transaction ID in the session that was
        created during checkout_prepare.
        """
        return request.session.get("payment_postfinance_transaction_id") is not None

    def _build_line_items(self, cart: dict[str, Any], currency: str) -> list[LineItemCreate]:
        """
        Build PostFinance line items from pretix cart.

        Creates detailed line items for each cart position and fee.
        """
        line_items: list[LineItemCreate] = []

        # Add individual items from grouped positions
        positions = cart.get("positions", [])
        for idx, position in enumerate(positions):
            # Get item name, including variation if applicable
            item_name = str(position.item.name)
            if hasattr(position, "variation") and position.variation:
                item_name = f"{item_name} - {position.variation.value}"

            # Get quantity (grouped positions have a count attribute)
            quantity = getattr(position, "count", 1)

            # Get the total price for this position (includes quantity)
            price = getattr(position, "total", getattr(position, "price", Decimal("0")))

            line_items.append(
                LineItemCreate(
                    name=item_name,
                    quantity=float(quantity),
                    amountIncludingTax=float(price),
                    type=LineItemType.PRODUCT,
                    uniqueId=f"position-{idx}-{position.item.pk}",
                )
            )

        # Add fees (surcharges, taxes, etc.)
        fees = cart.get("fees", [])
        for idx, fee in enumerate(fees):
            fee_value = getattr(fee, "value", Decimal("0"))
            if fee_value == Decimal("0"):
                continue

            # Get fee description
            fee_name = str(_("Fee"))
            if hasattr(fee, "get_fee_type_display"):
                fee_name = str(fee.get_fee_type_display())
            elif hasattr(fee, "fee_type"):
                fee_name = str(fee.fee_type)

            line_items.append(
                LineItemCreate(
                    name=fee_name,
                    quantity=1,
                    amountIncludingTax=float(fee_value),
                    type=LineItemType.FEE,
                    uniqueId=f"fee-{idx}",
                )
            )

        # Fallback: if no positions were found, use total as single line item
        if not line_items:
            total = cart.get("total", Decimal("0"))
            line_items.append(
                LineItemCreate(
                    name=str(_("Order Total")),
                    quantity=1,
                    amountIncludingTax=float(total),
                    type=LineItemType.PRODUCT,
                    uniqueId="order-total",
                )
            )

        return line_items

    def checkout_prepare(self, request: HttpRequest, cart: dict[str, Any]) -> bool | str:
        """
        Prepare the checkout for payment.

        Creates a PostFinance transaction and returns the payment page URL
        to redirect the customer to PostFinance for payment.
        """
        try:
            client = self._get_client()
            currency = self.event.currency

            line_items = self._build_line_items(cart, currency)

            success_url = build_absolute_uri(
                self.event,
                "presale:event.checkout",
                kwargs={"step": "confirm"},
            )
            failed_url = build_absolute_uri(
                self.event,
                "presale:event.checkout",
                kwargs={"step": "payment"},
            )

            merchant_reference = f"pretix-{self.event.slug}"

            # Determine completion behavior based on capture mode setting
            capture_mode = self.settings.get("capture_mode", "immediate")
            if capture_mode == "manual":
                completion_behavior = TransactionCompletionBehavior.COMPLETE_DEFERRED
            else:
                completion_behavior = TransactionCompletionBehavior.COMPLETE_IMMEDIATELY

            # Parse allowed payment method configurations
            allowed_payment_methods: list[int] | None = None
            allowed_methods_str = self.settings.get("allowed_payment_methods", "")
            if allowed_methods_str:
                try:
                    allowed_payment_methods = [
                        int(x.strip()) for x in str(allowed_methods_str).split(",") if x.strip()
                    ]
                except ValueError:
                    logger.warning(
                        "Invalid allowed_payment_methods setting: %s",
                        allowed_methods_str,
                    )

            transaction = client.create_transaction(
                currency=currency,
                line_items=line_items,
                success_url=success_url,
                failed_url=failed_url,
                merchant_reference=merchant_reference,
                completion_behavior=completion_behavior,
                allowed_payment_method_configurations=allowed_payment_methods,
            )

            transaction_id = transaction.id
            if not transaction_id:
                logger.error("PostFinance transaction missing ID: %s", transaction)
                messages.error(
                    request,
                    str(_("Failed to create payment. Please try again.")),
                )
                return False

            request.session["payment_postfinance_transaction_id"] = transaction_id
            logger.info(
                "Created PostFinance transaction %s for event %s",
                transaction_id,
                self.event.slug,
            )

            payment_page_url = client.get_payment_page_url(transaction_id)
            if not payment_page_url:
                logger.error(
                    "Failed to get payment page URL for transaction %s",
                    transaction_id,
                )
                messages.error(
                    request,
                    str(_("Failed to redirect to payment page. Please try again.")),
                )
                return False

            return payment_page_url

        except PostFinanceError as e:
            logger.exception("PostFinance API error during checkout_prepare: %s", e)
            messages.error(
                request,
                str(_("Payment service error. Please try again later.")),
            )
            return False
        except Exception as e:
            logger.exception("Unexpected error during checkout_prepare: %s", e)
            messages.error(
                request,
                str(_("An unexpected error occurred. Please try again.")),
            )
            return False

    def checkout_confirm_render(
        self, request: HttpRequest, order: Order | None = None, info_data: dict | None = None
    ) -> str:
        """
        Render the payment confirmation page content.

        This is displayed to the customer before they confirm their order
        to summarize what will happen during payment.
        """
        custom_description = self.settings.get("description")
        if custom_description:
            return str(custom_description)
        return str(
            _(
                "You will be redirected to PostFinance to complete your payment. "
                "After completing the payment, you will be returned to this site."
            )
        )

    def execute_payment(self, request: HttpRequest, payment: OrderPayment) -> str | None:
        """
        Execute the payment after the order is confirmed.

        Retrieves the transaction details from PostFinance, checks the
        transaction state, and confirms or fails the payment accordingly.
        """
        transaction_id = request.session.get("payment_postfinance_transaction_id")

        if not transaction_id:
            logger.warning(
                "No PostFinance transaction ID in session for payment %s",
                payment.pk,
            )
            payment.info_data = {"error": "No transaction ID in session"}
            payment.save(update_fields=["info"])
            return None

        try:
            client = self._get_client()
            transaction = client.get_transaction(transaction_id)

            payment_method = None
            if transaction.payment_connector_configuration:
                payment_method = transaction.payment_connector_configuration.name

            state = transaction.state
            payment.info_data = {
                "transaction_id": transaction_id,
                "state": state.value if state else None,
                "payment_method": payment_method,
                "created_on": str(transaction.created_on) if transaction.created_on else None,
            }
            payment.save(update_fields=["info"])

            logger.info(
                "PostFinance transaction %s has state %s for payment %s",
                transaction_id,
                state,
                payment.pk,
            )

            if state in SUCCESS_STATES:
                try:
                    payment.confirm()
                    logger.info(
                        "Payment %s confirmed (PostFinance state: %s)",
                        payment.pk,
                        state,
                    )
                except Exception as e:
                    logger.exception(
                        "Error confirming payment %s: %s",
                        payment.pk,
                        e,
                    )
                    raise PaymentException(
                        str(
                            _("Payment was successful but order confirmation failed: {error}")
                        ).format(error=str(e))
                    ) from e
            elif state in FAILURE_STATES:
                payment.fail(info={"state": state.value if state else None})
                logger.info(
                    "Payment %s failed (PostFinance state: %s)",
                    payment.pk,
                    state,
                )
            else:
                logger.info(
                    "Payment %s is pending (PostFinance state: %s)",
                    payment.pk,
                    state,
                )

            # Clean up session
            if "payment_postfinance_transaction_id" in request.session:
                del request.session["payment_postfinance_transaction_id"]

        except PostFinanceError as e:
            logger.exception("PostFinance API error during execute_payment: %s", e)
            payment.info_data = {
                "transaction_id": transaction_id,
                "error": str(e),
                "error_code": e.error_code,
                "error_status_code": e.status_code,
            }
            payment.save(update_fields=["info"])
            raise PaymentException(
                str(_("Payment processing failed: {error}")).format(error=str(e))
            ) from e

        except Exception as e:
            logger.exception("Unexpected error during execute_payment: %s", e)
            payment.info_data = {
                "transaction_id": transaction_id,
                "error": str(e),
                "error_code": type(e).__name__,
            }
            payment.save(update_fields=["info"])
            raise PaymentException(
                str(_("An unexpected error occurred: {error}")).format(error=str(e))
            ) from e

        return None

    def payment_pending_render(self, request: HttpRequest, payment: OrderPayment) -> str:
        """
        Render customer-facing instructions on how to proceed with a pending payment.
        """
        info_data = payment.info_data or {}
        transaction_id = info_data.get("transaction_id")
        state = info_data.get("state")

        parts = []

        parts.append(f"<p><strong>{_('Your payment is being processed.')}</strong></p>")

        if state == TransactionState.PENDING.value:
            msg = _(
                "Your payment has been initiated and is waiting for confirmation "
                "from your bank or payment provider."
            )
            parts.append(f"<p>{msg}</p>")
        elif state == TransactionState.PROCESSING.value:
            msg = _("Your payment is currently being processed. This usually takes a few moments.")
            parts.append(f"<p>{msg}</p>")
        else:
            msg = _(
                "Your payment is being verified. You will receive a confirmation email "
                "once completed."
            )
            parts.append(f"<p>{msg}</p>")

        if transaction_id:
            parts.append(f"<p>{_('Transaction reference')}: <code>{transaction_id}</code></p>")

        parts.append(
            "<div style='margin-top: 15px; padding: 10px; "
            "background-color: #f8f9fa; border-left: 3px solid #007bff;'>"
            f"<strong>{_('What happens next?')}</strong>"
            "<ul style='margin: 10px 0 0 0; padding-left: 20px;'>"
            f"<li>{_('Most payments are confirmed within a few minutes')}</li>"
            f"<li>{_('You will receive an email confirmation once your payment is complete')}</li>"
            f"<li>{_('You can refresh this page to check for updates')}</li>"
            "</ul>"
            "</div>"
        )

        return "".join(parts)

    def payment_control_render(self, request: HttpRequest, payment: OrderPayment) -> str:
        """
        Render payment control HTML for the admin order view.

        Displays PostFinance transaction details and action buttons.
        """
        info_data = payment.info_data or {}
        transaction_id = info_data.get("transaction_id")
        state = info_data.get("state")
        payment_method = info_data.get("payment_method")

        parts: list[str] = []

        if transaction_id:
            # Build link to PostFinance dashboard
            space_id = self.settings.get("space_id")
            if space_id:
                dashboard_url = (
                    f"https://checkout.postfinance.ch/s/{space_id}"
                    f"/payment/transaction/view/{transaction_id}"
                )
                parts.append(
                    format_html(
                        '<strong>{label}:</strong> <a href="{url}" target="_blank" '
                        'rel="noopener noreferrer">{value}</a>',
                        label=_("Transaction ID"),
                        url=dashboard_url,
                        value=transaction_id,
                    )
                )
            else:
                parts.append(
                    format_html(
                        "<strong>{label}:</strong> {value}",
                        label=_("Transaction ID"),
                        value=transaction_id,
                    )
                )

        if state:
            parts.append(
                format_html(
                    "<strong>{label}:</strong> {value}",
                    label=_("State"),
                    value=state,
                )
            )

        if payment_method:
            parts.append(
                format_html(
                    "<strong>{label}:</strong> {value}",
                    label=_("Payment Method"),
                    value=payment_method,
                )
            )

        # Show error details if any
        error_message = info_data.get("error")
        if error_message:
            error_code = info_data.get("error_code")
            error_status = info_data.get("error_status_code")

            error_parts = [str(error_message)]
            if error_code:
                error_parts.append(f"Code: {error_code}")
            if error_status:
                error_parts.append(f"HTTP {error_status}")

            parts.append(
                format_html(
                    '<strong style="color: #c00;">{label}:</strong> '
                    '<span style="color: #c00;">{value}</span>',
                    label=_("Error"),
                    value=" | ".join(error_parts),
                )
            )

            # Add actionable suggestion based on HTTP status code
            if error_status and int(error_status) in ERROR_STATUS_MESSAGES:
                suggestion = ERROR_STATUS_MESSAGES[int(error_status)]
                parts.append(
                    format_html(
                        '<span style="color: #666; font-style: italic;">{suggestion}</span>',
                        suggestion=suggestion,
                    )
                )

        # Show capture button for AUTHORIZED transactions (manual capture mode)
        # Void is handled via pretix's cancel_payment mechanism
        if state == TransactionState.AUTHORIZED.value:
            capture_url = reverse(
                "plugins:pretix_postfinance:postfinance.capture",
                kwargs={
                    "organizer": self.event.organizer.slug,
                    "event": self.event.slug,
                    "order": payment.order.code,
                    "payment": payment.pk,
                },
            )
            parts.append(
                format_html(
                    '<form action="{capture_url}" method="POST" '
                    'style="margin-top: 10px; display: inline-block;">'
                    '<input type="hidden" name="csrfmiddlewaretoken" value="{csrf}">'
                    '<button type="submit" class="btn btn-primary btn-sm">'
                    "{capture_text}"
                    "</button>"
                    "</form>",
                    capture_url=capture_url,
                    csrf=request.META.get("CSRF_COOKIE", ""),
                    capture_text=_("Capture Payment"),
                )
            )

        # Refunds are handled via pretix's built-in refund UI since we implement
        # payment_refund_supported() and payment_partial_refund_supported()

        if parts:
            return "<br>".join(parts)
        return ""

    def payment_presale_render(self, payment: OrderPayment) -> str:
        """
        Return a short description of the payment for customer view.
        """
        info_data = payment.info_data or {}
        payment_method = info_data.get("payment_method")
        if payment_method:
            return f"{self.public_name} ({payment_method})"
        return str(self.public_name)

    def payment_control_render_short(self, payment: OrderPayment) -> str:
        """
        Return a very short version of the payment method for admin actions.
        """
        info_data = payment.info_data or {}
        transaction_id = info_data.get("transaction_id")
        if transaction_id:
            return f"PostFinance ({transaction_id})"
        return "PostFinance"

    def payment_refund_supported(self, payment: OrderPayment) -> bool:
        """
        Check if automatic refunding is supported for this payment.
        """
        info_data = payment.info_data or {}
        state = info_data.get("state")
        refundable_states = {
            TransactionState.COMPLETED.value,
            TransactionState.FULFILL.value,
        }
        return state in refundable_states

    def payment_partial_refund_supported(self, payment: OrderPayment) -> bool:
        """
        Check if automatic partial refunding is supported for this payment.
        """
        return self.payment_refund_supported(payment)

    def execute_refund(self, refund: OrderRefund) -> None:
        """
        Execute a refund for an order.

        This is called by pretix when a refund needs to be processed.
        """
        payment = refund.payment
        info_data = payment.info_data or {}
        transaction_id = info_data.get("transaction_id")

        if not transaction_id:
            raise PaymentException(_("Transaction ID not found."))

        # Check if transaction is in a refundable state
        current_state = info_data.get("state")
        refundable_states = {
            TransactionState.COMPLETED.value,
            TransactionState.FULFILL.value,
        }
        if current_state not in refundable_states:
            raise PaymentException(
                _("Transaction cannot be refunded. Current state: {state}").format(
                    state=current_state or "Unknown"
                )
            )

        try:
            client = self._get_client()

            # Generate a unique external ID for idempotency
            external_id = f"pretix-refund-{refund.order.code}-R-{refund.local_id}"
            merchant_reference = f"pretix-{self.event.slug}-{refund.order.code}-R-{refund.local_id}"

            postfinance_refund = client.refund_transaction(
                transaction_id=int(transaction_id),
                external_id=external_id,
                merchant_reference=merchant_reference,
                amount=float(refund.amount),
            )

            # Store refund info on the OrderRefund object
            refund.info = json.dumps(
                {
                    "refund_id": postfinance_refund.id,
                    "state": postfinance_refund.state.value if postfinance_refund.state else None,
                    "amount": float(postfinance_refund.amount)
                    if postfinance_refund.amount
                    else None,
                    "created_on": str(postfinance_refund.created_on)
                    if postfinance_refund.created_on
                    else None,
                }
            )
            refund.save(update_fields=["info"])

            # Mark refund as done
            refund.done()

            logger.info(
                "PostFinance refund %s created for payment %s (amount %s)",
                postfinance_refund.id,
                payment.pk,
                refund.amount,
            )

        except PostFinanceError as e:
            logger.exception(
                "PostFinance API error refunding transaction %s: %s",
                transaction_id,
                e,
            )
            raise PaymentException(_("Refund failed: {error}").format(error=str(e))) from e

    def api_payment_details(self, payment: OrderPayment) -> dict:
        """
        Return payment details for the REST API.
        """
        info_data = payment.info_data or {}
        return {
            "transaction_id": info_data.get("transaction_id"),
            "state": info_data.get("state"),
            "payment_method": info_data.get("payment_method"),
            "created_on": info_data.get("created_on"),
        }

    def matching_id(self, payment: OrderPayment) -> str | None:
        """
        Return the transaction ID for matching with external records.
        """
        info_data = payment.info_data or {}
        return info_data.get("transaction_id")

    def refund_matching_id(self, refund: OrderRefund) -> str | None:
        """
        Return the refund ID for matching with external records.
        """
        info_data = refund.info_data or {}
        refund_id = info_data.get("refund_id")
        return str(refund_id) if refund_id else None

    def shred_payment_info(self, obj: OrderPayment | OrderRefund) -> None:
        """
        Remove personal data from payment/refund info when requested.
        """
        if not isinstance(obj, (OrderPayment, OrderRefund)):
            return

        # Keep transaction/refund IDs for reference, but remove other details
        if isinstance(obj, OrderPayment):
            info_data = obj.info_data or {}
            obj.info_data = {
                "transaction_id": info_data.get("transaction_id"),
                "state": info_data.get("state"),
                "_shredded": True,
            }
            obj.save(update_fields=["info"])
        else:
            # For refunds, clear the info
            obj.info_data = {"_shredded": True}
            obj.save(update_fields=["info"])

    def cancel_payment(self, payment: OrderPayment) -> None:
        """
        Cancel a payment.

        For PostFinance, if the transaction is in AUTHORIZED state, we void it.
        For other states, we use the default cancellation behavior.
        """
        self.execute_void(payment)

        # Call parent implementation to set payment state to canceled
        super().cancel_payment(payment)

    # Helper methods for custom views (capture, void, refund)

    def execute_capture(self, payment: OrderPayment) -> tuple[bool, str | None]:
        """
        Capture (complete) an authorized transaction.
        """
        info_data = payment.info_data or {}
        transaction_id = info_data.get("transaction_id")

        if not transaction_id:
            return (False, str(_("Transaction ID not found.")))

        # Check if transaction is in AUTHORIZED state
        current_state = info_data.get("state")
        if current_state != TransactionState.AUTHORIZED.value:
            return (
                False,
                str(
                    _("Transaction cannot be captured. Current state: {state}").format(
                        state=current_state or "Unknown"
                    )
                ),
            )

        try:
            client = self._get_client()
            completion = client.complete_transaction(int(transaction_id))

            # Update payment info with completion details
            info_data["state"] = TransactionState.COMPLETED.value
            if completion.id:
                info_data["completion_id"] = completion.id
            payment.info_data = info_data
            payment.save(update_fields=["info"])

            logger.info(
                "PostFinance transaction %s captured successfully for payment %s",
                transaction_id,
                payment.pk,
            )

            # Confirm the payment in pretix
            try:
                payment.confirm()
                logger.info("Payment %s confirmed after capture", payment.pk)
            except Exception as e:
                logger.exception(
                    "Error confirming payment %s after capture: %s",
                    payment.pk,
                    e,
                )

            return (True, None)

        except PostFinanceError as e:
            logger.exception(
                "PostFinance API error capturing transaction %s: %s",
                transaction_id,
                e,
            )
            return (False, str(_("Capture failed: {error}").format(error=str(e))))
        except Exception as e:
            logger.exception(
                "Unexpected error capturing transaction %s: %s",
                transaction_id,
                e,
            )
            return (False, str(_("Unexpected error: {error}").format(error=str(e))))

    def execute_void(self, payment: OrderPayment) -> tuple[bool, str | None]:
        """
        Void an authorized transaction.
        """
        info_data = payment.info_data or {}
        transaction_id = info_data.get("transaction_id")

        if not transaction_id:
            return (False, str(_("Transaction ID not found.")))

        # Check if transaction is in AUTHORIZED state
        current_state = info_data.get("state")
        if current_state != TransactionState.AUTHORIZED.value:
            return (
                False,
                str(
                    _("Transaction cannot be voided. Current state: {state}").format(
                        state=current_state or "Unknown"
                    )
                ),
            )

        try:
            client = self._get_client()
            void_result = client.void_transaction(int(transaction_id))

            # Update payment info with void details
            info_data["state"] = TransactionState.VOIDED.value
            if void_result.id:
                info_data["void_id"] = void_result.id
            payment.info_data = info_data
            payment.save(update_fields=["info"])

            logger.info(
                "PostFinance transaction %s voided successfully for payment %s",
                transaction_id,
                payment.pk,
            )

            # Fail the payment in pretix (void means the payment won't be captured)
            try:
                payment.fail(info={"state": TransactionState.VOIDED.value})
                logger.info("Payment %s marked as failed after void", payment.pk)
            except Exception as e:
                logger.exception(
                    "Error failing payment %s after void: %s",
                    payment.pk,
                    e,
                )

            return (True, None)

        except PostFinanceError as e:
            logger.exception(
                "PostFinance API error voiding transaction %s: %s",
                transaction_id,
                e,
            )
            return (False, str(_("Void failed: {error}").format(error=str(e))))
        except Exception as e:
            logger.exception(
                "Unexpected error voiding transaction %s: %s",
                transaction_id,
                e,
            )
            return (False, str(_("Unexpected error: {error}").format(error=str(e))))
