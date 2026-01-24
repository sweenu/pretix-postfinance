from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from django.db import models
from django.utils.translation import gettext_lazy as _

if TYPE_CHECKING:
    pass


class InstallmentSchedule(models.Model):
    """
    Tracks individual installment payments for orders using the installment payment feature.

    Each installment represents a scheduled payment that will be automatically charged
    to the customer's saved payment method.
    """

    class Status(models.TextChoices):
        SCHEDULED = "scheduled", _("Scheduled")
        PENDING = "pending", _("Pending")
        PAID = "paid", _("Paid")
        FAILED = "failed", _("Failed")
        CANCELLED = "cancelled", _("Cancelled")

    # Foreign key to the order this installment belongs to
    order = models.ForeignKey(
        "pretixbase.Order",
        on_delete=models.CASCADE,
        related_name="installment_schedule",
        verbose_name=_("Order"),
    )

    # Foreign key to the OrderPayment that represents this installment payment
    # Null for scheduled/failed/cancelled installments
    payment = models.ForeignKey(
        "pretixbase.OrderPayment",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="installment_schedule",
        verbose_name=_("Payment"),
    )

    # The installment number (1-based)
    installment_number = models.PositiveIntegerField(
        verbose_name=_("Installment Number")
    )

    # The amount of this installment
    amount = models.DecimalField(
        verbose_name=_("Amount"),
        max_digits=10,
        decimal_places=2,
        help_text=_("Amount of this installment")
    )

    # The due date for this installment
    due_date = models.DateField(
        verbose_name=_("Due Date"),
        help_text=_("Date when this installment is due")
    )

    # The status of this installment
    status = models.CharField(
        verbose_name=_("Status"),
        max_length=20,
        choices=Status.choices,
        default=Status.SCHEDULED,
        help_text=_("Current status of this installment")
    )

    # When the installment was paid (if applicable)
    paid_at = models.DateTimeField(
        verbose_name=_("Paid At"),
        null=True,
        blank=True,
        help_text=_("Date and time when this installment was paid")
    )

    # The token ID for charging this installment
    token_id = models.CharField(
        verbose_name=_("Token ID"),
        max_length=255,
        null=True,
        blank=True,
        help_text=_("PostFinance token ID for charging this installment")
    )

    # Reason for failure (if applicable)
    failure_reason = models.TextField(
        verbose_name=_("Failure Reason"),
        null=True,
        blank=True,
        help_text=_("Reason for payment failure")
    )

    # When the grace period ends for failed installments
    grace_period_ends = models.DateTimeField(
        verbose_name=_("Grace Period Ends"),
        null=True,
        blank=True,
        help_text=_("Date and time when grace period ends for failed installments")
    )

    # Total number of installments for this order
    num_installments = models.PositiveIntegerField(
        verbose_name=_("Total Installments"),
        help_text=_("Total number of installments for this order")
    )

    class Meta:
        verbose_name = _("Installment Schedule")
        verbose_name_plural = _("Installment Schedules")

        # Ensure each installment number is unique per order
        unique_together: ClassVar[list[str]] = ["order", "installment_number"]

        # Add indexes for efficient querying
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["due_date"]),
            models.Index(fields=["status"]),
            models.Index(fields=["order", "status"]),
        ]

    def __str__(self) -> str:
        return f"Installment {self.installment_number} for Order {self.order.code}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        """
        Validate that num_installments is between 2 and 12.
        """
        if self.num_installments and (self.num_installments < 2 or self.num_installments > 12):
            raise ValueError("num_installments must be between 2 and 12")

        super().save(*args, **kwargs)
