"""
URL configuration for PostFinance payment plugin.
"""

from django.urls import path
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

# Global URL patterns (not event-specific)
urlpatterns = [
    path(
        "_postfinance/webhook/",
        views.PostFinanceWebhookView.as_view(),
        name="postfinance.webhook",
    ),
]
