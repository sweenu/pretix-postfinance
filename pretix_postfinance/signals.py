from __future__ import annotations

from typing import Any

from django.dispatch import receiver
from pretix.base.signals import register_payment_providers


@receiver(register_payment_providers, dispatch_uid="payment_postfinance")
def register_payment_provider(sender: Any, **kwargs: Any) -> type[Any]:
    """
    Register the PostFinance payment provider with pretix.
    """
    from .payment import PostFinancePaymentProvider

    return PostFinancePaymentProvider
