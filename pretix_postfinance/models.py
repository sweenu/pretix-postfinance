from __future__ import annotations

from decimal import Decimal
from typing import ClassVar

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _


class InstallmentStatus(models.TextChoices):
    """Status choices for installment payments."""

    SCHEDULED = "scheduled", _("Scheduled")
    PENDING = "pending", _("Pending")
    PAID = "paid", _("Paid")
    FAILED = "failed", _("Failed")
    CANCELLED = "cancelled", _("Cancelled")


class InstallmentPlan(models.Model):
    """
    Installment plan configuration for an event.

    Allows event organizers to offer payment in installments with
    automatic recurring charges through PostFinance tokenization.
    Installments are always monthly (30 days apart).
    """

    event = models.ForeignKey(
        "pretixbase.Event",
        on_delete=models.CASCADE,
        related_name="postfinance_installment_plans",
        verbose_name=_("Event"),
    )

    name = models.CharField(
        max_length=200,
        verbose_name=_("Plan name"),
        help_text=_("Descriptive name for this installment plan (e.g., '3 monthly payments')"),
    )

    num_installments = models.IntegerField(
        verbose_name=_("Number of installments"),
        validators=[MinValueValidator(2), MaxValueValidator(12)],
        help_text=_("Number of monthly installments (2-12)"),
    )

    min_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        verbose_name=_("Minimum order amount"),
        help_text=_("Minimum order total required to use this plan"),
        default=Decimal("0.00"),
    )

    max_installments_override = models.IntegerField(
        null=True,
        blank=True,
        verbose_name=_("Maximum installments override"),
        validators=[MinValueValidator(2), MaxValueValidator(12)],
        help_text=_(
            "Optional: Set a lower maximum number of installments than the system "
            "default (which is based on event date). Leave empty to use system default."
        ),
    )

    enabled = models.BooleanField(
        default=True,
        verbose_name=_("Enabled"),
        help_text=_("Whether this plan is available for customers to select"),
    )

    class Meta:
        verbose_name = _("Installment Plan")
        verbose_name_plural = _("Installment Plans")
        ordering: ClassVar[list[str]] = ["num_installments", "name"]

    def __str__(self) -> str:
        return self.name

    def clean(self) -> None:
        """Validate model fields."""
        super().clean()

        # Validate num_installments is between 2 and 12
        if self.num_installments is not None and not (2 <= self.num_installments <= 12):
            raise ValidationError({
                "num_installments": _("Number of installments must be between 2 and 12")
            })

        # Validate max_installments_override if set
        if self.max_installments_override is not None:
            if not (2 <= self.max_installments_override <= 12):
                raise ValidationError({
                    "max_installments_override": _(
                        "Maximum installments override must be between 2 and 12"
                    )
                })

            # Ensure max_installments_override >= num_installments
            if self.num_installments and self.max_installments_override < self.num_installments:
                raise ValidationError({
                    "max_installments_override": _(
                        "Maximum installments override cannot be less than "
                        "the plan's number of installments"
                    )
                })


class InstallmentSchedule(models.Model):
    """
    Individual installment payment schedule for an order.

    Tracks each installment payment in a multi-payment order,
    including status, due dates, and payment processing details.
    """

    order = models.ForeignKey(
        "pretixbase.Order",
        on_delete=models.CASCADE,
        related_name="postfinance_installments",
        verbose_name=_("Order"),
    )

    payment = models.ForeignKey(
        "pretixbase.OrderPayment",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="postfinance_installment",
        verbose_name=_("Payment"),
        help_text=_("The OrderPayment record for this installment (if paid)"),
    )

    installment_number = models.IntegerField(
        verbose_name=_("Installment number"),
        help_text=_("Sequential number of this installment (1-based)"),
    )

    amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        verbose_name=_("Amount"),
        help_text=_("Amount to charge for this installment"),
    )

    due_date = models.DateField(
        verbose_name=_("Due date"),
        help_text=_("Date when this installment payment is due"),
        db_index=True,
    )

    status = models.CharField(
        max_length=20,
        choices=InstallmentStatus.choices,
        default=InstallmentStatus.SCHEDULED,
        verbose_name=_("Status"),
        db_index=True,
    )

    paid_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("Paid at"),
        help_text=_("Timestamp when this installment was successfully paid"),
    )

    token_id = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        verbose_name=_("Token ID"),
        help_text=_("PostFinance payment token ID for charging this installment"),
    )

    failure_reason = models.TextField(
        null=True,
        blank=True,
        verbose_name=_("Failure reason"),
        help_text=_("Reason for payment failure (if applicable)"),
    )

    grace_period_ends = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("Grace period ends"),
        help_text=_("End of grace period for failed payment (3 days after failure)"),
    )

    class Meta:
        verbose_name = _("Installment Schedule")
        verbose_name_plural = _("Installment Schedules")
        ordering: ClassVar[list[str]] = ["order", "installment_number"]
        unique_together: ClassVar[list[list[str]]] = [["order", "installment_number"]]
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["due_date"]),
            models.Index(fields=["status"]),
            models.Index(fields=["status", "due_date"]),
            models.Index(fields=["status", "grace_period_ends"]),
        ]

    def __str__(self) -> str:
        return f"{self.order.code} - Installment {self.installment_number}"
