"""
Tests for the PostFinance payment provider.

Inspired by pretix's Stripe plugin test suite.
"""

from __future__ import annotations

import json
from datetime import timedelta
from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from django.test import RequestFactory
from django.utils.timezone import now
from django_scopes import scope
from postfinancecheckout.models import TransactionState
from pretix.base.models import Event, Order, OrderPayment, OrderRefund, Organizer
from pretix.base.payment import PaymentException

from pretix_postfinance.api import PostFinanceError
from pretix_postfinance.payment import PostFinancePaymentProvider


@pytest.fixture
def env():
    """Create test environment with organizer, event, and order."""
    o = Organizer.objects.create(name="Dummy", slug="dummy")
    with scope(organizer=o):
        event = Event.objects.create(
            organizer=o,
            name="Dummy",
            slug="dummy",
            date_from=now(),
            live=True,
            plugins="pretix_postfinance",
        )
        event.settings.set("payment_postfinance_space_id", "12345")
        event.settings.set("payment_postfinance_user_id", "67890")
        event.settings.set("payment_postfinance_auth_key", "test-secret")

        event.settings.set("payment_postfinance__enabled", True)

        order = Order.objects.create(
            code="FOOBAR",
            event=event,
            email="dummy@dummy.test",
            status=Order.STATUS_PENDING,
            datetime=now(),
            expires=now() + timedelta(days=10),
            total=Decimal("13.37"),
            sales_channel=o.sales_channels.get(identifier="web"),
        )
        yield event, order


@pytest.fixture(autouse=True)
def no_messages(monkeypatch):
    """Patch out template rendering for performance improvements."""
    monkeypatch.setattr("django.contrib.messages.api.add_message", lambda *args, **kwargs: None)


@pytest.fixture
def factory():
    """Create request factory."""
    return RequestFactory()


class MockedTransaction:
    """Mock PostFinance Transaction object."""

    id = 123456
    state = TransactionState.COMPLETED
    payment_connector_configuration = MagicMock()
    payment_connector_configuration.name = "TWINT"
    created_on = "2026-01-13T10:00:00Z"


class MockedRefund:
    """Mock PostFinance Refund object."""

    id = 789012
    state = MagicMock()
    state.value = "SUCCESSFUL"
    amount = 50.00
    created_on = "2026-01-13T11:00:00Z"


class MockedSpace:
    """Mock PostFinance Space object."""

    id = 12345
    name = "Test Space"


class MockedCompletion:
    """Mock PostFinance TransactionCompletion object."""

    id = 111222


class MockedVoid:
    """Mock PostFinance TransactionVoid object."""

    id = 333444


@pytest.mark.django_db
def test_perform_success(env, factory, monkeypatch):
    """Test successful payment execution."""
    event, order = env

    def get_transaction(transaction_id):
        t = MockedTransaction()
        t.state = TransactionState.COMPLETED
        return t

    monkeypatch.setattr(
        "pretix_postfinance.payment.PostFinanceClient.get_transaction",
        lambda self, tid: get_transaction(tid),
    )

    prov = PostFinancePaymentProvider(event)
    req = factory.post("/")
    req.session = {"payment_postfinance_transaction_id": 123456}

    payment = order.payments.create(provider="postfinance", amount=order.total)
    prov.execute_payment(req, payment)

    order.refresh_from_db()
    assert order.status == Order.STATUS_PAID


@pytest.mark.django_db
def test_perform_success_authorized_state(env, factory, monkeypatch):
    """Test successful payment with AUTHORIZED state."""
    event, order = env

    def get_transaction(transaction_id):
        t = MockedTransaction()
        t.state = TransactionState.AUTHORIZED
        return t

    monkeypatch.setattr(
        "pretix_postfinance.payment.PostFinanceClient.get_transaction",
        lambda self, tid: get_transaction(tid),
    )

    prov = PostFinancePaymentProvider(event)
    req = factory.post("/")
    req.session = {"payment_postfinance_transaction_id": 123456}

    payment = order.payments.create(provider="postfinance", amount=order.total)
    prov.execute_payment(req, payment)

    order.refresh_from_db()
    assert order.status == Order.STATUS_PAID


@pytest.mark.django_db
def test_perform_failed(env, factory, monkeypatch):
    """Test failed payment execution."""
    event, order = env

    def get_transaction(transaction_id):
        t = MockedTransaction()
        t.state = TransactionState.FAILED
        return t

    monkeypatch.setattr(
        "pretix_postfinance.payment.PostFinanceClient.get_transaction",
        lambda self, tid: get_transaction(tid),
    )

    prov = PostFinancePaymentProvider(event)
    req = factory.post("/")
    req.session = {"payment_postfinance_transaction_id": 123456}

    payment = order.payments.create(provider="postfinance", amount=order.total)
    prov.execute_payment(req, payment)

    order.refresh_from_db()
    assert order.status == Order.STATUS_PENDING
    payment.refresh_from_db()
    assert payment.state == OrderPayment.PAYMENT_STATE_FAILED


@pytest.mark.django_db
def test_perform_declined(env, factory, monkeypatch):
    """Test declined payment execution."""
    event, order = env

    def get_transaction(transaction_id):
        t = MockedTransaction()
        t.state = TransactionState.DECLINE
        return t

    monkeypatch.setattr(
        "pretix_postfinance.payment.PostFinanceClient.get_transaction",
        lambda self, tid: get_transaction(tid),
    )

    prov = PostFinancePaymentProvider(event)
    req = factory.post("/")
    req.session = {"payment_postfinance_transaction_id": 123456}

    payment = order.payments.create(provider="postfinance", amount=order.total)
    prov.execute_payment(req, payment)

    order.refresh_from_db()
    assert order.status == Order.STATUS_PENDING
    payment.refresh_from_db()
    assert payment.state == OrderPayment.PAYMENT_STATE_FAILED


@pytest.mark.django_db
def test_perform_api_error(env, factory, monkeypatch):
    """Test payment execution with API error."""
    event, order = env

    def get_transaction_error(transaction_id):
        raise PostFinanceError("API Error", status_code=500)

    monkeypatch.setattr(
        "pretix_postfinance.payment.PostFinanceClient.get_transaction",
        lambda self, tid: get_transaction_error(tid),
    )

    prov = PostFinancePaymentProvider(event)
    req = factory.post("/")
    req.session = {"payment_postfinance_transaction_id": 123456}

    payment = order.payments.create(provider="postfinance", amount=order.total)

    with pytest.raises(PaymentException):
        prov.execute_payment(req, payment)

    order.refresh_from_db()
    assert order.status == Order.STATUS_PENDING


@pytest.mark.django_db
def test_perform_no_transaction_id(env, factory):
    """Test payment execution without transaction ID in session."""
    event, order = env

    prov = PostFinancePaymentProvider(event)
    req = factory.post("/")
    req.session = {}

    payment = order.payments.create(provider="postfinance", amount=order.total)
    result = prov.execute_payment(req, payment)

    # Should return None without raising exception
    assert result is None
    payment.refresh_from_db()
    assert payment.info_data.get("error") == "No transaction ID in session"


@pytest.mark.django_db
def test_refund_success(env, factory, monkeypatch):
    """Test successful refund execution."""
    event, order = env

    def refund_transaction(*args, **kwargs):
        r = MockedRefund()
        r.id = 789012
        r.state = MagicMock()
        r.state.value = "SUCCESSFUL"
        r.amount = 13.37
        r.created_on = "2026-01-13T11:00:00Z"
        return r

    monkeypatch.setattr(
        "pretix_postfinance.payment.PostFinanceClient.refund_transaction",
        lambda self, **kwargs: refund_transaction(**kwargs),
    )

    order.status = Order.STATUS_PAID
    order.save()

    payment = order.payments.create(
        provider="postfinance",
        amount=order.total,
        info=json.dumps(
            {
                "transaction_id": 123456,
                "state": TransactionState.COMPLETED.value,
            }
        ),
    )

    prov = PostFinancePaymentProvider(event)
    refund = order.refunds.create(
        provider="postfinance",
        amount=order.total,
        payment=payment,
    )

    prov.execute_refund(refund)

    refund.refresh_from_db()
    assert refund.state == OrderRefund.REFUND_STATE_DONE


@pytest.mark.django_db
def test_refund_partial(env, factory, monkeypatch):
    """Test partial refund execution."""
    event, order = env

    def refund_transaction(*args, **kwargs):
        r = MockedRefund()
        r.id = 789012
        r.state = MagicMock()
        r.state.value = "SUCCESSFUL"
        r.amount = 5.00
        r.created_on = "2026-01-13T11:00:00Z"
        return r

    monkeypatch.setattr(
        "pretix_postfinance.payment.PostFinanceClient.refund_transaction",
        lambda self, **kwargs: refund_transaction(**kwargs),
    )

    order.status = Order.STATUS_PAID
    order.save()

    payment = order.payments.create(
        provider="postfinance",
        amount=order.total,
        info=json.dumps(
            {
                "transaction_id": 123456,
                "state": TransactionState.COMPLETED.value,
            }
        ),
    )

    prov = PostFinancePaymentProvider(event)
    refund = order.refunds.create(
        provider="postfinance",
        amount=Decimal("5.00"),
        payment=payment,
    )

    prov.execute_refund(refund)

    refund.refresh_from_db()
    assert refund.state == OrderRefund.REFUND_STATE_DONE
    # Refund info is stored on the refund object
    assert refund.info_data.get("refund_id") == 789012
    assert refund.info_data.get("state") == "SUCCESSFUL"


@pytest.mark.django_db
def test_refund_api_error(env, factory, monkeypatch):
    """Test refund with API error."""
    event, order = env

    def refund_error(*args, **kwargs):
        raise PostFinanceError("Refund failed", status_code=400)

    monkeypatch.setattr(
        "pretix_postfinance.payment.PostFinanceClient.refund_transaction",
        lambda self, **kwargs: refund_error(**kwargs),
    )

    order.status = Order.STATUS_PAID
    order.save()

    payment = order.payments.create(
        provider="postfinance",
        amount=order.total,
        info=json.dumps(
            {
                "transaction_id": 123456,
                "state": TransactionState.COMPLETED.value,
            }
        ),
    )

    prov = PostFinancePaymentProvider(event)
    refund = order.refunds.create(
        provider="postfinance",
        amount=order.total,
        payment=payment,
    )

    with pytest.raises(PaymentException):
        prov.execute_refund(refund)

    refund.refresh_from_db()
    assert refund.state != OrderRefund.REFUND_STATE_DONE


@pytest.mark.django_db
def test_refund_wrong_state(env, factory):
    """Test refund when transaction is not in refundable state."""
    event, order = env

    order.status = Order.STATUS_PAID
    order.save()

    payment = order.payments.create(
        provider="postfinance",
        amount=order.total,
        info=json.dumps(
            {
                "transaction_id": 123456,
                "state": TransactionState.AUTHORIZED.value,  # Not refundable
            }
        ),
    )

    prov = PostFinancePaymentProvider(event)
    refund = order.refunds.create(
        provider="postfinance",
        amount=order.total,
        payment=payment,
    )

    with pytest.raises(PaymentException) as exc_info:
        prov.execute_refund(refund)

    assert "cannot be refunded" in str(exc_info.value)


@pytest.mark.django_db
def test_capture_success(env, factory, monkeypatch):
    """Test successful manual capture."""
    event, order = env

    def complete_transaction(transaction_id):
        return MockedCompletion()

    monkeypatch.setattr(
        "pretix_postfinance.payment.PostFinanceClient.complete_transaction",
        lambda self, tid: complete_transaction(tid),
    )

    order.status = Order.STATUS_PENDING
    order.save()

    payment = order.payments.create(
        provider="postfinance",
        amount=order.total,
        info=json.dumps(
            {
                "transaction_id": 123456,
                "state": TransactionState.AUTHORIZED.value,
            }
        ),
    )

    prov = PostFinancePaymentProvider(event)
    success, error = prov.execute_capture(payment)

    assert success is True
    assert error is None

    payment.refresh_from_db()
    assert payment.info_data.get("state") == TransactionState.COMPLETED.value


@pytest.mark.django_db
def test_capture_wrong_state(env, factory):
    """Test capture when transaction is not in AUTHORIZED state."""
    event, order = env

    payment = order.payments.create(
        provider="postfinance",
        amount=order.total,
        info=json.dumps(
            {
                "transaction_id": 123456,
                "state": TransactionState.COMPLETED.value,  # Already completed
            }
        ),
    )

    prov = PostFinancePaymentProvider(event)
    success, error = prov.execute_capture(payment)

    assert success is False
    assert "cannot be captured" in error


@pytest.mark.django_db
def test_void_success(env, factory, monkeypatch):
    """Test successful void."""
    event, order = env

    def void_transaction(transaction_id):
        return MockedVoid()

    monkeypatch.setattr(
        "pretix_postfinance.payment.PostFinanceClient.void_transaction",
        lambda self, tid: void_transaction(tid),
    )

    payment = order.payments.create(
        provider="postfinance",
        amount=order.total,
        info=json.dumps(
            {
                "transaction_id": 123456,
                "state": TransactionState.AUTHORIZED.value,
            }
        ),
    )

    prov = PostFinancePaymentProvider(event)
    success, error = prov.execute_void(payment)

    assert success is True
    assert error is None

    payment.refresh_from_db()
    assert payment.info_data.get("state") == TransactionState.VOIDED.value


@pytest.mark.django_db
def test_void_wrong_state(env, factory):
    """Test void when transaction is not in AUTHORIZED state."""
    event, order = env

    payment = order.payments.create(
        provider="postfinance",
        amount=order.total,
        info=json.dumps(
            {
                "transaction_id": 123456,
                "state": TransactionState.COMPLETED.value,  # Already completed
            }
        ),
    )

    prov = PostFinancePaymentProvider(event)
    success, error = prov.execute_void(payment)

    assert success is False
    assert "cannot be voided" in error


@pytest.mark.django_db
def test_test_connection_success(env, monkeypatch):
    """Test successful connection test."""
    event, _ = env

    def get_space():
        return MockedSpace()

    monkeypatch.setattr(
        "pretix_postfinance.payment.PostFinanceClient.get_space",
        lambda self: get_space(),
    )

    prov = PostFinancePaymentProvider(event)
    success, message = prov.test_connection()

    assert success is True
    assert "Test Space" in message


@pytest.mark.django_db
def test_test_connection_auth_error(env, monkeypatch):
    """Test connection test with authentication error."""
    event, _ = env

    def get_space_error():
        raise PostFinanceError("Unauthorized", status_code=401)

    monkeypatch.setattr(
        "pretix_postfinance.payment.PostFinanceClient.get_space",
        lambda self: get_space_error(),
    )

    prov = PostFinancePaymentProvider(event)
    success, message = prov.test_connection()

    assert success is False
    assert "Authentication failed" in message


@pytest.mark.django_db
def test_test_connection_missing_credentials(env):
    """Test connection test with missing credentials."""
    event, _ = env

    # Clear credentials
    event.settings.set("payment_postfinance_space_id", "")
    event.settings.set("payment_postfinance_user_id", "")
    event.settings.set("payment_postfinance_auth_key", "")

    prov = PostFinancePaymentProvider(event)
    success, message = prov.test_connection()

    assert success is False
    assert "configure" in message.lower()


@pytest.mark.django_db
def test_payment_refund_supported(env):
    """Test payment_refund_supported returns correct value."""
    event, order = env

    prov = PostFinancePaymentProvider(event)

    # Should be supported for COMPLETED state
    payment = order.payments.create(
        provider="postfinance",
        amount=order.total,
        info=json.dumps({"state": TransactionState.COMPLETED.value}),
    )
    assert prov.payment_refund_supported(payment) is True

    # Should be supported for FULFILL state
    payment2 = order.payments.create(
        provider="postfinance",
        amount=order.total,
        info=json.dumps({"state": TransactionState.FULFILL.value}),
    )
    assert prov.payment_refund_supported(payment2) is True

    # Should not be supported for AUTHORIZED state
    payment3 = order.payments.create(
        provider="postfinance",
        amount=order.total,
        info=json.dumps({"state": TransactionState.AUTHORIZED.value}),
    )
    assert prov.payment_refund_supported(payment3) is False


@pytest.mark.django_db
def test_payment_is_valid_session(env, factory):
    """Test payment_is_valid_session checks for transaction ID."""
    event, _ = env

    prov = PostFinancePaymentProvider(event)

    # Valid session with transaction ID
    req = factory.get("/")
    req.session = {"payment_postfinance_transaction_id": 123456}
    assert prov.payment_is_valid_session(req) is True

    # Invalid session without transaction ID
    req2 = factory.get("/")
    req2.session = {}
    assert prov.payment_is_valid_session(req2) is False


@pytest.mark.django_db
def test_matching_id(env):
    """Test matching_id returns transaction ID."""
    event, order = env

    prov = PostFinancePaymentProvider(event)

    payment = order.payments.create(
        provider="postfinance",
        amount=order.total,
        info=json.dumps({"transaction_id": 123456}),
    )

    assert prov.matching_id(payment) == 123456


@pytest.mark.django_db
def test_shred_payment_info(env):
    """Test shred_payment_info removes sensitive data."""
    event, order = env

    prov = PostFinancePaymentProvider(event)

    payment = order.payments.create(
        provider="postfinance",
        amount=order.total,
        info=json.dumps(
            {
                "transaction_id": 123456,
                "state": TransactionState.COMPLETED.value,
                "payment_method": "TWINT",
                "created_on": "2026-01-13T10:00:00Z",
            }
        ),
    )

    prov.shred_payment_info(payment)

    payment.refresh_from_db()
    info = payment.info_data
    assert info.get("transaction_id") == 123456
    assert info.get("state") == TransactionState.COMPLETED.value
    assert info.get("_shredded") is True
    assert info.get("payment_method") is None
    assert info.get("created_on") is None


@pytest.mark.django_db
def test_api_refund_details(env):
    """Test api_refund_details returns correct data."""
    event, order = env

    order.status = Order.STATUS_PAID
    order.save()

    payment = order.payments.create(
        provider="postfinance",
        amount=order.total,
        info=json.dumps({"transaction_id": 123456}),
    )

    refund = order.refunds.create(
        provider="postfinance",
        amount=order.total,
        payment=payment,
        info=json.dumps(
            {
                "refund_id": 789012,
                "state": "SUCCESSFUL",
                "amount": 13.37,
                "created_on": "2026-01-13T11:00:00Z",
            }
        ),
    )

    prov = PostFinancePaymentProvider(event)
    details = prov.api_refund_details(refund)

    assert details["refund_id"] == 789012
    assert details["state"] == "SUCCESSFUL"
    assert details["amount"] == 13.37
    assert details["created_on"] == "2026-01-13T11:00:00Z"


@pytest.mark.django_db
def test_refund_control_render_short(env):
    """Test refund_control_render_short returns correct format."""
    event, order = env

    order.status = Order.STATUS_PAID
    order.save()

    payment = order.payments.create(
        provider="postfinance",
        amount=order.total,
        info=json.dumps({"transaction_id": 123456}),
    )

    # With refund ID
    refund = order.refunds.create(
        provider="postfinance",
        amount=order.total,
        payment=payment,
        info=json.dumps({"refund_id": 789012}),
    )

    prov = PostFinancePaymentProvider(event)
    result = prov.refund_control_render_short(refund)

    assert result == "PostFinance (789012)"

    # Without refund ID
    refund2 = order.refunds.create(
        provider="postfinance",
        amount=order.total,
        payment=payment,
        info=json.dumps({}),
    )

    result2 = prov.refund_control_render_short(refund2)
    assert result2 == "PostFinance"
