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
    def settings_form_fields(self) -> dict:
        """
        Return the form fields for the payment provider settings.

        These will be displayed in the event's payment settings.
        """
        return {}

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
