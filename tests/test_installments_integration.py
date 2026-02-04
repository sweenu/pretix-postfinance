"""
End-to-end tests of installment plans against pretix's own models.

Installments live in the ``pretix.efcc`` app, which only exists on the EFCC
pretix fork, so the whole module is skipped on an installation without it.
Everything here drives pretix's real flow — plan creation at order time, the
first payment through the provider, and the scheduled charges pretix's
periodic task makes afterwards — rather than calling the provider directly.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest
from django_scopes import scopes_disabled
from postfinancecheckout.models import ChargeState, TransactionState
from pretix.base.models import OrderPayment

pytest.importorskip("pretix.efcc.models", reason="requires pretix with installments")

from pretix.base.services.installments import (
    cancel_installment_plan,
    create_installment_plan,
    process_single_installment,
)
from pretix.efcc.models import InstallmentPlan, ScheduledInstallment

from pretix_postfinance.payment import PostFinancePaymentProvider
from pretix_postfinance.views import _process_transaction_webhook

TOKEN_ID = 999888


@pytest.fixture
def installments_event(event):
    event.currency = "CHF"
    event.save()
    event.settings.set("installments_enabled", True)
    event.settings.set("installments_count", 3)
    event.settings.set("installments_limit_by_event_date", False)
    return event


@pytest.fixture
def alt_currency_event(installments_event):
    installments_event.settings.set("payment_postfinance_alt_currency", "EUR")
    installments_event.settings.set("payment_postfinance_alt_currency_rate", "1.07")
    return installments_event


@pytest.fixture
def installments_order(installments_event, order):
    order.total = Decimal("300.00")
    order.save()
    return order


def fulfilled_transaction(with_token=True):
    token = (
        SimpleNamespace(
            id=TOKEN_ID,
            customer_id="cus_test123",
            customer_email_address="customer@example.org",
        )
        if with_token
        else None
    )
    return SimpleNamespace(
        id=123456,
        state=TransactionState.FULFILL,
        payment_connector_configuration=SimpleNamespace(name="TWINT"),
        created_on="2026-01-13T10:00:00Z",
        token=token,
    )


@pytest.fixture
def postfinance(monkeypatch):
    """
    Stand in for the PostFinance API and record every transaction created.

    One stub for the whole flow, so the interactive first payment and the
    later token charges cannot shadow each other's patches.
    """
    api = SimpleNamespace(transactions=[], revoked=[], transaction=fulfilled_transaction())

    def create_transaction(self, **kwargs):
        api.transactions.append(kwargs)
        return SimpleNamespace(id=123456 + len(api.transactions))

    monkeypatch.setattr(
        "pretix_postfinance.payment.PostFinanceClient.create_transaction",
        create_transaction,
    )
    monkeypatch.setattr(
        "pretix_postfinance.payment.PostFinanceClient.get_payment_page_url",
        lambda self, tid: f"https://checkout.postfinance.ch/pay/{tid}",
    )
    monkeypatch.setattr(
        "pretix_postfinance.payment.PostFinanceClient.get_transaction",
        lambda self, tid: api.transaction,
    )
    monkeypatch.setattr(
        "pretix_postfinance.payment.PostFinanceClient.process_with_token",
        lambda self, tid: SimpleNamespace(state=ChargeState.SUCCESSFUL, failure_reason=None),
    )
    monkeypatch.setattr(
        "pretix_postfinance.payment.PostFinanceClient.delete_token",
        lambda self, tid: api.revoked.append(tid),
    )

    # Only the charges pretix schedules carry a token; the first payment is
    # the customer paying on the payment page.
    api.token_charges = lambda: [t for t in api.transactions if t.get("token")]
    return api


@pytest.fixture
def pay_first_installment(postfinance, rf):
    """
    Drive a plan's first payment through the provider, as checkout does.
    """

    def _pay(event, payment, *, pay_alt=False):
        prov = PostFinancePaymentProvider(event)
        req = rf.post("/", {"payment": "postfinance"})
        req.session = {}
        if pay_alt:
            req.session[prov._session_pay_alt_key] = True

        # The first call creates the transaction and sends the customer to
        # PostFinance; returning from the payment page calls it again, and
        # that second call is what settles the payment.
        redirect_url = prov.execute_payment(req, payment)
        assert redirect_url.startswith("https://checkout.postfinance.ch/pay/")
        prov.execute_payment(req, payment)
        payment.refresh_from_db()
        return prov

    return _pay


def first_payment_of(plan):
    return plan.installments.get(installment_number=1).payment


def due(plan, number):
    """
    Load a scheduled installment the way pretix's periodic task does.

    Fetching it through ``plan.installments`` would hand it the test's own
    in-memory plan, whose token predates the first payment.
    """
    return ScheduledInstallment.objects.select_related(
        "plan", "plan__order", "plan__order__event"
    ).get(plan=plan, installment_number=number)


@pytest.mark.django_db
def test_plan_creation_splits_the_order(installments_event, installments_order):
    plan = create_installment_plan(installments_order, "postfinance", 3)

    assert plan.total_installments == 3
    assert [i.amount for i in plan.installments.all()] == [
        Decimal("100.00"),
        Decimal("100.00"),
        Decimal("100.00"),
    ]
    assert first_payment_of(plan).amount == Decimal("100.00")


@pytest.mark.django_db
def test_full_plan_lifecycle_in_event_currency(
    installments_event,
    installments_order,
    pay_first_installment,
    postfinance,
):
    """
    The customer pays the first installment, pretix charges the rest against
    the token, and the plan closes with the token revoked.
    """
    plan = create_installment_plan(installments_order, "postfinance", 3)
    payment = first_payment_of(plan)

    pay_first_installment(installments_event, payment)

    # The first payment settles installment one and leaves the token behind.
    plan.refresh_from_db()
    assert payment.state == OrderPayment.PAYMENT_STATE_CONFIRMED
    assert plan.payment_token["token_id"] == TOKEN_ID
    assert plan.installments_paid == 1
    assert plan.installments.get(installment_number=1).state == (
        ScheduledInstallment.STATE_PAID
    )
    assert plan.status == InstallmentPlan.STATUS_ACTIVE

    for number in (2, 3):
        installment = due(plan, number)
        assert process_single_installment(installment) is True
        installment.refresh_from_db()
        assert installment.state == ScheduledInstallment.STATE_PAID
        assert installment.payment.amount == Decimal("100.00")

    assert [c["currency"] for c in postfinance.token_charges()] == ["CHF", "CHF"]
    assert [c["line_items"][0].amount_including_tax for c in postfinance.token_charges()] == [
        100.00,
        100.00,
    ]
    assert all(c["token"] == TOKEN_ID for c in postfinance.token_charges())

    plan.refresh_from_db()
    assert plan.status == InstallmentPlan.STATUS_COMPLETED
    assert plan.installments_paid == 3
    assert postfinance.revoked == [TOKEN_ID]

    assert plan.order.pending_sum == Decimal("0.00")


@pytest.mark.django_db
def test_full_plan_lifecycle_in_alternative_currency(
    alt_currency_event,
    installments_order,
    pay_first_installment,
    postfinance,
):
    """
    A plan bought in the alternative currency stays in that currency: every
    scheduled charge converts its own share at the rate the customer was
    quoted, while pretix keeps accounting the order in CHF.
    """
    plan = create_installment_plan(installments_order, "postfinance", 3)
    payment = first_payment_of(plan)

    pay_first_installment(alt_currency_event, payment, pay_alt=True)

    payment.refresh_from_db()
    assert payment.info_data["charged_currency"] == "EUR"
    assert payment.info_data["charged_amount"] == "107.00"
    assert payment.amount == Decimal("100.00")

    plan.refresh_from_db()
    assert plan.payment_token["charged_currency"] == "EUR"
    assert plan.payment_token["fx_rate"] == "1.07"
    # The first payment's own converted amount must not be mistaken for a
    # per-installment amount.
    assert "charged_amount" not in plan.payment_token

    for number in (2, 3):
        assert process_single_installment(due(plan, number)) is True

    assert [c["currency"] for c in postfinance.token_charges()] == ["EUR", "EUR"]
    assert [c["line_items"][0].amount_including_tax for c in postfinance.token_charges()] == [
        107.00,
        107.00,
    ]

    # pretix's own records stay in the event currency.
    plan.refresh_from_db()
    assert plan.status == InstallmentPlan.STATUS_COMPLETED
    assert [i.payment.amount for i in plan.installments.order_by("installment_number")] == [
        Decimal("100.00"),
        Decimal("100.00"),
        Decimal("100.00"),
    ]
    assert plan.order.pending_sum == Decimal("0.00")
    assert postfinance.revoked == [TOKEN_ID]


@pytest.mark.django_db
def test_alternative_currency_rate_change_does_not_reprice_open_installments(
    alt_currency_event,
    installments_order,
    pay_first_installment,
    postfinance,
):
    plan = create_installment_plan(installments_order, "postfinance", 3)
    pay_first_installment(alt_currency_event, first_payment_of(plan), pay_alt=True)

    alt_currency_event.settings.set("payment_postfinance_alt_currency_rate", "2.00")

    assert process_single_installment(due(plan, 2)) is True

    assert postfinance.token_charges()[0]["line_items"][0].amount_including_tax == 107.00


@pytest.mark.django_db
def test_uneven_split_converts_each_share(
    alt_currency_event,
    installments_order,
    pay_first_installment,
    postfinance,
):
    """
    pretix puts the rounding remainder on the last installment; each share is
    converted on its own, so the charges follow that split.
    """
    installments_order.total = Decimal("100.00")
    installments_order.save()

    plan = create_installment_plan(installments_order, "postfinance", 3)
    assert [i.amount for i in plan.installments.all()] == [
        Decimal("33.33"),
        Decimal("33.33"),
        Decimal("33.34"),
    ]

    pay_first_installment(alt_currency_event, first_payment_of(plan), pay_alt=True)
    for number in (2, 3):
        assert process_single_installment(due(plan, number)) is True

    # 33.33 * 1.07 = 35.6631 -> 35.66; 33.34 * 1.07 = 35.6738 -> 35.67
    assert [c["line_items"][0].amount_including_tax for c in postfinance.token_charges()] == [
        35.66,
        35.67,
    ]


@pytest.mark.django_db
def test_webhook_stores_the_token_when_the_customer_never_returns(
    installments_event, installments_order, monkeypatch, postfinance
):
    """
    A customer who closes the tab after paying still gets a working plan:
    the webhook is then the only place the token is ever seen.
    """
    plan = create_installment_plan(installments_order, "postfinance", 3)
    payment = first_payment_of(plan)
    payment.info_data = {"transaction_id": 123456, "space_id": "12345"}
    payment.save(update_fields=["info"])

    monkeypatch.setattr(
        "pretix_postfinance.views.PostFinanceClient.get_transaction",
        lambda self, tid: fulfilled_transaction(),
    )
    monkeypatch.setattr(
        "pretix_postfinance.views._client_for_entity",
        lambda entity, space_id, entity_id, kind: (
            PostFinancePaymentProvider(installments_event)._get_client(),
            "ok",
        ),
    )

    with scopes_disabled():
        status, processed = _process_transaction_webhook(123456, 12345)

    assert processed is True
    plan.refresh_from_db()
    assert plan.payment_token["token_id"] == TOKEN_ID
    assert plan.installments_paid == 1

    # The rest of the plan can now be charged.
    assert process_single_installment(due(plan, 2)) is True
    assert postfinance.token_charges()[0]["token"] == TOKEN_ID


@pytest.mark.django_db
def test_missing_token_leaves_the_plan_chargeable_by_hand(
    installments_event, installments_order, pay_first_installment, postfinance
):
    """
    If PostFinance returns no token, the first payment still stands — only
    the scheduled installments cannot run.
    """
    plan = create_installment_plan(installments_order, "postfinance", 3)
    payment = first_payment_of(plan)

    postfinance.transaction = fulfilled_transaction(with_token=False)
    pay_first_installment(installments_event, payment)

    payment.refresh_from_db()
    assert payment.state == OrderPayment.PAYMENT_STATE_CONFIRMED
    plan.refresh_from_db()
    assert plan.payment_token == {}

    installment = due(plan, 2)
    assert process_single_installment(installment) is False
    installment.refresh_from_db()
    assert installment.state == ScheduledInstallment.STATE_FAILED
    assert installment.failure_reason == "No payment token available"


@pytest.mark.django_db
def test_cancelling_a_plan_revokes_the_token(
    installments_event, installments_order, pay_first_installment, postfinance
):
    plan = create_installment_plan(installments_order, "postfinance", 3)
    pay_first_installment(installments_event, first_payment_of(plan))

    plan.refresh_from_db()
    cancel_installment_plan(plan, cancel_order=False)

    assert postfinance.revoked == [TOKEN_ID]
    plan.refresh_from_db()
    assert plan.status == InstallmentPlan.STATUS_CANCELLED
    assert plan.payment_token == {}
    assert set(plan.installments.values_list("state", flat=True)) == {
        ScheduledInstallment.STATE_PAID,
        ScheduledInstallment.STATE_CANCELLED,
    }
