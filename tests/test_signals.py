from __future__ import annotations

import pytest
from django.urls import Resolver404

from pretix_postfinance.signals import control_html_head


class FakeRequest:
    def __init__(self, path_info: str) -> None:
        self.path_info = path_info


@pytest.mark.django_db
def test_script_loads_on_the_provider_settings_page(event):
    """
    The script wires up the "Test connection" and "Setup webhooks" buttons,
    which only this page renders.
    """
    path = (
        f"/control/event/{event.organizer.slug}/{event.slug}"
        "/settings/payment/postfinance"
    )
    assert "pretix-postfinance.js" in control_html_head(None, FakeRequest(path))


@pytest.mark.django_db
def test_script_does_not_load_on_other_settings_pages(event):
    """
    Matching every control URL whose name merely contains "settings" served
    the script on pages with nothing to put it to use.
    """
    base = f"/control/event/{event.organizer.slug}/{event.slug}"
    for path in (
        f"{base}/settings/",
        f"{base}/settings/plugins",
        f"{base}/settings/payment",
        f"{base}/settings/tickets",
    ):
        assert control_html_head(None, FakeRequest(path)) == "", path


@pytest.mark.django_db
def test_script_does_not_load_for_another_provider(event):
    """
    Another plugin's provider settings page is not ours to decorate — and the
    production space provider renders no buttons of its own either.
    """
    base = f"/control/event/{event.organizer.slug}/{event.slug}/settings/payment"
    assert control_html_head(None, FakeRequest(f"{base}/banktransfer")) == ""
    assert control_html_head(None, FakeRequest(f"{base}/postfinance_prod")) == ""


@pytest.mark.django_db
def test_an_unresolvable_path_is_not_an_error(event):
    """`resolve()` raises on a path no URLconf claims; that is not our crash."""
    try:
        assert control_html_head(None, FakeRequest("/no/such/page/ever")) == ""
    except Resolver404:  # pragma: no cover - the regression this guards
        pytest.fail("control_html_head must not propagate Resolver404")
