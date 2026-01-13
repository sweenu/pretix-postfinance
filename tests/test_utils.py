"""
Tests for pretix_postfinance.utils module.
"""

from decimal import Decimal

import pytest

from pretix_postfinance.utils import (
    CURRENCY_DECIMAL_PLACES,
    SUPPORTED_CURRENCIES,
    amount_to_minor_units,
    minor_units_to_amount,
)


class TestSupportedCurrencies:
    """Tests for supported currencies constants."""

    def test_supported_currencies_contains_chf(self):
        """CHF should be in supported currencies."""
        assert "CHF" in SUPPORTED_CURRENCIES

    def test_supported_currencies_contains_eur(self):
        """EUR should be in supported currencies."""
        assert "EUR" in SUPPORTED_CURRENCIES

    def test_currency_decimal_places_chf(self):
        """CHF should have 2 decimal places."""
        assert CURRENCY_DECIMAL_PLACES["CHF"] == 2

    def test_currency_decimal_places_eur(self):
        """EUR should have 2 decimal places."""
        assert CURRENCY_DECIMAL_PLACES["EUR"] == 2


class TestAmountToMinorUnits:
    """Tests for amount_to_minor_units function."""

    def test_chf_whole_amount(self):
        """Convert whole CHF amount to centimes."""
        result = amount_to_minor_units(Decimal("100"), "CHF")
        assert result == 10000

    def test_chf_with_centimes(self):
        """Convert CHF amount with centimes."""
        result = amount_to_minor_units(Decimal("99.95"), "CHF")
        assert result == 9995

    def test_eur_whole_amount(self):
        """Convert whole EUR amount to cents."""
        result = amount_to_minor_units(Decimal("50"), "EUR")
        assert result == 5000

    def test_eur_with_cents(self):
        """Convert EUR amount with cents."""
        result = amount_to_minor_units(Decimal("49.99"), "EUR")
        assert result == 4999

    def test_zero_amount(self):
        """Convert zero amount."""
        result = amount_to_minor_units(Decimal("0"), "CHF")
        assert result == 0

    def test_small_amount(self):
        """Convert small amount (1 centime)."""
        result = amount_to_minor_units(Decimal("0.01"), "CHF")
        assert result == 1

    def test_string_input(self):
        """Accept string input and convert correctly."""
        result = amount_to_minor_units("25.50", "CHF")
        assert result == 2550

    def test_large_amount(self):
        """Convert large amount correctly."""
        result = amount_to_minor_units(Decimal("10000.00"), "CHF")
        assert result == 1000000


class TestMinorUnitsToAmount:
    """Tests for minor_units_to_amount function."""

    def test_chf_centimes_to_amount(self):
        """Convert centimes to CHF amount."""
        result = minor_units_to_amount(10000, "CHF")
        assert result == Decimal("100.00")

    def test_chf_with_centimes(self):
        """Convert centimes with fractional part."""
        result = minor_units_to_amount(9995, "CHF")
        assert result == Decimal("99.95")

    def test_eur_cents_to_amount(self):
        """Convert cents to EUR amount."""
        result = minor_units_to_amount(5000, "EUR")
        assert result == Decimal("50.00")

    def test_zero_minor_units(self):
        """Convert zero minor units."""
        result = minor_units_to_amount(0, "CHF")
        assert result == Decimal("0.00")

    def test_one_centime(self):
        """Convert single centime."""
        result = minor_units_to_amount(1, "CHF")
        assert result == Decimal("0.01")

    def test_result_has_correct_precision(self):
        """Result should have 2 decimal places."""
        result = minor_units_to_amount(100, "CHF")
        # Check string representation to verify decimal places
        assert str(result) == "1.00"


class TestRoundTrip:
    """Test converting to minor units and back."""

    @pytest.mark.parametrize(
        "amount",
        [
            Decimal("0.01"),
            Decimal("1.00"),
            Decimal("99.99"),
            Decimal("100.00"),
            Decimal("1234.56"),
        ],
    )
    def test_round_trip_chf(self, amount):
        """Converting to minor units and back should preserve value."""
        minor = amount_to_minor_units(amount, "CHF")
        result = minor_units_to_amount(minor, "CHF")
        assert result == amount

    @pytest.mark.parametrize(
        "amount",
        [
            Decimal("0.01"),
            Decimal("1.00"),
            Decimal("99.99"),
            Decimal("100.00"),
        ],
    )
    def test_round_trip_eur(self, amount):
        """Converting to minor units and back should preserve value for EUR."""
        minor = amount_to_minor_units(amount, "EUR")
        result = minor_units_to_amount(minor, "EUR")
        assert result == amount
