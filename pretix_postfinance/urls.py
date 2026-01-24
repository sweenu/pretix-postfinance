"""
URL configuration for PostFinance payment plugin.
"""

from typing import Any

from django.urls import path, re_path

from . import views

# Customer-facing event patterns for installment payment updates
event_patterns: list[Any] = [
    path(
        "postfinance/update-payment-method/<str:order>/",
        views.PostFinanceUpdatePaymentMethodView.as_view(),
        name="postfinance.update_payment_method",
    ),
    path(
        "postfinance/update-payment-method-return/<str:order>/",
        views.PostFinanceUpdatePaymentMethodView.as_view(),
        name="postfinance.update_payment_method_return",
    ),
]

urlpatterns = [
    path("_postfinance/webhook/", views.webhook, name="postfinance.webhook"),
    re_path(
        r"^control/event/(?P<organizer>[^/]+)/(?P<event>[^/]+)/postfinance/test-connection/$",
        views.PostFinanceTestConnectionView.as_view(),
        name="postfinance.test_connection",
    ),
    re_path(
        r"^control/event/(?P<organizer>[^/]+)/(?P<event>[^/]+)/postfinance/setup-webhooks/$",
        views.PostFinanceSetupWebhooksView.as_view(),
        name="postfinance.setup_webhooks",
    ),
    re_path(
        r"^control/event/(?P<organizer>[^/]+)/(?P<event>[^/]+)/postfinance/capture/"
        r"(?P<order>[^/]+)/(?P<payment>\d+)/$",
        views.PostFinanceCaptureView.as_view(),
        name="postfinance.capture",
    ),
    re_path(
        r"^control/event/(?P<organizer>[^/]+)/(?P<event>[^/]+)/postfinance/retry-installment/"
        r"(?P<order>[^/]+)/(?P<installment>\d+)/$",
        views.PostFinanceRetryInstallmentView.as_view(),
        name="postfinance.retry_installment",
    ),
]
