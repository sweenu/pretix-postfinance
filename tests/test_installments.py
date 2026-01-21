"""
Tests for pretix_postfinance.installments module.
"""

from datetime import date
from decimal import Decimal

import pytest

from pretix_postfinance.installments import (
    calculate_installment_schedule,
    get_max_installments,
    validate_schedule_against_event,
)


class TestGetMaxInstallments:
    """Tests for get_max_installments function."""

    def test_returns_system_max_when_plenty_of_time(self):
        """Should return 12 when there's more than enough time until event."""
        event_date = date(2026, 12, 31)
        start_date = date(2026, 1, 1)
        result = get_max_installments(event_date, start_date)
        assert result == 12

    def test_limited_by_event_date(self):
        """Should limit installments based on time until event minus 30 days."""
        # Event in 4 months, so last payment must be 3 months out
        # With 30-day installments: can fit 3 payments (at 0, 30, 60 days)
        # But need 30 days before event, so 120 days - 30 grace = 90 days available
        # 90 days / 30 = 3 installments possible
        event_date = date(2026, 5, 1)
        start_date = date(2026, 1, 1)  # 120 days until event
        result = get_max_installments(event_date, start_date)
        assert result == 3

    def test_respects_organizer_maximum(self):
        """Should respect organizer's maximum when lower than calculated max."""
        event_date = date(2026, 12, 31)
        start_date = date(2026, 1, 1)
        result = get_max_installments(event_date, start_date, organizer_max=6)
        assert result == 6

    def test_organizer_max_does_not_increase_time_limit(self):
        """Organizer max cannot increase beyond time-based limit."""
        event_date = date(2026, 5, 1)
        start_date = date(2026, 1, 1)
        # Time allows 3, organizer wants 6, should get 3
        result = get_max_installments(event_date, start_date, organizer_max=6)
        assert result == 3

    def test_minimum_one_installment(self):
        """Should return at least 1 even with very short time."""
        event_date = date(2026, 2, 15)
        start_date = date(2026, 2, 1)  # Only 14 days until event
        result = get_max_installments(event_date, start_date)
        assert result == 1

    def test_exactly_30_days_before_event(self):
        """Should handle case where start is exactly 30 days before event."""
        event_date = date(2026, 2, 28)
        start_date = date(2026, 1, 29)  # Exactly 30 days before
        result = get_max_installments(event_date, start_date)
        assert result == 1

    def test_with_organizer_max_none(self):
        """Should work correctly when organizer_max is explicitly None."""
        event_date = date(2026, 12, 31)
        start_date = date(2026, 1, 1)
        result = get_max_installments(event_date, start_date, organizer_max=None)
        assert result == 12


class TestCalculateInstallmentSchedule:
    """Tests for calculate_installment_schedule function."""

    def test_single_installment(self):
        """Should create single installment with full amount."""
        total = Decimal("100.00")
        start = date(2026, 1, 1)
        schedule = calculate_installment_schedule(total, 1, start)

        assert len(schedule) == 1
        assert schedule[0] == (1, Decimal("100.00"), date(2026, 1, 1))

    def test_equal_installments_no_rounding(self):
        """Should create equal installments when amount divides evenly."""
        total = Decimal("300.00")
        start = date(2026, 1, 1)
        schedule = calculate_installment_schedule(total, 3, start)

        assert len(schedule) == 3
        assert schedule[0] == (1, Decimal("100.00"), date(2026, 1, 1))
        assert schedule[1] == (2, Decimal("100.00"), date(2026, 1, 31))
        assert schedule[2] == (3, Decimal("100.00"), date(2026, 3, 2))

        # Verify total
        total_paid = sum(amount for _, amount, _ in schedule)
        assert total_paid == total

    def test_handles_rounding_in_last_installment(self):
        """Should adjust last installment to handle rounding."""
        total = Decimal("100.00")
        start = date(2026, 1, 1)
        schedule = calculate_installment_schedule(total, 3, start)

        assert len(schedule) == 3
        # First two get 33.33 each, last gets 33.34
        assert schedule[0][1] == Decimal("33.33")
        assert schedule[1][1] == Decimal("33.33")
        assert schedule[2][1] == Decimal("33.34")

        # Verify total is exact
        total_paid = sum(amount for _, amount, _ in schedule)
        assert total_paid == total

    def test_installment_dates_30_days_apart(self):
        """Should create installments 30 days apart."""
        total = Decimal("100.00")
        start = date(2026, 1, 1)
        schedule = calculate_installment_schedule(total, 4, start)

        assert schedule[0][2] == date(2026, 1, 1)  # Start date
        assert schedule[1][2] == date(2026, 1, 31)  # +30 days
        assert schedule[2][2] == date(2026, 3, 2)  # +60 days
        assert schedule[3][2] == date(2026, 4, 1)  # +90 days

    def test_installment_numbers_are_one_based(self):
        """Should use 1-based installment numbers."""
        total = Decimal("100.00")
        start = date(2026, 1, 1)
        schedule = calculate_installment_schedule(total, 5, start)

        numbers = [num for num, _, _ in schedule]
        assert numbers == [1, 2, 3, 4, 5]

    def test_raises_on_invalid_num_installments(self):
        """Should raise ValueError if num_installments < 1."""
        with pytest.raises(ValueError, match="at least 1"):
            calculate_installment_schedule(Decimal("100.00"), 0, date(2026, 1, 1))

        with pytest.raises(ValueError, match="at least 1"):
            calculate_installment_schedule(Decimal("100.00"), -1, date(2026, 1, 1))

    def test_raises_on_invalid_total_amount(self):
        """Should raise ValueError if total_amount <= 0."""
        with pytest.raises(ValueError, match="must be positive"):
            calculate_installment_schedule(Decimal("0.00"), 3, date(2026, 1, 1))

        with pytest.raises(ValueError, match="must be positive"):
            calculate_installment_schedule(Decimal("-10.00"), 3, date(2026, 1, 1))

    def test_large_number_of_installments(self):
        """Should handle 12 installments correctly."""
        total = Decimal("1200.00")
        start = date(2026, 1, 1)
        schedule = calculate_installment_schedule(total, 12, start)

        assert len(schedule) == 12
        # Verify total is exact
        total_paid = sum(amount for _, amount, _ in schedule)
        assert total_paid == total

    def test_complex_rounding_case(self):
        """Should handle complex rounding with multiple decimal places."""
        total = Decimal("99.99")
        start = date(2026, 1, 1)
        schedule = calculate_installment_schedule(total, 7, start)

        # First 6 get 14.28, last gets 14.31
        for i in range(6):
            assert schedule[i][1] == Decimal("14.28")
        assert schedule[6][1] == Decimal("14.31")

        # Verify total is exact
        total_paid = sum(amount for _, amount, _ in schedule)
        assert total_paid == total


class TestValidateScheduleAgainstEvent:
    """Tests for validate_schedule_against_event function."""

    def test_valid_schedule_with_grace_period(self):
        """Should return True when last installment is >= 30 days before event."""
        schedule = [
            (1, Decimal("50.00"), date(2026, 1, 1)),
            (2, Decimal("50.00"), date(2026, 1, 31)),
        ]
        event_date = date(2026, 3, 2)  # 30 days after last installment
        assert validate_schedule_against_event(schedule, event_date) is True

    def test_valid_schedule_exactly_30_days(self):
        """Should return True when last installment is exactly 30 days before event."""
        schedule = [
            (1, Decimal("100.00"), date(2026, 1, 1)),
        ]
        event_date = date(2026, 1, 31)  # Exactly 30 days after
        assert validate_schedule_against_event(schedule, event_date) is True

    def test_invalid_schedule_too_close_to_event(self):
        """Should return False when last installment is < 30 days before event."""
        schedule = [
            (1, Decimal("50.00"), date(2026, 1, 1)),
            (2, Decimal("50.00"), date(2026, 1, 31)),
        ]
        event_date = date(2026, 2, 15)  # Only 15 days after last installment
        assert validate_schedule_against_event(schedule, event_date) is False

    def test_invalid_schedule_on_event_day(self):
        """Should return False when last installment is on event day."""
        schedule = [
            (1, Decimal("100.00"), date(2026, 1, 1)),
        ]
        event_date = date(2026, 1, 1)  # Same day
        assert validate_schedule_against_event(schedule, event_date) is False

    def test_invalid_empty_schedule(self):
        """Should return False for empty schedule."""
        schedule = []
        event_date = date(2026, 1, 1)
        assert validate_schedule_against_event(schedule, event_date) is False

    def test_valid_schedule_with_multiple_installments(self):
        """Should validate based on last installment only."""
        schedule = [
            (1, Decimal("25.00"), date(2026, 1, 1)),
            (2, Decimal("25.00"), date(2026, 1, 31)),
            (3, Decimal("25.00"), date(2026, 3, 2)),
            (4, Decimal("25.00"), date(2026, 4, 1)),
        ]
        event_date = date(2026, 5, 1)  # 30 days after last installment
        assert validate_schedule_against_event(schedule, event_date) is True

    def test_valid_with_extra_grace_period(self):
        """Should return True when there's more than 30 days grace period."""
        schedule = [
            (1, Decimal("100.00"), date(2026, 1, 1)),
        ]
        event_date = date(2026, 3, 1)  # 59 days after (well beyond 30)
        assert validate_schedule_against_event(schedule, event_date) is True
