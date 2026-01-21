"""
Installment calculation utilities for pretix-postfinance.

Provides functions to calculate installment schedules for payment plans,
including determining maximum allowed installments based on event dates.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal


def get_max_installments(
    event_date: date,
    start_date: date,
    organizer_max: int | None = None,
) -> int:
    """
    Calculate maximum number of installments allowed for an event.

    The maximum is determined by:
    - System maximum: 12 installments
    - Time constraint: Last installment must be 30 days before event
    - Organizer override: Optional lower limit set by event organizer

    Args:
        event_date: The date of the event.
        start_date: The date when installment payments begin.
        organizer_max: Optional maximum set by organizer (must be <= 12).

    Returns:
        Maximum number of installments allowed (between 1 and 12).
    """
    SYSTEM_MAX = 12
    GRACE_PERIOD_DAYS = 30

    # Calculate how many months are available until event minus grace period
    # Each installment is 30 days apart, and last one must be 30 days before event
    latest_payment_date = event_date - timedelta(days=GRACE_PERIOD_DAYS)

    # Calculate days between start and latest payment date
    days_available = (latest_payment_date - start_date).days

    # Calculate number of installments that fit
    # First installment is on start_date (day 0), subsequent ones are every 30 days
    # Formula: (days_available - 1) // 30 + 1 accounts for the first payment on day 0
    # and ensures the last payment is strictly before the grace period boundary
    if days_available < 0:
        months_available = 1
    else:
        months_available = max(1, (days_available - 1) // 30 + 1)

    # Apply all constraints
    max_installments = min(SYSTEM_MAX, months_available)

    if organizer_max is not None:
        max_installments = min(max_installments, organizer_max)

    return max_installments


def calculate_installment_schedule(
    total_amount: Decimal,
    num_installments: int,
    start_date: date,
) -> list[tuple[int, Decimal, date]]:
    """
    Calculate an installment payment schedule.

    All installments have equal amounts except the last one, which is adjusted
    to ensure the total equals the original amount exactly (handles rounding).
    Installments are always 30 days apart.

    Args:
        total_amount: Total amount to be paid across all installments.
        num_installments: Number of installments (must be >= 1).
        start_date: Date of the first installment payment.

    Returns:
        List of tuples: (installment_number, amount, due_date)
        where installment_number is 1-based.

    Raises:
        ValueError: If num_installments < 1 or total_amount <= 0.
    """
    if num_installments < 1:
        raise ValueError("Number of installments must be at least 1")

    if total_amount <= 0:
        raise ValueError("Total amount must be positive")

    # Calculate base amount per installment (rounded down to 2 decimal places)
    base_amount = (total_amount / num_installments).quantize(Decimal("0.01"))

    schedule = []
    total_allocated = Decimal("0")

    for i in range(1, num_installments + 1):
        # Calculate due date (30 days per installment, first one is on start_date)
        due_date = start_date + timedelta(days=(i - 1) * 30)

        # Regular installment with base amount, or last installment adjusted for rounding
        amount = base_amount if i < num_installments else total_amount - total_allocated

        schedule.append((i, amount, due_date))
        total_allocated += amount

    return schedule


def validate_schedule_against_event(
    schedule: list[tuple[int, Decimal, date]],
    event_date: date,
) -> bool:
    """
    Validate that an installment schedule ends before the event grace period.

    The last installment must be due at least 30 days before the event date
    to allow time for payment processing and potential retry attempts.

    Args:
        schedule: List of (installment_number, amount, due_date) tuples.
        event_date: The date of the event.

    Returns:
        True if the schedule is valid (last installment is at least 30 days
        before event), False otherwise.
    """
    if not schedule:
        return False

    GRACE_PERIOD_DAYS = 30

    # Get the last installment's due date
    last_due_date = schedule[-1][2]

    # Calculate the latest acceptable payment date
    latest_payment_date = event_date - timedelta(days=GRACE_PERIOD_DAYS)

    # Schedule is valid if last payment is on or before the latest acceptable date
    return last_due_date <= latest_payment_date
