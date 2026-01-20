from __future__ import annotations

from typing import Any

from django.dispatch import receiver
from django.template.loader import get_template
from django.urls import resolve
from pretix.base.signals import register_payment_providers
from pretix.control.signals import html_head


@receiver(register_payment_providers, dispatch_uid="payment_postfinance")
def register_payment_provider(sender: Any, **kwargs: Any) -> type[Any]:
    """
    Register the PostFinance payment provider with pretix.
    """
    from .payment import PostFinancePaymentProvider

    return PostFinancePaymentProvider


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
