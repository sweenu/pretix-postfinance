"""
URL configuration for PostFinance payment plugin.
"""

from django.urls import path, re_path
from pretix.multidomain import event_url

from . import views

# Event-specific URL patterns (include organizer and event in the path)
event_patterns = [
    event_url(
        r"^postfinance/return/(?P<order>[^/]+)/(?P<payment>\d+)/(?P<hash>[^/]+)/$",
        views.PostFinanceReturnView.as_view(),
        name="postfinance.return",
    ),
]

# Global URL patterns (including control panel admin views)
# Registered under the plugins:pretix_postfinance: namespace
urlpatterns = [
    path(
        "_postfinance/webhook/",
        views.PostFinanceWebhookView.as_view(),
        name="postfinance.webhook",
    ),
    # Control panel capture view (admin-only)
    # URL: /control/event/<organizer>/<event>/postfinance/capture/<order>/<payment>/
    re_path(
        r"^control/event/(?P<organizer>[^/]+)/(?P<event>[^/]+)/postfinance/capture/"
        r"(?P<order>[^/]+)/(?P<payment>\d+)/$",
        views.PostFinanceCaptureView.as_view(),
        name="postfinance.capture",
    ),
]
