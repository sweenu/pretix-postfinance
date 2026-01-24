from __future__ import annotations

from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal


def get_max_installments(
    event_date: date, start_date: date, organizer_max: int | None = None
) -> int:
    """
    Calculate the maximum number of installments allowed for an order.

    Args:
        event_date: The date of the event.
        start_date: The date when the first installment would be charged (usually today).
        organizer_max: Optional maximum number of installments configured by the organizer.
                      If None, defaults to 12.

    Returns:
        The maximum number of installments allowed, calculated as:
        min(12, months_until_event_minus_30_days, organizer_max or 12)

        This ensures that all installments can be completed at least 30 days before the event.
    """
    # Calculate the number of days between start_date and event_date
    days_until_event = (event_date - start_date).days

    # We need all installments to be completed at least 30 days before the event
    # Installments are monthly (30 days apart), so we calculate how many 30-day periods
    # fit into (days_until_event - 30)
    max_by_event_date = max(0, (days_until_event - 30) // 30)

    # The maximum is the minimum of:
    # 1. System maximum (12)
    # 2. Maximum allowed by event date timing
    # 3. Organizer's configured maximum (if provided)
    system_max = 12
    organizer_max = organizer_max or system_max

    return min(system_max, max_by_event_date, organizer_max)


def calculate_installment_schedule(
    total_amount: Decimal,
    num_installments: int,
    start_date: date,
) -> list[tuple[int, Decimal, date]]:
    """
    Calculate an installment schedule with equal monthly payments.

    Args:
        total_amount: The total amount to be paid in installments.
        num_installments: The number of installments (2-12).
        start_date: The date of the first installment.

    Returns:
        A list of tuples containing (installment_number, amount, due_date).

        All installments have equal amounts except possibly the last one,
        which is adjusted to ensure the total equals the original amount exactly.

        Installments are scheduled monthly (30 days apart).
    """
    if num_installments < 2 or num_installments > 12:
        raise ValueError("num_installments must be between 2 and 12")

    if total_amount <= Decimal("0"):
        raise ValueError("total_amount must be positive")

    # Calculate the base amount for each installment
    base_amount = total_amount / Decimal(num_installments)

    # Round to 2 decimal places using banker's rounding
    base_amount_rounded = base_amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    schedule = []
    current_date = start_date
    remaining_amount = total_amount

    # Create all installments except the last one
    for i in range(1, num_installments):
        installment_amount = base_amount_rounded
        schedule.append((i, installment_amount, current_date))
        remaining_amount -= installment_amount
        current_date += timedelta(days=30)

    # The last installment gets the remaining amount to ensure exact total
    last_installment_amount = remaining_amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    schedule.append((num_installments, last_installment_amount, current_date))

    return schedule


def validate_schedule_against_event(
    schedule: list[tuple[int, Decimal, date]],
    event_date: date,
) -> bool:
    """
    Validate that an installment schedule ends at least 30 days before the event.

    Args:
        schedule: The installment schedule as returned by calculate_installment_schedule.
        event_date: The date of the event.

    Returns:
        True if the last installment is due at least 30 days before the event,
        False otherwise.
    """
    if not schedule:
        return False

    # Get the due date of the last installment
    last_installment_date = schedule[-1][2]

    # Check if it's at least 30 days before the event
    return (event_date - last_installment_date).days >= 30

