from collections import OrderedDict
from typing import Any, Tuple

from django import forms
from django.utils.translation import gettext_lazy as _

from pretix.base.payment import BasePaymentProvider

from .api import PostFinanceClient, PostFinanceError


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
            space_info = client.get_space()
            space_name = space_info.get("name", str(_("Unknown")))
            return (
                True,
                str(
                    _("Connection successful! Connected to space: {space_name}").format(
                        space_name=space_name
                    )
                ),
            )
        except PostFinanceError as e:
            if e.response is not None and e.response.status_code == 401:
                return (
                    False,
                    str(
                        _(
                            "Authentication failed. Please check your User ID and "
                            "API Secret."
                        )
                    ),
                )
            elif e.response is not None and e.response.status_code == 404:
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
