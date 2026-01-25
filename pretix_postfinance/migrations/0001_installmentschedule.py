import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """
    Initial migration for InstallmentSchedule model.
    """

    initial = True

    dependencies = [
        # This migration depends on pretix base models being available
        # We don't specify exact dependencies as pretix handles that
    ]

    operations = [
        migrations.CreateModel(
            name="InstallmentSchedule",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("installment_number", models.PositiveIntegerField(verbose_name="Installment Number")),
                ("amount", models.DecimalField(decimal_places=2, help_text="Amount of this installment", max_digits=10, verbose_name="Amount")),
                ("due_date", models.DateField(help_text="Date when this installment is due", verbose_name="Due Date")),
                ("status", models.CharField(choices=[("scheduled", "Scheduled"), ("pending", "Pending"), ("paid", "Paid"), ("failed", "Failed"), ("cancelled", "Cancelled")], default="scheduled", help_text="Current status of this installment", max_length=20, verbose_name="Status")),
                ("paid_at", models.DateTimeField(blank=True, help_text="Date and time when this installment was paid", null=True, verbose_name="Paid At")),
                ("token_id", models.CharField(blank=True, help_text="PostFinance token ID for charging this installment", max_length=255, null=True, verbose_name="Token ID")),
                ("failure_reason", models.TextField(blank=True, help_text="Reason for payment failure", null=True, verbose_name="Failure Reason")),
                ("grace_period_ends", models.DateTimeField(blank=True, help_text="Date and time when grace period ends for failed installments", null=True, verbose_name="Grace Period Ends")),
                ("num_installments", models.PositiveIntegerField(help_text="Total number of installments for this order", verbose_name="Total Installments")),
                ("order", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="installment_schedule", to="pretixbase.order", verbose_name="Order")),
                ("payment", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="installment_schedule", to="pretixbase.orderpayment", verbose_name="Payment")),
            ],
            options={
                "verbose_name": "Installment Schedule",
                "verbose_name_plural": "Installment Schedules",
                "unique_together": {("order", "installment_number")},
                "indexes": [models.Index(fields=["due_date"], name="pretix_postf_due_dat_6a1b2e_idx"), models.Index(fields=["status"], name="pretix_postf_status_7c8f9a_idx"), models.Index(fields=["order", "status"], name="pretix_postf_order_i_8b2c1d_idx")],
            },
        ),
    ]
