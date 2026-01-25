from __future__ import annotations

from typing import Any, ClassVar

from django import forms
from django.dispatch import receiver
from django.template.loader import get_template
from django.urls import resolve
from django.utils.functional import Promise
from django.utils.translation import gettext_lazy as _
from pretix.base.signals import register_payment_providers
from pretix.control.signals import html_head, order_search_forms


@receiver(register_payment_providers, dispatch_uid="payment_postfinance")
def register_payment_provider(sender: Any, **kwargs: Any) -> type[Any]:
    """
    Register the PostFinance payment provider with pretix.
    """
    from .payment import PostFinancePaymentProvider

    return PostFinancePaymentProvider


class InstallmentStatusFilterForm(forms.Form):
    """
    Filter form for installment payment status.

    This form allows organizers to filter orders by their installment payment status.
    """
    INSTALLMENT_STATUS_CHOICES: ClassVar[list[tuple[str, Promise]]] = [
        ("", _("All")),
        ("fully_paid", _("Fully Paid")),
        ("partially_paid", _("Partially Paid (in progress)")),
        ("payment_failed", _("Payment Failed")),
    ]

    installment_status = forms.ChoiceField(
        label=_("Installment Status"),
        choices=INSTALLMENT_STATUS_CHOICES,
        required=False,
        help_text=_("Filter orders by installment payment status"),
    )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)

    def filter_qs(self, queryset: Any) -> Any:
        """
        Filter the queryset based on the selected installment status.

        Args:
            queryset: The Order queryset to filter

        Returns:
            Filtered queryset
        """
        from .models import InstallmentSchedule

        status = self.cleaned_data.get("installment_status")
        if not status:
            return queryset

        # Only apply installment filtering if the installments feature is enabled
        # for this event (check if any orders have installment schedules)
        if not InstallmentSchedule.objects.filter(
            order__in=queryset
        ).exists():
            return queryset

        if status == "fully_paid":
            # Fully paid: All installments are paid, no failed installments
            return queryset.filter(
                installment_schedule__status=InstallmentSchedule.Status.PAID
            ).distinct()

        elif status == "partially_paid":
            # Partially paid: At least one paid installment and at least one scheduled installment
            return queryset.filter(
                installment_schedule__status__in=[
                    InstallmentSchedule.Status.PAID,
                    InstallmentSchedule.Status.SCHEDULED
                ]
            ).distinct()

        elif status == "payment_failed":
            # Payment failed: At least one failed installment
            return queryset.filter(
                installment_schedule__status=InstallmentSchedule.Status.FAILED
            ).distinct()

        return queryset

    def filter_to_strings(self) -> list[str]:
        """
        Return a list of strings describing the currently active filters.

        Returns:
            List of filter description strings
        """
        status = self.cleaned_data.get("installment_status")
        if not status:
            return []

        # Get the display name for the selected status
        for choice_id, choice_label in self.INSTALLMENT_STATUS_CHOICES:
            if choice_id == status:
                return [str(_("Installment Status: {status}")).format(status=choice_label)]

        return []


@receiver(html_head, dispatch_uid="postfinance_control_html_head")
def control_html_head(sender: Any, request: Any, **kwargs: Any) -> str:
    """
    Inject PostFinance JavaScript into control panel pages.
    """
    url = resolve(request.path_info)
    # Only load on payment settings page
    if url.url_name and "settings" in url.url_name:
        template = get_template("pretixplugins/postfinance/control_head.html")
        return template.render()
    return ""


@receiver(order_search_forms, dispatch_uid="postfinance_installment_status_filter")
def register_installment_status_filter(sender: Any, **kwargs: Any) -> list[forms.Form]:
    """
    Register the installment status filter form for order search.

    This signal is called when building the order search filter forms.
    We only register the filter if the installments feature is enabled
    for this event.

    Args:
        sender: The event object
        **kwargs: Additional arguments

    Returns:
        List of filter forms to add
    """
    from .models import InstallmentSchedule

    # Check if installments feature is enabled for this event
    # We check if there are any installment schedules for orders in this event
    if InstallmentSchedule.objects.filter(
        order__event=sender
    ).exists():
        form = InstallmentStatusFilterForm()
        form.prefix = "installment_status"
        return [form]

    return []
