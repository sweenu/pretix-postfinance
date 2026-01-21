from __future__ import annotations

from decimal import Decimal
from typing import ClassVar

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _


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
