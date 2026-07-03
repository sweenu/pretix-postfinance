from __future__ import annotations

import json
from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from postfinancecheckout.models import TransactionState
from pretix.base.models import Order, OrderPayment, OrderRefund
from pretix.base.payment import PaymentException

from pretix_postfinance.payment import (
    PostFinanceCHFPaymentProvider,
    PostFinancePaymentProvider,
)
from pretix_postfinance.views import _process_transaction_webhook


@pytest.fixture
def chf_event(event):
    event.settings.set("payment_postfinance_chf__enabled", True)
    event.settings.set("payment_postfinance_chf_chf_rate", "0.93")
    return event


@pytest.fixture
def chf_provider(chf_event):
    return PostFinanceCHFPaymentProvider(chf_event)


# Registration and configuration


@pytest.mark.django_db
def test_both_providers_registered(chf_event):
    providers = chf_event.get_payment_providers()
    assert "postfinance" in providers
    assert "postfinance_chf" in providers
    assert isinstance(providers["postfinance_chf"], PostFinanceCHFPaymentProvider)


@pytest.mark.django_db
def test_credentials_shared_with_main_provider(chf_provider):
    # The CHF provider has no credential settings of its own; it reads the
    # main provider's, which the event fixture configures.
    assert chf_provider._get_credentials_for_mode("live") == (
        "12345",
        "67890",
        "test-secret",
    )


@pytest.mark.django_db
def test_settings_form_has_rate_but_no_credentials(chf_event, monkeypatch):
    monkeypatch.setattr(
        "pretix_postfinance.payment.PostFinancePaymentProvider._get_payment_method_choices",
        lambda self: [],
    )
    fields = PostFinanceCHFPaymentProvider(chf_event).settings_form_fields
    assert "chf_rate" in fields
    for key in (
        "space_id",
        "user_id",
        "auth_key",
        "test_space_id",
        "test_user_id",
        "test_auth_key",
    ):
        assert key not in fields


@pytest.mark.django_db
def test_conversion_rounds_half_up(chf_provider):
    assert chf_provider._convert(Decimal("13.37"), Decimal("0.93")) == Decimal("12.43")
    assert chf_provider._convert(Decimal("10.00"), Decimal("0.955")) == Decimal("9.55")
    # 1.005 rounds up, not to even
    assert chf_provider._convert(Decimal("1.00"), Decimal("1.005")) == Decimal("1.01")


@pytest.mark.django_db
def test_is_allowed_requires_rate(chf_event, rf, monkeypatch):
    # The base checks (availability window, totals, sales channel) are the
    # parent's business; here we only exercise the CHF-specific guards.
    monkeypatch.setattr(
        "pretix_postfinance.payment.PostFinancePaymentProvider.is_allowed",
        lambda self, request, total=None: True,
    )
    provider = PostFinanceCHFPaymentProvider(chf_event)
    req = rf.get("/")
    req.event = chf_event
    req.session = {}
    assert provider.is_allowed(req, total=Decimal("10.00"))

    chf_event.settings.delete("payment_postfinance_chf_chf_rate")
    provider = PostFinanceCHFPaymentProvider(chf_event)
    assert not provider.is_allowed(req, total=Decimal("10.00"))


@pytest.mark.django_db
def test_is_allowed_rejects_chf_events(chf_event, rf, monkeypatch):
    monkeypatch.setattr(
        "pretix_postfinance.payment.PostFinancePaymentProvider.is_allowed",
        lambda self, request, total=None: True,
    )
    chf_event.currency = "CHF"
    chf_event.save()
    provider = PostFinanceCHFPaymentProvider(chf_event)
    req = rf.get("/")
    req.event = chf_event
    req.session = {}
    assert not provider.is_allowed(req, total=Decimal("10.00"))


# Transaction creation


@pytest.mark.django_db
def test_transaction_created_in_chf_with_converted_amount(chf_event, order, rf, monkeypatch):
    captured = {}

    def fake_create_transaction(self, **kwargs):
        captured.update(kwargs)
        transaction = MagicMock()
        transaction.id = 999888
        return transaction

    monkeypatch.setattr(
        "pretix_postfinance.payment.PostFinanceClient.create_transaction",
        fake_create_transaction,
    )
    monkeypatch.setattr(
        "pretix_postfinance.payment.PostFinanceClient.get_payment_page_url",
        lambda self, tid: "https://checkout.postfinance.ch/pay/999888",
    )

    provider = PostFinanceCHFPaymentProvider(chf_event)
    payment = order.payments.create(
        provider="postfinance_chf",
        amount=order.total,  # 13.37 EUR
        state=OrderPayment.PAYMENT_STATE_CREATED,
    )
    req = rf.post("/")
    req.session = {}

    result = provider.execute_payment(req, payment)

    assert result == "https://checkout.postfinance.ch/pay/999888"
    assert captured["currency"] == "CHF"
    line_items = captured["line_items"]
    assert len(line_items) == 1
    assert line_items[0].amount_including_tax == pytest.approx(12.43)  # 13.37 * 0.93

    payment.refresh_from_db()
    assert payment.info_data["pending_transaction_id"] == 999888
    assert payment.info_data["fx_rate"] == "0.93"
    assert payment.info_data["fx_base_currency"] == "EUR"
    assert payment.info_data["charged_currency"] == "CHF"
    assert payment.info_data["charged_amount"] == "12.43"


@pytest.mark.django_db
def test_execute_payment_confirm_preserves_fx_snapshot(
    chf_event, order, rf, monkeypatch, transaction_factory
):
    monkeypatch.setattr(
        "pretix_postfinance.payment.PostFinanceClient.get_transaction",
        lambda self, tid: transaction_factory(state=TransactionState.FULFILL),
    )

    provider = PostFinanceCHFPaymentProvider(chf_event)
    payment = order.payments.create(
        provider="postfinance_chf",
        amount=order.total,
        state=OrderPayment.PAYMENT_STATE_CREATED,
        info=json.dumps(
            {
                "pending_transaction_id": 123456,
                "fx_rate": "0.93",
                "fx_base_currency": "EUR",
                "charged_currency": "CHF",
                "charged_amount": "12.43",
            }
        ),
    )
    req = rf.post("/")
    req.session = {}

    provider.execute_payment(req, payment)

    order.refresh_from_db()
    payment.refresh_from_db()
    assert order.status == Order.STATUS_PAID
    assert payment.info_data["transaction_id"] == 123456
    assert "pending_transaction_id" not in payment.info_data
    # The FX snapshot must survive confirmation for later refunds
    assert payment.info_data["fx_rate"] == "0.93"
    assert payment.info_data["charged_amount"] == "12.43"


@pytest.mark.django_db
def test_session_keys_are_scoped_per_provider(chf_event, order, rf):
    # A transaction prepared by the EUR provider must never be reused by
    # the CHF provider, and vice versa.
    eur_provider = PostFinancePaymentProvider(chf_event)
    chf_provider = PostFinanceCHFPaymentProvider(chf_event)
    assert (
        eur_provider._session_transaction_id_key
        != chf_provider._session_transaction_id_key
    )

    req = rf.post("/")
    req.session = {}
    payment = order.payments.create(provider="postfinance_chf", amount=order.total)
    eur_provider._set_session_transaction_id(req, payment, 111)
    assert chf_provider._get_prepared_transaction_id(req, payment) is None


# Refunds


def _paid_chf_payment(order, amount=None, fx_rate="0.93"):
    info = {
        "transaction_id": 123456,
        "state": TransactionState.COMPLETED.value,
        "fx_base_currency": "EUR",
        "charged_currency": "CHF",
    }
    if fx_rate is not None:
        info["fx_rate"] = fx_rate
    return order.payments.create(
        provider="postfinance_chf",
        amount=amount or order.total,
        state=OrderPayment.PAYMENT_STATE_CONFIRMED,
        info=json.dumps(info),
    )


@pytest.mark.django_db
def test_full_refund_lets_postfinance_compute_amount(chf_event, order, monkeypatch, refund_factory):
    captured = {}

    def fake_refund_transaction(self, **kwargs):
        captured.update(kwargs)
        return refund_factory()

    monkeypatch.setattr(
        "pretix_postfinance.payment.PostFinanceClient.refund_transaction",
        fake_refund_transaction,
    )

    provider = PostFinanceCHFPaymentProvider(chf_event)
    payment = _paid_chf_payment(order)
    refund = order.refunds.create(
        provider="postfinance_chf",
        amount=order.total,
        payment=payment,
    )

    provider.execute_refund(refund)

    # Full refunds pass no amount so PostFinance refunds the exact
    # remaining CHF, immune to conversion rounding.
    assert captured["amount"] is None
    refund.refresh_from_db()
    assert refund.state == OrderRefund.REFUND_STATE_TRANSIT


@pytest.mark.django_db
def test_partial_refund_amount_is_converted(chf_event, order, monkeypatch, refund_factory):
    captured = {}

    def fake_refund_transaction(self, **kwargs):
        captured.update(kwargs)
        return refund_factory()

    monkeypatch.setattr(
        "pretix_postfinance.payment.PostFinanceClient.refund_transaction",
        fake_refund_transaction,
    )

    provider = PostFinanceCHFPaymentProvider(chf_event)
    payment = _paid_chf_payment(order)
    refund = order.refunds.create(
        provider="postfinance_chf",
        amount=Decimal("5.00"),
        payment=payment,
    )

    provider.execute_refund(refund)

    assert captured["amount"] == Decimal("4.65")  # 5.00 EUR * 0.93


@pytest.mark.django_db
def test_final_partial_refund_of_remainder_is_full_refund(chf_event, order):
    provider = PostFinanceCHFPaymentProvider(chf_event)
    payment = _paid_chf_payment(order)
    order.refunds.create(
        provider="postfinance_chf",
        amount=Decimal("5.00"),
        payment=payment,
        state=OrderRefund.REFUND_STATE_DONE,
    )
    remainder = order.refunds.create(
        provider="postfinance_chf",
        amount=order.total - Decimal("5.00"),
        payment=payment,
    )

    assert provider._refund_transaction_amount(remainder) is None


@pytest.mark.django_db
def test_partial_refund_without_stored_rate_raises(chf_event, order):
    provider = PostFinanceCHFPaymentProvider(chf_event)
    payment = _paid_chf_payment(order, fx_rate=None)
    refund = order.refunds.create(
        provider="postfinance_chf",
        amount=Decimal("5.00"),
        payment=payment,
    )

    with pytest.raises(PaymentException):
        provider._refund_transaction_amount(refund)


@pytest.mark.django_db
def test_partial_refund_uses_stored_rate_not_current_setting(chf_event, order):
    provider = PostFinanceCHFPaymentProvider(chf_event)
    payment = _paid_chf_payment(order, fx_rate="0.90")
    chf_event.settings.set("payment_postfinance_chf_chf_rate", "1.10")
    refund = order.refunds.create(
        provider="postfinance_chf",
        amount=Decimal("5.00"),
        payment=payment,
    )

    assert provider._refund_transaction_amount(refund) == Decimal("4.50")


# Webhooks


@pytest.mark.django_db
def test_webhook_confirms_chf_payment(chf_event, order, monkeypatch, transaction_factory):
    monkeypatch.setattr(
        "pretix_postfinance.views.PostFinanceClient.get_transaction",
        lambda self, tid: transaction_factory(state=TransactionState.FULFILL),
    )

    payment = order.payments.create(
        provider="postfinance_chf",
        amount=order.total,
        state=OrderPayment.PAYMENT_STATE_PENDING,
        info=json.dumps(
            {
                "transaction_id": 123456,
                "fx_rate": "0.93",
                "charged_currency": "CHF",
            }
        ),
    )

    status, processed = _process_transaction_webhook(123456, "12345")

    assert status == "ok"
    assert processed is True
    payment.refresh_from_db()
    order.refresh_from_db()
    assert payment.state == OrderPayment.PAYMENT_STATE_CONFIRMED
    assert order.status == Order.STATUS_PAID
    # FX snapshot survives the webhook's info update
    assert payment.info_data["fx_rate"] == "0.93"


# Customer-facing rendering


@pytest.mark.django_db
def test_payment_form_render_shows_conversion(chf_event, rf):
    provider = PostFinanceCHFPaymentProvider(chf_event)
    req = rf.get("/")
    req.event = chf_event
    req.session = {}

    html = provider.payment_form_render(req, Decimal("13.37"))

    assert "12.43" in html
    assert "0.93" in html


@pytest.mark.django_db
def test_checkout_confirm_render_mentions_rate(chf_event, rf):
    provider = PostFinanceCHFPaymentProvider(chf_event)
    req = rf.get("/")
    req.event = chf_event
    req.session = {}

    html = provider.checkout_confirm_render(req)

    assert "CHF" in html
    assert "0.93" in html
