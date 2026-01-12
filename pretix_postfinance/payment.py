from collections import OrderedDict
from typing import Any

from django import forms
from django.utils.translation import gettext_lazy as _

from pretix.base.payment import BasePaymentProvider


class PostFinancePaymentProvider(BasePaymentProvider):
    """
    PostFinance Checkout payment provider for pretix.

    Enables Swiss payment methods including Card, E-Finance, and TWINT
    through the PostFinance Checkout API.
    """

    identifier = "postfinance"
    verbose_name = _("PostFinance")
    public_name = _("PostFinance")

    def checkout_confirm_render(self, request, order=None) -> str:
        """
        Render the payment confirmation page content.

        This is displayed to the customer before they confirm their order
        to summarize what will happen during payment.
        """
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
            ]
        )
        return d

    def payment_is_valid_session(self, request) -> bool:
        """
        Check if the user session contains valid payment information.
        """
        return True

    def checkout_prepare(self, request, cart):
        """
        Prepare the checkout for payment.

        Called when the user proceeds to the payment step.
        """
        return True

    def execute_payment(self, request, payment):
        """
        Execute the payment.

        Called when the order is confirmed.
        """
        pass
