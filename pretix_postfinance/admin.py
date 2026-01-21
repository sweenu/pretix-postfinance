from __future__ import annotations

from django.contrib import admin

from .models import InstallmentPlan, InstallmentSchedule


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


@admin.register(InstallmentSchedule)
class InstallmentScheduleAdmin(admin.ModelAdmin):
    """Admin interface for InstallmentSchedule model."""

    list_display = [
        "order",
        "installment_number",
        "amount",
        "due_date",
        "status",
        "paid_at",
    ]
    list_filter = ["status", "due_date"]
    search_fields = ["order__code", "token_id"]
    ordering = ["order", "installment_number"]
    readonly_fields = ["paid_at"]
