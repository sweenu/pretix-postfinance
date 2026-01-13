import logging
from collections import OrderedDict
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple, Union

from django import forms
from django.contrib import messages
from django.http import HttpRequest
from django.urls import reverse
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _
from postfinancecheckout.models import (
    LineItemCreate,
    LineItemType,
    TransactionCompletionBehavior,
    TransactionState,
)
from pretix.base.models import OrderPayment
from pretix.base.payment import BasePaymentProvider
from pretix.multidomain.urlreverse import build_absolute_uri

from .api import PostFinanceClient, PostFinanceError

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


class PostFinancePaymentProvider(BasePaymentProvider):
    """
    PostFinance Checkout payment provider for pretix.

    Enables Swiss payment methods including Card, E-Finance, and TWINT
    through the PostFinance Checkout API.
    """

    identifier = "postfinance"
    verbose_name = _("PostFinance")

    @property
    def public_name(self) -> str:
        """
        Return the name shown to customers during checkout.

        If a custom display name is configured in event settings, use that.
        Otherwise fall back to the default verbose name.
        """
        custom_name = self.settings.get("display_name")
        if custom_name:
            return str(custom_name)
        return str(_("PostFinance"))

    def checkout_confirm_render(self, request, order=None) -> str:
        """
        Render the payment confirmation page content.

        This is displayed to the customer before they confirm their order
        to summarize what will happen during payment.

        If a custom description is configured in event settings, use that.
        Otherwise fall back to the default message.
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

    @property
    def settings_form_fields(self) -> "OrderedDict[str, forms.Field]":
        """
        Return the form fields for the payment provider settings.

        These will be displayed in the event's payment settings.
        """
        d: OrderedDict[str, Any] = OrderedDict(
            list(super().settings_form_fields.items())
            + [
                (
                    "space_id",
                    forms.CharField(
                        label=_("Space ID"),
                        help_text=_(
                            "Your PostFinance Checkout space ID. "
                            "You can find this in your PostFinance Checkout account "
                            "under Space > General Settings."
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
                    "api_secret",
                    forms.CharField(
                        label=_("API Secret"),
                        help_text=_(
                            "The API secret (authentication key) for your application user. "
                            "This is shown only once when creating the application user."
                        ),
                        required=True,
                        widget=forms.PasswordInput(
                            render_value=True,
                            attrs={"autocomplete": "new-password"},
                        ),
                    ),
                ),
                (
                    "environment",
                    forms.ChoiceField(
                        label=_("Environment"),
                        help_text=_(
                            "Select 'Sandbox' for testing or 'Production' for live payments. "
                            "Use sandbox credentials when testing."
                        ),
                        choices=[
                            ("sandbox", _("Sandbox (Testing)")),
                            ("production", _("Production (Live)")),
                        ],
                        initial="sandbox",
                        required=True,
                    ),
                ),
                (
                    "display_name",
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
            ]
        )
        return d

    def _get_client(self) -> PostFinanceClient:
        """
        Create and return a PostFinance API client using the configured settings.

        Returns:
            A configured PostFinanceClient instance.
        """
        return PostFinanceClient(
            space_id=int(self.settings.get("space_id", 0)),
            user_id=int(self.settings.get("user_id", 0)),
            api_secret=str(self.settings.get("api_secret", "")),
            environment=self.settings.get("environment", "sandbox"),
        )

    def test_connection(self) -> Tuple[bool, str]:
        """
        Test the connection to PostFinance API using configured credentials.

        Verifies that the credentials are valid by fetching the space details.

        Returns:
            A tuple of (success: bool, message: str).
            On success, message contains the space name.
            On failure, message contains the error description.
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
                    str(
                        _(
                            "Authentication failed. Please check your User ID and "
                            "API Secret."
                        )
                    ),
                )
            elif e.status_code == 404:
                return (
                    False,
                    str(
                        _(
                            "Space not found. Please check your Space ID."
                        )
                    ),
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

    def _build_line_items(
        self, cart: Dict[str, Any], currency: str
    ) -> List[LineItemCreate]:
        """
        Build PostFinance line items from pretix cart.

        Args:
            cart: The pretix cart dictionary with 'total' and 'positions'.
            currency: The currency code.

        Returns:
            List of LineItemCreate objects for PostFinance API.
        """
        line_items: List[LineItemCreate] = []
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

    def checkout_prepare(
        self, request: HttpRequest, cart: Dict[str, Any]
    ) -> Union[bool, str]:
        """
        Prepare the checkout for payment.

        Creates a PostFinance transaction and returns the payment page URL
        to redirect the customer to PostFinance for payment.

        Args:
            request: The HTTP request object.
            cart: The cart dictionary containing items and total.

        Returns:
            The PostFinance payment page URL to redirect to, or False on error.
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

            transaction = client.create_transaction(
                currency=currency,
                line_items=line_items,
                success_url=success_url,
                failed_url=failed_url,
                merchant_reference=merchant_reference,
                completion_behavior=completion_behavior,
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

    def execute_payment(
        self, request: HttpRequest, payment: OrderPayment
    ) -> Union[str, None]:
        """
        Execute the payment after the order is confirmed.

        Retrieves the transaction details from PostFinance, checks the
        transaction state, and confirms or fails the payment accordingly.

        Args:
            request: The HTTP request object.
            payment: The OrderPayment object.

        Returns:
            None on success, or a URL to redirect to.
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

            # Handle different transaction states
            if state in SUCCESS_STATES:
                try:
                    payment.confirm()
                    logger.info(
                        "Payment %s confirmed (PostFinance state: %s)",
                        payment.pk,
                        state,
                    )
                except Exception as e:
                    # Log but don't fail - payment was successful even if
                    # confirmation has issues (e.g., quota exceeded)
                    logger.exception(
                        "Error confirming payment %s: %s",
                        payment.pk,
                        e,
                    )
            elif state in FAILURE_STATES:
                payment.fail(info={"state": state.value if state else None})
                logger.info(
                    "Payment %s failed (PostFinance state: %s)",
                    payment.pk,
                    state,
                )
            else:
                # Transaction is still pending (CREATE, PENDING, etc.)
                logger.info(
                    "Payment %s is pending (PostFinance state: %s)",
                    payment.pk,
                    state,
                )

            # Clean up session
            del request.session["payment_postfinance_transaction_id"]

        except PostFinanceError as e:
            logger.exception(
                "PostFinance API error during execute_payment: %s", e
            )
            payment.info_data = {
                "transaction_id": transaction_id,
                "error": str(e),
            }
            payment.save(update_fields=["info"])

        except Exception as e:
            logger.exception("Unexpected error during execute_payment: %s", e)
            payment.info_data = {
                "transaction_id": transaction_id,
                "error": str(e),
            }
            payment.save(update_fields=["info"])

        return None

    def payment_control_render(
        self, request: HttpRequest, payment: OrderPayment
    ) -> str:
        """
        Render payment control HTML for the admin order view.

        Displays PostFinance transaction details and, for AUTHORIZED payments,
        provides a capture button to manually complete the transaction.

        Args:
            request: The HTTP request object.
            payment: The OrderPayment object.

        Returns:
            HTML string to display in the admin panel.
        """
        info_data = payment.info_data or {}
        transaction_id = info_data.get("transaction_id")
        state = info_data.get("state")
        payment_method = info_data.get("payment_method")

        parts: List[str] = []

        if transaction_id:
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

        # Show capture button for AUTHORIZED transactions
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
                    '<form action="{url}" method="POST" style="margin-top: 10px;">'
                    '<input type="hidden" name="csrfmiddlewaretoken" value="{csrf}">'
                    '<button type="submit" class="btn btn-primary btn-sm">'
                    '{button_text}'
                    '</button>'
                    '</form>',
                    url=capture_url,
                    csrf=request.META.get("CSRF_COOKIE", ""),
                    button_text=_("Capture Payment"),
                )
            )

        if parts:
            return "<br>".join(parts)
        return ""

    def execute_capture(
        self, payment: OrderPayment
    ) -> Tuple[bool, Optional[str]]:
        """
        Capture (complete) an authorized transaction.

        This method is called from the capture view to manually complete
        a transaction that is in the AUTHORIZED state.

        Args:
            payment: The OrderPayment to capture.

        Returns:
            A tuple of (success: bool, error_message: Optional[str]).
            On success, error_message is None.
            On failure, error_message contains the error description.
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
                logger.info(
                    "Payment %s confirmed after capture",
                    payment.pk,
                )
            except Exception as e:
                # Log but don't fail - capture was successful even if
                # confirmation has issues (e.g., quota exceeded)
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
