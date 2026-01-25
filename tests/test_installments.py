"""
Comprehensive tests for the installment payment feature.

Tests cover:
- InstallmentSchedule model validation and constraints
- Installment calculation utilities
- Token-based charging
- Background tasks for installment processing
- Partial refund handling
"""

# ruff: noqa: B017

from __future__ import annotations

import json
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from django.test import RequestFactory
from django.utils.timezone import now
from django_scopes import scope
from postfinancecheckout.models import TransactionState
from pretix.base.models import Event, Order, Organizer

from pretix_postfinance.api import PostFinanceError
from pretix_postfinance.installments import (
    calculate_installment_schedule,
    get_max_installments,
    validate_schedule_against_event,
)
from pretix_postfinance.models import InstallmentSchedule
from pretix_postfinance.payment import PostFinancePaymentProvider
from pretix_postfinance.tasks import (
    cancel_expired_grace_periods,
    process_due_installments,
    retry_failed_installments,
    send_installment_reminders,
)


@pytest.fixture
def env():
    """Create test environment with organizer, event, and order."""
    o = Organizer.objects.create(name="Dummy", slug="dummy")
    with scope(organizer=o):
        event = Event.objects.create(
            organizer=o,
            name="Dummy",
            slug="dummy",
            date_from=now() + timedelta(days=60),  # Event in 60 days
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
            total=Decimal("100.00"),
            sales_channel=o.sales_channels.get(identifier="web"),
        )
        yield event, order


@pytest.fixture
def factory():
    """Create request factory."""
    return RequestFactory()


# ============================================================================
# InstallmentSchedule Model Tests
# ============================================================================


@pytest.mark.django_db
def test_installment_schedule_creation(env):
    """Test InstallmentSchedule model creation and basic fields."""
    event, order = env

    # Create a valid installment schedule
    schedule = InstallmentSchedule.objects.create(
        order=order,
        installment_number=1,
        amount=Decimal("50.00"),
        due_date=date.today(),
        status=InstallmentSchedule.Status.SCHEDULED,
        num_installments=3,
    )

    assert schedule.order == order
    assert schedule.installment_number == 1
    assert schedule.amount == Decimal("50.00")
    assert schedule.due_date == date.today()
    assert schedule.status == InstallmentSchedule.Status.SCHEDULED
    assert schedule.num_installments == 3


@pytest.mark.django_db
def test_installment_schedule_unique_constraint(env):
    """Test that installment_number is unique per order."""
    event, order = env

    # Create first installment
    InstallmentSchedule.objects.create(
        order=order,
        installment_number=1,
        amount=Decimal("50.00"),
        due_date=date.today(),
        status=InstallmentSchedule.Status.SCHEDULED,
        num_installments=3,
    )

    # Try to create another installment with same number for same order
    # Should raise an exception due to unique constraint violation
    with pytest.raises(Exception):
        InstallmentSchedule.objects.create(
            order=order,
            installment_number=1,  # Same number
            amount=Decimal("60.00"),
            due_date=date.today(),
            status=InstallmentSchedule.Status.SCHEDULED,
            num_installments=3,
        )


@pytest.mark.django_db
def test_installment_schedule_num_installments_validation(env):
    """Test that num_installments must be between 2 and 12."""
    event, order = env

    # Valid range
    schedule = InstallmentSchedule(
        order=order,
        installment_number=1,
        amount=Decimal("50.00"),
        due_date=date.today(),
        status=InstallmentSchedule.Status.SCHEDULED,
        num_installments=2,
    )
    schedule.save()  # Should work

    schedule.num_installments = 12
    schedule.save()  # Should work

    # Invalid range - too low
    schedule.num_installments = 1
    with pytest.raises(ValueError, match="num_installments must be between 2 and 12"):
        schedule.save()

    # Invalid range - too high
    schedule.num_installments = 13
    with pytest.raises(ValueError, match="num_installments must be between 2 and 12"):
        schedule.save()


@pytest.mark.django_db
def test_installment_schedule_status_choices(env):
    """Test that status field only accepts valid choices."""
    event, order = env

    schedule = InstallmentSchedule.objects.create(
        order=order,
        installment_number=1,
        amount=Decimal("50.00"),
        due_date=date.today(),
        status=InstallmentSchedule.Status.SCHEDULED,
        num_installments=3,
    )

    # Test all valid statuses
    valid_statuses = [
        InstallmentSchedule.Status.SCHEDULED,
        InstallmentSchedule.Status.PENDING,
        InstallmentSchedule.Status.PAID,
        InstallmentSchedule.Status.FAILED,
        InstallmentSchedule.Status.CANCELLED,
    ]

    for status in valid_statuses:
        schedule.status = status
        schedule.save()  # Should work for all valid statuses


# ============================================================================
# Installment Calculation Utility Tests
# ============================================================================


def test_get_max_installments_basic():
    """Test basic get_max_installments calculation."""
    # Event in 120 days (about 4 months)
    event_date = date.today() + timedelta(days=120)
    start_date = date.today()

    # Should allow 3 installments (30 days each + 30 day buffer)
    # (120 - 30) / 30 = 3
    max_installments = get_max_installments(event_date, start_date)
    assert max_installments == 3


def test_get_max_installments_with_organizer_limit():
    """Test get_max_installments with organizer limit."""
    event_date = date.today() + timedelta(days=300)  # 10 months
    start_date = date.today()

    # Without organizer limit: (300 - 30) / 30 = 9
    max_installments = get_max_installments(event_date, start_date)
    assert max_installments == 9

    # With organizer limit of 5
    max_installments = get_max_installments(event_date, start_date, organizer_max=5)
    assert max_installments == 5


def test_get_max_installments_event_too_soon():
    """Test get_max_installments when event is too soon."""
    # Event in 20 days (less than 30 day buffer)
    event_date = date.today() + timedelta(days=20)
    start_date = date.today()

    # Should return 0 since we can't complete any installments 30 days before event
    max_installments = get_max_installments(event_date, start_date)
    assert max_installments == 0


def test_get_max_installments_system_max():
    """Test that system maximum of 12 is respected."""
    # Event far in future
    event_date = date.today() + timedelta(days=1000)  # ~33 months
    start_date = date.today()

    # Should be capped at 12
    max_installments = get_max_installments(event_date, start_date)
    assert max_installments == 12


def test_calculate_installment_schedule_equal_amounts():
    """Test that installments have equal amounts (except possibly last)."""
    total_amount = Decimal("100.00")
    num_installments = 3
    start_date = date.today()

    schedule = calculate_installment_schedule(total_amount, num_installments, start_date)

    assert len(schedule) == 3

    # First two installments should be equal
    assert schedule[0][1] == schedule[1][1]

    # Sum should equal total
    total_calculated = sum(amount for _, amount, _ in schedule)
    assert total_calculated == total_amount


def test_calculate_installment_schedule_monthly_intervals():
    """Test that installments are scheduled 30 days apart."""
    total_amount = Decimal("100.00")
    num_installments = 3
    start_date = date(2026, 1, 1)

    schedule = calculate_installment_schedule(total_amount, num_installments, start_date)

    # Check dates are 30 days apart
    assert schedule[0][2] == date(2026, 1, 1)
    assert schedule[1][2] == date(2026, 1, 31)
    assert schedule[2][2] == date(2026, 3, 2)  # 30 days after Jan 31


def test_calculate_installment_schedule_rounding():
    """Test that rounding is handled correctly."""
    # Amount that doesn't divide evenly
    total_amount = Decimal("100.01")
    num_installments = 3
    start_date = date.today()

    schedule = calculate_installment_schedule(total_amount, num_installments, start_date)

    # Sum should still equal total exactly
    total_calculated = sum(amount for _, amount, _ in schedule)
    assert total_calculated == total_amount


def test_calculate_installment_schedule_validation():
    """Test input validation for calculate_installment_schedule."""
    start_date = date.today()

    # Invalid num_installments - too low
    with pytest.raises(ValueError, match="num_installments must be between 2 and 12"):
        calculate_installment_schedule(Decimal("100.00"), 1, start_date)

    # Invalid num_installments - too high
    with pytest.raises(ValueError, match="num_installments must be between 2 and 12"):
        calculate_installment_schedule(Decimal("100.00"), 13, start_date)

    # Invalid total_amount - zero
    with pytest.raises(ValueError, match="total_amount must be positive"):
        calculate_installment_schedule(Decimal("0.00"), 3, start_date)

    # Invalid total_amount - negative
    with pytest.raises(ValueError, match="total_amount must be positive"):
        calculate_installment_schedule(Decimal("-100.00"), 3, start_date)


def test_validate_schedule_against_event_valid():
    """Test schedule validation with valid schedule."""
    event_date = date.today() + timedelta(days=120)  # 4 months from now
    start_date = date.today()

    schedule = calculate_installment_schedule(Decimal("100.00"), 3, start_date)

    # Last installment should be 60 days from start (30 days * 2 intervals)
    # Event is 120 days from now, so 120 - 60 = 60 days buffer > 30 days required
    assert validate_schedule_against_event(schedule, event_date) is True


def test_validate_schedule_against_event_invalid():
    """Test schedule validation with invalid schedule."""
    event_date = date.today() + timedelta(days=35)  # Only 35 days from now
    start_date = date.today()

    schedule = calculate_installment_schedule(Decimal("100.00"), 2, start_date)

    # Last installment is 30 days from start
    # Event is 35 days from now, so 35 - 30 = 5 days buffer < 30 days required
    assert validate_schedule_against_event(schedule, event_date) is False


def test_validate_schedule_against_event_empty():
    """Test schedule validation with empty schedule."""
    event_date = date.today() + timedelta(days=100)

    # Empty schedule should return False
    assert validate_schedule_against_event([], event_date) is False


# ============================================================================
# Token-Based Charging Tests
# ============================================================================


@pytest.mark.django_db
def test_charge_token_success(env, monkeypatch):
    """Test successful token charging."""
    event, order = env

    # Mock the PostFinance API
    mock_transaction = MagicMock()
    mock_transaction.id = 999888
    mock_transaction.state = TransactionState.COMPLETED

    def mock_charge_token(**kwargs):
        return mock_transaction

    monkeypatch.setattr(
        "pretix_postfinance.api.PostFinanceClient.charge_token",
        mock_charge_token,
    )

    # Create payment provider
    prov = PostFinancePaymentProvider(event)
    client = prov._get_client()

    # Test charge_token method
    transaction = client.charge_token(
        token_id="test-token-123",
        amount=50.00,
        currency="CHF",
        merchant_reference="test-ref-456",
    )

    assert transaction.id == 999888
    assert transaction.state == TransactionState.COMPLETED


@pytest.mark.django_db
def test_charge_token_api_error(env, monkeypatch):
    """Test token charging with API error."""
    event, order = env

    def mock_charge_token_error(**kwargs):
        raise PostFinanceError("API Error", status_code=500)

    monkeypatch.setattr(
        "pretix_postfinance.api.PostFinanceClient.charge_token",
        mock_charge_token_error,
    )

    prov = PostFinancePaymentProvider(event)
    client = prov._get_client()

    with pytest.raises(PostFinanceError):
        client.charge_token(
            token_id="test-token-123",
            amount=50.00,
            currency="CHF",
            merchant_reference="test-ref-456",
        )


# ============================================================================
# Background Task Tests
# ============================================================================


@pytest.mark.django_db
def test_process_due_installments_success(env, monkeypatch):
    """Test successful processing of due installments."""
    event, order = env

    # Create a due installment
    installment = InstallmentSchedule.objects.create(
        order=order,
        installment_number=2,
        amount=Decimal("50.00"),
        due_date=date.today(),  # Due today
        status=InstallmentSchedule.Status.SCHEDULED,
        token_id="test-token-123",
        num_installments=3,
    )

    # Mock successful token charge
    mock_transaction = MagicMock()
    mock_transaction.id = 999888
    mock_transaction.state = TransactionState.COMPLETED

    def mock_charge_token(**kwargs):
        return mock_transaction

    monkeypatch.setattr(
        "pretix_postfinance.api.PostFinanceClient.charge_token",
        mock_charge_token,
    )

    # Mock email sending to avoid actual emails
    monkeypatch.setattr(
        "pretix_postfinance.tasks.send_mail",
        lambda *args, **kwargs: None,
    )

    # Run the task
    process_due_installments()

    # Refresh installment from database
    installment.refresh_from_db()

    # Should be marked as paid
    assert installment.status == InstallmentSchedule.Status.PAID
    assert installment.paid_at is not None
    assert installment.failure_reason == ""


@pytest.mark.django_db
def test_process_due_installments_failure(env, monkeypatch):
    """Test failed processing of due installments."""
    event, order = env

    # Create a due installment
    installment = InstallmentSchedule.objects.create(
        order=order,
        installment_number=2,
        amount=Decimal("50.00"),
        due_date=date.today(),  # Due today
        status=InstallmentSchedule.Status.SCHEDULED,
        token_id="test-token-123",
        num_installments=3,
    )

    # Mock failed token charge
    mock_transaction = MagicMock()
    mock_transaction.id = 999888
    mock_transaction.state = TransactionState.FAILED

    def mock_charge_token(**kwargs):
        return mock_transaction

    monkeypatch.setattr(
        "pretix_postfinance.api.PostFinanceClient.charge_token",
        mock_charge_token,
    )

    # Mock email sending
    monkeypatch.setattr(
        "pretix_postfinance.tasks.send_mail",
        lambda *args, **kwargs: None,
    )

    # Run the task
    process_due_installments()

    # Refresh installment from database
    installment.refresh_from_db()

    # Should be marked as failed with grace period
    assert installment.status == InstallmentSchedule.Status.FAILED
    assert installment.failure_reason == "PostFinance transaction state: FAILED"
    assert installment.grace_period_ends is not None


@pytest.mark.django_db
def test_retry_failed_installments_success(env, monkeypatch):
    """Test successful retry of failed installments."""
    event, order = env

    # Create a failed installment with grace period
    grace_period_ends = now() + timedelta(days=2)  # Still in grace period
    installment = InstallmentSchedule.objects.create(
        order=order,
        installment_number=2,
        amount=Decimal("50.00"),
        due_date=date.today() - timedelta(days=1),  # Was due yesterday
        status=InstallmentSchedule.Status.FAILED,
        token_id="test-token-123",
        failure_reason="Initial failure",
        grace_period_ends=grace_period_ends,
        num_installments=3,
    )

    # Mock successful token charge
    mock_transaction = MagicMock()
    mock_transaction.id = 999888
    mock_transaction.state = TransactionState.COMPLETED

    def mock_charge_token(**kwargs):
        return mock_transaction

    monkeypatch.setattr(
        "pretix_postfinance.api.PostFinanceClient.charge_token",
        mock_charge_token,
    )

    # Mock email sending
    monkeypatch.setattr(
        "pretix_postfinance.tasks.send_mail",
        lambda *args, **kwargs: None,
    )

    # Run the task
    retry_failed_installments()

    # Refresh installment from database
    installment.refresh_from_db()

    # Should be marked as paid
    assert installment.status == InstallmentSchedule.Status.PAID
    assert installment.paid_at is not None
    assert installment.failure_reason == ""
    assert installment.grace_period_ends is None


@pytest.mark.django_db
def test_retry_failed_installments_expired_grace_period(env, monkeypatch):
    """Test that expired grace periods are not retried."""
    event, order = env

    # Create a failed installment with expired grace period
    grace_period_ends = now() - timedelta(days=1)  # Already expired
    installment = InstallmentSchedule.objects.create(
        order=order,
        installment_number=2,
        amount=Decimal("50.00"),
        due_date=date.today() - timedelta(days=5),  # Was due 5 days ago
        status=InstallmentSchedule.Status.FAILED,
        token_id="test-token-123",
        failure_reason="Initial failure",
        grace_period_ends=grace_period_ends,
        num_installments=3,
    )

    # Mock successful token charge (should not be called)
    def mock_charge_token(**kwargs):
        raise AssertionError("charge_token should not be called for expired grace periods")

    monkeypatch.setattr(
        "pretix_postfinance.api.PostFinanceClient.charge_token",
        mock_charge_token,
    )

    # Run the task
    retry_failed_installments()

    # Refresh installment from database
    installment.refresh_from_db()

    # Should still be failed (not retried)
    assert installment.status == InstallmentSchedule.Status.FAILED


@pytest.mark.django_db
def test_cancel_expired_grace_periods(env, monkeypatch):
    """Test cancellation of orders with expired grace periods."""
    event, order = env

    # Create a failed installment with expired grace period
    grace_period_ends = now() - timedelta(days=1)  # Already expired
    installment = InstallmentSchedule.objects.create(
        order=order,
        installment_number=2,
        amount=Decimal("50.00"),
        due_date=date.today() - timedelta(days=5),  # Was due 5 days ago
        status=InstallmentSchedule.Status.FAILED,
        token_id="test-token-123",
        failure_reason="Payment declined",
        grace_period_ends=grace_period_ends,
        num_installments=3,
    )

    # Create a paid installment (should be refunded)
    paid_installment = InstallmentSchedule.objects.create(
        order=order,
        installment_number=1,
        amount=Decimal("50.00"),
        due_date=date.today() - timedelta(days=30),  # Paid 30 days ago
        status=InstallmentSchedule.Status.PAID,
        paid_at=now() - timedelta(days=30),
        token_id="test-token-123",
        num_installments=3,
    )

    # Create a scheduled installment (should be cancelled)
    scheduled_installment = InstallmentSchedule.objects.create(
        order=order,
        installment_number=3,
        amount=Decimal("50.00"),
        due_date=date.today() + timedelta(days=30),  # Scheduled for future
        status=InstallmentSchedule.Status.SCHEDULED,
        token_id="test-token-123",
        num_installments=3,
    )

    # Mock refund execution
    def mock_execute_refund(refund, user):
        refund.state = "done"
        refund.save()

    # Mock email sending
    monkeypatch.setattr(
        "pretix_postfinance.tasks.send_mail",
        lambda *args, **kwargs: None,
    )

    # Run the task
    cancel_expired_grace_periods()

    # Refresh all installments from database
    installment.refresh_from_db()
    paid_installment.refresh_from_db()
    scheduled_installment.refresh_from_db()

    # Failed installment should still be failed (not changed by this task)
    assert installment.status == InstallmentSchedule.Status.FAILED

    # Scheduled installment should be cancelled
    assert scheduled_installment.status == InstallmentSchedule.Status.CANCELLED

    # Paid installment should still be paid (refund handled separately)
    assert paid_installment.status == InstallmentSchedule.Status.PAID


@pytest.mark.django_db
def test_send_installment_reminders(env, monkeypatch):
    """Test sending of installment reminders."""
    event, order = env

    # Create an installment due in 3 days
    reminder_date = date.today() + timedelta(days=3)
    installment = InstallmentSchedule.objects.create(
        order=order,
        installment_number=2,
        amount=Decimal("50.00"),
        due_date=reminder_date,
        status=InstallmentSchedule.Status.SCHEDULED,
        token_id="test-token-123",
        num_installments=3,
    )

    # Mock email sending
    sent_emails = []

    def mock_send_mail(subject, message, from_email, recipient_list, **kwargs):
        sent_emails.append({
            "subject": subject,
            "message": message,
            "to": recipient_list,
        })

    monkeypatch.setattr(
        "pretix_postfinance.tasks.send_mail",
        mock_send_mail,
    )

    # Run the task
    send_installment_reminders()

    # Check that email was sent
    assert len(sent_emails) == 1
    email = sent_emails[0]
    assert "Installment Payment Reminder" in email["subject"]
    assert order.email in email["to"]
    assert "50.00" in email["message"]
    assert "3 days" in email["message"]

    # Check that reminder was marked as sent
    installment.refresh_from_db()
    assert "reminder_sent" in installment.failure_reason


# ============================================================================
# Partial Refund Handling Tests
# ============================================================================


@pytest.mark.django_db
def test_installment_refund_full_refund(env, monkeypatch):
    """Test full refund of installment payments."""
    event, order = env

    # Set order to paid status
    order.status = Order.STATUS_PAID
    order.save()

    # Create installment schedule
    installments = []
    for i in range(1, 4):  # 3 installments
        status = InstallmentSchedule.Status.PAID if i <= 2 else InstallmentSchedule.Status.SCHEDULED
        paid_at = now() - timedelta(days=30 * (3 - i)) if i <= 2 else None

        due_date = (
            date.today() - timedelta(days=30 * (3 - i))
            if i <= 2
            else date.today() + timedelta(days=30)
        )
        installment = InstallmentSchedule.objects.create(
            order=order,
            installment_number=i,
            amount=Decimal("33.34"),
            due_date=due_date,
            status=status,
            paid_at=paid_at,
            token_id="test-token-123",
            num_installments=3,
        )
        installments.append(installment)

    # Create payment
    payment = order.payments.create(
        provider="postfinance",
        amount=Decimal("100.00"),
        info=json.dumps({
            "transaction_id": 123456,
            "state": TransactionState.COMPLETED.value,
            "installment_schedule": {
                "num_installments": 3,
                "token_id": "test-token-123",
            },
        }),
    )

    # Create refund for full amount
    refund = order.refunds.create(
        provider="postfinance",
        amount=Decimal("100.00"),
        payment=payment,
    )

    # Mock refund execution
    mock_refund = MagicMock()
    mock_refund.id = 789012
    mock_refund.state = MagicMock()
    mock_refund.state.value = "SUCCESSFUL"
    mock_refund.amount = 100.00

    def mock_refund_transaction(**kwargs):
        return mock_refund

    monkeypatch.setattr(
        "pretix_postfinance.api.PostFinanceClient.refund_transaction",
        mock_refund_transaction,
    )

    # Execute refund
    prov = PostFinancePaymentProvider(event)
    prov.execute_refund(refund)

    # Check that scheduled installment was cancelled
    scheduled_installment = InstallmentSchedule.objects.get(installment_number=3)
    assert scheduled_installment.status == InstallmentSchedule.Status.CANCELLED


@pytest.mark.django_db
def test_installment_refund_partial_refund(env, monkeypatch):
    """Test partial refund of installment payments."""
    event, order = env

    # Set order to paid status
    order.status = Order.STATUS_PAID
    order.save()

    # Create installment schedule
    installments = []
    for i in range(1, 4):  # 3 installments
        status = InstallmentSchedule.Status.PAID if i <= 2 else InstallmentSchedule.Status.SCHEDULED
        paid_at = now() - timedelta(days=30 * (3 - i)) if i <= 2 else None

        due_date = (
            date.today() - timedelta(days=30 * (3 - i))
            if i <= 2
            else date.today() + timedelta(days=30)
        )
        installment = InstallmentSchedule.objects.create(
            order=order,
            installment_number=i,
            amount=Decimal("33.34"),
            due_date=due_date,
            status=status,
            paid_at=paid_at,
            token_id="test-token-123",
            num_installments=3,
        )
        installments.append(installment)

    # Create payment
    payment = order.payments.create(
        provider="postfinance",
        amount=Decimal("100.00"),
        info=json.dumps({
            "transaction_id": 123456,
            "state": TransactionState.COMPLETED.value,
            "installment_schedule": {
                "num_installments": 3,
                "token_id": "test-token-123",
            },
        }),
    )

    # Create partial refund
    refund = order.refunds.create(
        provider="postfinance",
        amount=Decimal("50.00"),  # Partial refund
        payment=payment,
    )

    # Mock refund execution
    mock_refund = MagicMock()
    mock_refund.id = 789012
    mock_refund.state = MagicMock()
    mock_refund.state.value = "SUCCESSFUL"
    mock_refund.amount = 50.00

    def mock_refund_transaction(**kwargs):
        return mock_refund

    monkeypatch.setattr(
        "pretix_postfinance.api.PostFinanceClient.refund_transaction",
        mock_refund_transaction,
    )

    # Execute refund
    prov = PostFinancePaymentProvider(event)
    prov.execute_refund(refund)

    # Check that scheduled installment was adjusted
    scheduled_installment = InstallmentSchedule.objects.get(installment_number=3)
    assert scheduled_installment.status == InstallmentSchedule.Status.SCHEDULED  # Not cancelled

    # The scheduled installment should have been increased to account for the refund
    # Original: 33.34, after refund: should be higher
    assert scheduled_installment.amount > Decimal("33.34")


@pytest.mark.django_db
def test_installment_refund_over_refund_protection(env, monkeypatch):
    """Test that refund amount is capped at paid amount."""
    event, order = env

    # Set order to paid status
    order.status = Order.STATUS_PAID
    order.save()

    # Create installment schedule with only 2 paid installments
    installments = []
    for i in range(1, 4):  # 3 installments
        status = InstallmentSchedule.Status.PAID if i <= 2 else InstallmentSchedule.Status.SCHEDULED
        paid_at = now() - timedelta(days=30 * (3 - i)) if i <= 2 else None
        due_date = (
            date.today() - timedelta(days=30 * (3 - i))
            if i <= 2
            else date.today() + timedelta(days=30)
        )

        installment = InstallmentSchedule.objects.create(
            order=order,
            installment_number=i,
            amount=Decimal("33.34"),
            due_date=due_date,
            status=status,
            paid_at=paid_at,
            token_id="test-token-123",
            num_installments=3,
        )
        installments.append(installment)

    # Create payment
    payment = order.payments.create(
        provider="postfinance",
        amount=Decimal("100.00"),
        info=json.dumps({
            "transaction_id": 123456,
            "state": TransactionState.COMPLETED.value,
            "installment_schedule": {
                "num_installments": 3,
                "token_id": "test-token-123",
            },
        }),
    )

    # Try to refund more than paid amount (66.68 paid, trying to refund 100)
    refund = order.refunds.create(
        provider="postfinance",
        amount=Decimal("100.00"),  # More than paid
        payment=payment,
    )

    # Mock refund execution
    mock_refund = MagicMock()
    mock_refund.id = 789012
    mock_refund.state = MagicMock()
    mock_refund.state.value = "SUCCESSFUL"
    mock_refund.amount = 66.68  # Should be capped at paid amount

    def mock_refund_transaction(**kwargs):
        return mock_refund

    monkeypatch.setattr(
        "pretix_postfinance.api.PostFinanceClient.refund_transaction",
        mock_refund_transaction,
    )

    # Execute refund
    prov = PostFinancePaymentProvider(event)
    prov.execute_refund(refund)

    # Check that refund was capped at paid amount
    refund.refresh_from_db()
    assert refund.amount == Decimal("66.68")  # Should be capped


# ============================================================================
# Integration Tests
# ============================================================================


@pytest.mark.django_db
def test_installment_payment_flow_integration(env, factory, monkeypatch):
    """Test the complete installment payment flow."""
    event, order = env

    # Enable installments
    event.settings.set("payment_postfinance_installments_enabled", True)
    event.settings.set("payment_postfinance_installments_min_amount", Decimal("50.00"))

    # Mock transaction with token
    mock_transaction = MagicMock()
    mock_transaction.id = 123456
    mock_transaction.state = TransactionState.COMPLETED
    mock_transaction.token = MagicMock()
    mock_transaction.token.id = 789

    def get_transaction(transaction_id):
        return mock_transaction

    monkeypatch.setattr(
        "pretix_postfinance.payment.PostFinanceClient.get_transaction",
        lambda self, tid: get_transaction(tid),
    )

    # Create payment with installment session data
    prov = PostFinancePaymentProvider(event)
    req = factory.post("/")
    req.session = {
        "payment_postfinance_transaction_id": 123456,
        "payment_postfinance_num_installments": 3,
    }

    payment = order.payments.create(provider="postfinance", amount=order.total)
    prov.execute_payment(req, payment)

    # Check that installment schedule was created
    schedule = InstallmentSchedule.objects.filter(order=order).order_by("installment_number")
    assert schedule.count() == 3

    # First installment should be paid
    assert schedule[0].status == InstallmentSchedule.Status.PAID
    assert schedule[0].payment == payment

    # Remaining installments should be scheduled
    assert schedule[1].status == InstallmentSchedule.Status.SCHEDULED
    assert schedule[2].status == InstallmentSchedule.Status.SCHEDULED

    # Check that token is stored on future installments
    assert schedule[1].token_id == "789"
    assert schedule[2].token_id == "789"


@pytest.mark.django_db
def test_installment_payment_flow_without_token(env, factory, monkeypatch):
    """Test installment payment flow when no token is available."""
    event, order = env

    # Enable installments
    event.settings.set("payment_postfinance_installments_enabled", True)

    # Mock transaction without token
    mock_transaction = MagicMock()
    mock_transaction.id = 123456
    mock_transaction.state = TransactionState.COMPLETED
    mock_transaction.token = None  # No token

    def get_transaction(transaction_id):
        return mock_transaction

    monkeypatch.setattr(
        "pretix_postfinance.payment.PostFinanceClient.get_transaction",
        lambda self, tid: get_transaction(tid),
    )

    # Create payment with installment session data
    prov = PostFinancePaymentProvider(event)
    req = factory.post("/")
    req.session = {
        "payment_postfinance_transaction_id": 123456,
        "payment_postfinance_num_installments": 3,
    }

    payment = order.payments.create(provider="postfinance", amount=order.total)
    prov.execute_payment(req, payment)

    # Check that no installment schedule was created (no token)
    schedule = InstallmentSchedule.objects.filter(order=order)
    assert schedule.count() == 0


@pytest.mark.django_db
def test_installment_payment_flow_invalid_num_installments(env, factory, monkeypatch):
    """Test installment payment flow with invalid num_installments."""
    event, order = env

    # Enable installments
    event.settings.set("payment_postfinance_installments_enabled", True)

    # Mock transaction with token
    mock_transaction = MagicMock()
    mock_transaction.id = 123456
    mock_transaction.state = TransactionState.COMPLETED
    mock_transaction.token = MagicMock()
    mock_transaction.token.id = 789

    def get_transaction(transaction_id):
        return mock_transaction

    monkeypatch.setattr(
        "pretix_postfinance.payment.PostFinanceClient.get_transaction",
        lambda self, tid: get_transaction(tid),
    )

    # Create payment with invalid installment count (13 > max 12)
    prov = PostFinancePaymentProvider(event)
    req = factory.post("/")
    req.session = {
        "payment_postfinance_transaction_id": 123456,
        "payment_postfinance_num_installments": 13,  # Invalid
    }

    payment = order.payments.create(provider="postfinance", amount=order.total)
    prov.execute_payment(req, payment)

    # Check that no installment schedule was created (invalid count)
    schedule = InstallmentSchedule.objects.filter(order=order)
    assert schedule.count() == 0
