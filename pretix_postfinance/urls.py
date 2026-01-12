"""
URL configuration for PostFinance payment plugin.
"""

from django.urls import re_path

from pretix.multidomain import event_url

from . import views

event_patterns = [
    event_url(
        r"^postfinance/return/(?P<order>[^/]+)/(?P<payment>\d+)/(?P<hash>[^/]+)/$",
        views.PostFinanceReturnView.as_view(),
        name="postfinance.return",
    ),
]
