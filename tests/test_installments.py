"""
Tests for PostFinance installment payments.

These exercise the provider against stand-in plan and installment objects
rather than pretix's models, so they also run on an installation without
installment support. ``test_installments_integration.py`` covers the same
code against the real models.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from postfinancecheckout.models import ChargeState, TransactionState

from pretix_postfinance.api import PostFinanceError
from pretix_postfinance.payment import PostFinancePaymentProvider


@pytest.fixture
def chf_event(event):
    event.currency = "CHF"
    event.save()
    return event


@pytest.fixture
def alt_currency_event(chf_event):
    chf_event.settings.set("payment_postfinance_alt_currency", "EUR")
    chf_event.settings.set("payment_postfinance_alt_currency_rate", "1.07")
    return chf_event


def make_plan(event, order, *, token=None, total_installments=3):
    """A stand-in for ``InstallmentPlan`` with the fields the provider reads."""
    plan = SimpleNamespace(
        pk=42,
        order=order,
        total_installments=total_installments,
        payment_token=token if token is not None else {},
        payment_provider="postfinance",
    )
    plan.store_payment_token = lambda data: setattr(plan, "payment_token", data)
    return plan


def make_installment(plan, *, number=2, amount="100.00"):
    """A stand-in for ``ScheduledInstallment``."""
    installment = SimpleNamespace(
        pk=number,
        plan=plan,
        installment_number=number,
        amount=Decimal(amount),
        failure_reason=None,
        saved_fields=[],
    )
    installment.save = lambda update_fields=None: installment.saved_fields.append(
        tuple(update_fields or ())
    )
    return installment


@pytest.fixture
def charge_calls(monkeypatch):
    """Capture the transaction the provider creates for a token charge."""
    calls: dict[str, object] = {}

    def create_transaction(self, **kwargs):
        calls["create"] = kwargs
        calls["space_id"] = self.space_id
        return SimpleNamespace(id=234567, state=TransactionState.PENDING)

    monkeypatch.setattr(
        "pretix_postfinance.payment.PostFinanceClient.create_transaction",
        create_transaction,
    )
    return calls


def successful_charge(monkeypatch):
    monkeypatch.setattr(
        "pretix_postfinance.payment.PostFinanceClient.process_with_token",
        lambda self, tid: SimpleNamespace(state=ChargeState.SUCCESSFUL, failure_reason=None),
    )


STORED_TOKEN = {
    "token_id": 999888,
    "customer_id": "cus_test123",
    "customer_email": "customer@example.org",
    "space_id": "12345",
}


@pytest.mark.django_db
def test_installments_supported_when_pretix_provides_them(event):
    prov = PostFinancePaymentProvider(event)
    pytest.importorskip("pretix.efcc.models")
    assert prov.installments_supported is True


@pytest.mark.django_db
def test_execute_installment_charges_event_currency(
    chf_event, order, monkeypatch, charge_calls
):
    successful_charge(monkeypatch)
    plan = make_plan(chf_event, order, token=dict(STORED_TOKEN))
    installment = make_installment(plan, number=2, amount="100.00")

    prov = PostFinancePaymentProvider(chf_event)
    assert prov.execute_installment(plan, installment) is True

    create = charge_calls["create"]
    assert create["currency"] == "CHF"
    assert create["token"] == 999888
    assert create["line_items"][0].amount_including_tax == 100.00
    assert create["merchant_reference"] == f"{chf_event.slug}-{order.code}-inst-2"
    assert installment.failure_reason is None


@pytest.mark.django_db
def test_execute_installment_charges_alternative_currency(
    alt_currency_event, order, monkeypatch, charge_calls
):
    """An installment of a plan sold in EUR is charged in EUR."""
    successful_charge(monkeypatch)
    plan = make_plan(
        alt_currency_event,
        order,
        token={**STORED_TOKEN, "charged_currency": "EUR", "fx_rate": "1.07"},
    )
    installment = make_installment(plan, number=2, amount="100.00")

    prov = PostFinancePaymentProvider(alt_currency_event)
    assert prov.execute_installment(plan, installment) is True

    create = charge_calls["create"]
    assert create["currency"] == "EUR"
    assert create["line_items"][0].amount_including_tax == 107.00


@pytest.mark.django_db
def test_installment_uses_snapshotted_rate_not_current_setting(
    alt_currency_event, order, monkeypatch, charge_calls
):
    """
    A rate change after the order must not reprice open installments.
    """
    successful_charge(monkeypatch)
    alt_currency_event.settings.set("payment_postfinance_alt_currency_rate", "2.00")
    plan = make_plan(
        alt_currency_event,
        order,
        token={**STORED_TOKEN, "charged_currency": "EUR", "fx_rate": "1.07"},
    )
    installment = make_installment(plan, number=3, amount="100.00")

    prov = PostFinancePaymentProvider(alt_currency_event)
    assert prov.execute_installment(plan, installment) is True

    assert charge_calls["create"]["line_items"][0].amount_including_tax == 107.00


@pytest.mark.django_db
def test_installment_falls_back_to_configured_rate(
    alt_currency_event, order, monkeypatch, charge_calls
):
    """
    A plan predating the snapshot converts at the currently configured rate,
    which is the only rate on record for it.
    """
    successful_charge(monkeypatch)
    plan = make_plan(
        alt_currency_event, order, token={**STORED_TOKEN, "charged_currency": "EUR"}
    )
    installment = make_installment(plan, number=2, amount="100.00")

    prov = PostFinancePaymentProvider(alt_currency_event)
    assert prov.execute_installment(plan, installment) is True

    assert charge_calls["create"]["currency"] == "EUR"
    assert charge_calls["create"]["line_items"][0].amount_including_tax == 107.00


@pytest.mark.django_db
def test_installment_fails_when_rate_is_unknown(chf_event, order, monkeypatch):
    """
    Without a rate, the amount to charge cannot be derived — the installment
    fails with a reason instead of being charged something made up.
    """
    created = MagicMock()
    monkeypatch.setattr(
        "pretix_postfinance.payment.PostFinanceClient.create_transaction",
        lambda self, **kwargs: created,
    )
    plan = make_plan(chf_event, order, token={**STORED_TOKEN, "charged_currency": "EUR"})
    installment = make_installment(plan, number=2, amount="100.00")

    prov = PostFinancePaymentProvider(chf_event)
    assert prov.execute_installment(plan, installment) is False

    assert "exchange rate" in installment.failure_reason
    created.assert_not_called()


@pytest.mark.django_db
def test_installment_rounds_each_share_independently(
    alt_currency_event, order, monkeypatch, charge_calls
):
    """Each installment converts its own amount, half-up."""
    successful_charge(monkeypatch)
    plan = make_plan(
        alt_currency_event,
        order,
        token={**STORED_TOKEN, "charged_currency": "EUR", "fx_rate": "1.07"},
    )
    installment = make_installment(plan, number=3, amount="33.34")

    prov = PostFinancePaymentProvider(alt_currency_event)
    assert prov.execute_installment(plan, installment) is True

    # 33.34 * 1.07 = 35.6738
    assert charge_calls["create"]["line_items"][0].amount_including_tax == 35.67


@pytest.mark.django_db
def test_execute_installment_without_token(chf_event, order):
    plan = make_plan(chf_event, order, token={})
    installment = make_installment(plan)

    prov = PostFinancePaymentProvider(chf_event)
    assert prov.execute_installment(plan, installment) is False
    assert installment.failure_reason == "No payment token available"


@pytest.mark.django_db
def test_execute_installment_records_charge_failure(
    chf_event, order, monkeypatch, charge_calls
):
    monkeypatch.setattr(
        "pretix_postfinance.payment.PostFinanceClient.process_with_token",
        lambda self, tid: SimpleNamespace(
            state=ChargeState.FAILED,
            failure_reason=SimpleNamespace(description="Insufficient funds"),
        ),
    )
    plan = make_plan(chf_event, order, token=dict(STORED_TOKEN))
    installment = make_installment(plan)

    prov = PostFinancePaymentProvider(chf_event)
    assert prov.execute_installment(plan, installment) is False
    assert installment.failure_reason == "Insufficient funds"


@pytest.mark.django_db
def test_execute_installment_records_pending_charge_as_failure(
    chf_event, order, monkeypatch, charge_calls
):
    """
    A charge that is neither successful nor failed has not moved money, so
    pretix must not be told the installment is paid.
    """
    monkeypatch.setattr(
        "pretix_postfinance.payment.PostFinanceClient.process_with_token",
        lambda self, tid: SimpleNamespace(state=ChargeState.PENDING, failure_reason=None),
    )
    plan = make_plan(chf_event, order, token=dict(STORED_TOKEN))
    installment = make_installment(plan)

    prov = PostFinancePaymentProvider(chf_event)
    assert prov.execute_installment(plan, installment) is False
    assert "not successful" in installment.failure_reason


@pytest.mark.django_db
def test_execute_installment_survives_api_error(chf_event, order, monkeypatch):
    def boom(self, **kwargs):
        raise PostFinanceError("PostFinance is down", status_code=503)

    monkeypatch.setattr(
        "pretix_postfinance.payment.PostFinanceClient.create_transaction", boom
    )
    plan = make_plan(chf_event, order, token=dict(STORED_TOKEN))
    installment = make_installment(plan)

    prov = PostFinancePaymentProvider(chf_event)
    assert prov.execute_installment(plan, installment) is False
    assert installment.failure_reason == "PostFinance is down"


@pytest.mark.django_db
def test_execute_installment_without_transaction_id(chf_event, order, monkeypatch):
    monkeypatch.setattr(
        "pretix_postfinance.payment.PostFinanceClient.create_transaction",
        lambda self, **kwargs: SimpleNamespace(id=None),
    )
    plan = make_plan(chf_event, order, token=dict(STORED_TOKEN))
    installment = make_installment(plan)

    prov = PostFinancePaymentProvider(chf_event)
    assert prov.execute_installment(plan, installment) is False
    assert installment.failure_reason == "PostFinance transaction missing ID"


@pytest.mark.django_db
def test_installment_charged_in_the_space_that_issued_the_token(
    chf_event, order, monkeypatch, charge_calls
):
    """
    A token only works in the space it was created in, so the recorded space
    decides which credentials are used — not the event's current test mode.
    """
    successful_charge(monkeypatch)
    chf_event.settings.set("payment_postfinance_test_space_id", "54321")
    chf_event.settings.set("payment_postfinance_test_user_id", "67890")
    chf_event.settings.set("payment_postfinance_test_auth_key", "test-secret")
    plan = make_plan(chf_event, order, token={**STORED_TOKEN, "space_id": "54321"})
    installment = make_installment(plan)

    prov = PostFinancePaymentProvider(chf_event)
    assert prov.execute_installment(plan, installment) is True
    assert charge_calls["space_id"] == 54321


@pytest.mark.django_db
def test_revoke_payment_token(chf_event, order, monkeypatch):
    deleted = []
    monkeypatch.setattr(
        "pretix_postfinance.payment.PostFinanceClient.delete_token",
        lambda self, tid: deleted.append(tid),
    )
    plan = make_plan(chf_event, order, token=dict(STORED_TOKEN))

    PostFinancePaymentProvider(chf_event).revoke_payment_token(plan)
    assert deleted == [999888]


@pytest.mark.django_db
def test_revoke_payment_token_without_token(chf_event, order, monkeypatch):
    monkeypatch.setattr(
        "pretix_postfinance.payment.PostFinanceClient.delete_token",
        lambda self, tid: pytest.fail("nothing to revoke"),
    )
    plan = make_plan(chf_event, order, token={})

    PostFinancePaymentProvider(chf_event).revoke_payment_token(plan)


@pytest.mark.django_db
def test_revoke_payment_token_survives_api_error(chf_event, order, monkeypatch):
    """
    pretix clears the plan either way, so a failed revocation must not stop
    the plan from being closed.
    """

    def boom(self, tid):
        raise PostFinanceError("token already gone", status_code=404)

    monkeypatch.setattr("pretix_postfinance.payment.PostFinanceClient.delete_token", boom)
    plan = make_plan(chf_event, order, token=dict(STORED_TOKEN))

    PostFinancePaymentProvider(chf_event).revoke_payment_token(plan)
