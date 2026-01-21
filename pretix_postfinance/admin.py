from __future__ import annotations

from django.contrib import admin

from .models import InstallmentPlan


@admin.register(InstallmentPlan)
class InstallmentPlanAdmin(admin.ModelAdmin):
    """Admin interface for InstallmentPlan model."""

    list_display = [
        "name",
        "event",
        "num_installments",
        "min_amount",
        "max_installments_override",
        "enabled",
    ]
    list_filter = ["enabled", "num_installments"]
    search_fields = ["name", "event__name"]
    ordering = ["event", "num_installments", "name"]
