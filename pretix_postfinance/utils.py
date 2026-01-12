"""Utility functions for PostFinance payment plugin."""
from decimal import Decimal
from typing import Union

# Supported currencies with their decimal places
SUPPORTED_CURRENCIES = frozenset({"CHF", "EUR"})

# Decimal places for each currency (both CHF and EUR use 2 decimal places)
CURRENCY_DECIMAL_PLACES = {
    "CHF": 2,  # Swiss Franc - centimes
    "EUR": 2,  # Euro - cents
}


def amount_to_minor_units(amount: Union[Decimal, str], currency: str) -> int:
    """
    Convert a decimal amount to minor currency units (e.g., centimes/cents).

    Args:
        amount: The amount in major currency units (e.g., 10.50 CHF)
        currency: The ISO 4217 currency code (e.g., 'CHF', 'EUR')

    Returns:
        The amount in minor units as an integer (e.g., 1050 centimes)

    Raises:
        ValueError: If the currency is not supported

    Example:
        >>> amount_to_minor_units(Decimal("10.50"), "CHF")
        1050
    """
    if currency not in SUPPORTED_CURRENCIES:
        raise ValueError(
            f"Unsupported currency: {currency}. "
            f"Supported currencies: {', '.join(sorted(SUPPORTED_CURRENCIES))}"
        )

    # Convert to Decimal if string
    if isinstance(amount, str):
        amount = Decimal(amount)

    decimal_places = CURRENCY_DECIMAL_PLACES[currency]
    multiplier = Decimal(10) ** decimal_places

    # Use quantize to ensure proper rounding and conversion
    minor_units = amount * multiplier
    return int(minor_units.to_integral_value())


def minor_units_to_amount(minor_units: int, currency: str) -> Decimal:
    """
    Convert minor currency units to a decimal amount.

    Args:
        minor_units: The amount in minor units (e.g., 1050 centimes)
        currency: The ISO 4217 currency code (e.g., 'CHF', 'EUR')

    Returns:
        The amount in major currency units as a Decimal (e.g., Decimal("10.50"))

    Raises:
        ValueError: If the currency is not supported

    Example:
        >>> minor_units_to_amount(1050, "CHF")
        Decimal('10.50')
    """
    if currency not in SUPPORTED_CURRENCIES:
        raise ValueError(
            f"Unsupported currency: {currency}. "
            f"Supported currencies: {', '.join(sorted(SUPPORTED_CURRENCIES))}"
        )

    decimal_places = CURRENCY_DECIMAL_PLACES[currency]
    divisor = Decimal(10) ** decimal_places

    # Convert to Decimal and divide to get the amount
    result = Decimal(minor_units) / divisor

    # Quantize to ensure proper decimal places
    quantize_str = "0." + "0" * decimal_places
    return result.quantize(Decimal(quantize_str))
