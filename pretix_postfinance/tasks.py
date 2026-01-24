from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from celery import shared_task
from django.core.mail import send_mail
from django.utils.timezone import now

from .api import PostFinanceError
from .models import InstallmentSchedule

logger = logging.getLogger(__name__)


@shared_task
def cancel_expired_grace_periods() -> None:
    """
    Cancel orders when grace period expires without payment.

    This task runs daily to handle installments where the grace period
    has expired without successful payment.
    """
    logger.info("Starting cancel_expired_grace_periods task")

    # Get failed installments where grace period has expired
    now_time = now()
    expired_installments = InstallmentSchedule.objects.filter(
        status=InstallmentSchedule.Status.FAILED,
        grace_period_ends__lte=now_time,
    ).select_related("order")

    logger.info("Found %s expired installments to process", expired_installments.count())

    # Group by order to process all installments for each order together
    orders_to_cancel = {}
    for installment in expired_installments:
        order_id = installment.order.id
        if order_id not in orders_to_cancel:
            orders_to_cancel[order_id] = []
        orders_to_cancel[order_id].append(installment)

    for order_id, installments in orders_to_cancel.items():
        try:
            order = installments[0].order
            event = order.event

            logger.info(
                "Processing order %s with %s expired installments",
                order.code,
                len(installments),
            )

            # Cancel all remaining scheduled installments for this order
            remaining_installments = InstallmentSchedule.objects.filter(
                order=order,
                status__in=[
                    InstallmentSchedule.Status.SCHEDULED,
                    InstallmentSchedule.Status.FAILED,
                ],
            )

            for remaining_installment in remaining_installments:
                remaining_installment.status = InstallmentSchedule.Status.CANCELLED
                remaining_installment.save()

            # Process refunds for paid installments
            paid_installments = InstallmentSchedule.objects.filter(
                order=order,
                status=InstallmentSchedule.Status.PAID,
            ).order_by("installment_number")

            total_paid = sum(
                float(installment.amount) for installment in paid_installments
            )

            if total_paid > 0:
                # Create refund for all paid installments
                from pretix.base.models import OrderRefund

                refund = OrderRefund.objects.create(
                    order=order,
                    amount=total_paid,
                    provider="postfinance",
                    state="created",
                )

                # Get payment provider to execute refund
                provider = event.get_payment_provider("postfinance")
                if provider:
                    try:
                        provider.execute_refund(refund, user="system")
                    except Exception as e:
                        logger.error(
                            "Failed to execute refund for order %s: %s",
                            order.code,
                            e,
                        )

            # Update order status appropriately
            if order.status in ["pending", "paid"]:
                order.status = "canceled"
                order.save()

            # Create audit log entry
            order.log_action(
                "pretix_postfinance.installment.cancelled",
                data={
                    "reason": "Grace period expired",
                    "failed_installments": [
                        {
                            "installment_number": inst.installment_number,
                            "amount": str(inst.amount),
                            "failure_reason": inst.failure_reason,
                        }
                        for inst in installments
                    ],
                    "total_paid": str(total_paid),
                    "refund_amount": str(total_paid),
                },
            )

            logger.info(
                "Cancelled order %s due to expired grace period",
                order.code,
            )

            # Send cancellation email to customer
            _send_installment_order_cancelled_email(order, installments, total_paid)

        except Exception as e:
            logger.exception(
                "Error processing order %s for grace period expiration: %s",
                order_id,
                e,
            )

    logger.info("Completed cancel_expired_grace_periods task")


@shared_task
def retry_failed_installments() -> None:
    """
    Retry failed installment payments during grace period.

    This task runs daily to retry installments that failed but are still
    within their grace period.
    """
    logger.info("Starting retry_failed_installments task")

    # Get failed installments that are still within grace period
    now_time = now()
    failed_installments = InstallmentSchedule.objects.filter(
        status=InstallmentSchedule.Status.FAILED,
        grace_period_ends__gt=now_time,
    ).select_related("order")

    logger.info("Found %s failed installments to retry", failed_installments.count())

    for installment in failed_installments:
        try:
            # Get the event settings to create PostFinance client
            event = installment.order.event
            provider = event.get_payment_provider("postfinance")
            if not provider:
                logger.warning(
                    "No PostFinance provider configured for event %s",
                    event.slug,
                )
                continue

            # Create PostFinance client
            client = provider._get_client()

            # Charge the token
            merchant_reference = (
                f"pretix-{event.slug}-installment-{installment.installment_number}-retry"
            )
            transaction = client.charge_token(
                token_id=installment.token_id or "",
                amount=float(installment.amount),
                currency=event.currency,
                merchant_reference=merchant_reference,
            )

            # Check transaction state
            if transaction.state in {
                "AUTHORIZED",
                "COMPLETED",
                "FULFILL",
                "CONFIRMED",
                "PROCESSING",
            }:
                # Payment successful
                installment.status = InstallmentSchedule.Status.PAID
                installment.paid_at = now()
                installment.grace_period_ends = None
                installment.failure_reason = ""
                installment.save()

                # Create OrderPayment record
                from pretix.base.models import OrderPayment

                OrderPayment.objects.create(
                    order=installment.order,
                    amount=installment.amount,
                    payment_date=now(),
                    provider="postfinance",
                    state="confirmed",
                    info_data={
                        "transaction_id": transaction.id,
                        "state": (
                            transaction.state.value
                            if transaction.state
                            else None
                        ),
                        "installment_number": installment.installment_number,
                        "type": "installment",
                        "retry": True,
                    },
                )

                logger.info(
                    "Successfully retried installment %s for order %s",
                    installment.installment_number,
                    installment.order.code,
                )

                # Send confirmation email to customer
                _send_installment_payment_success_email(installment)

            else:
                # Payment still failing
                logger.warning(
                    "Retry failed for installment %s for order %s: %s",
                    installment.installment_number,
                    installment.order.code,
                    transaction.state,
                )

                # Update failure reason but keep grace period
                installment.failure_reason = (
                    f"Retry failed - PostFinance transaction state: {transaction.state}"
                )
                installment.save()

        except PostFinanceError as e:
            logger.error(
                "PostFinance API error retrying installment %s: %s",
                installment.installment_number,
                e,
            )

            # Update failure reason but keep grace period
            installment.failure_reason = f"Retry failed - {e!s}"
            installment.save()

        except Exception as e:
            logger.exception(
                "Unexpected error retrying installment %s: %s",
                installment.installment_number,
                e,
            )

            # Update failure reason but keep grace period
            installment.failure_reason = f"Retry failed - {e!s}"
            installment.save()

    logger.info("Completed retry_failed_installments task")


@shared_task
def process_due_installments() -> None:
    """
    Process due installments by charging saved tokens.

    This task runs daily to automatically charge installments that are due.
    For each due installment, it attempts to charge the saved token and updates
    the installment status accordingly.
    """
    logger.info("Starting process_due_installments task")

    # Get installments that are due today and in scheduled status
    today = now().date()
    due_installments = InstallmentSchedule.objects.filter(
        status=InstallmentSchedule.Status.SCHEDULED,
        due_date__lte=today,
    ).select_related("order")

    logger.info("Found %s due installments to process", due_installments.count())

    for installment in due_installments:
        try:
            # Get the event settings to create PostFinance client
            event = installment.order.event
            provider = event.get_payment_provider("postfinance")
            if not provider:
                logger.warning(
                    "No PostFinance provider configured for event %s",
                    event.slug,
                )
                continue

            # Create PostFinance client
            client = provider._get_client()

            # Charge the token
            merchant_reference = (
                f"pretix-{event.slug}-installment-{installment.installment_number}"
            )
            transaction = client.charge_token(
                token_id=installment.token_id or "",
                amount=float(installment.amount),
                currency=event.currency,
                merchant_reference=merchant_reference,
            )

            # Check transaction state
            if transaction.state in {
                "AUTHORIZED",
                "COMPLETED",
                "FULFILL",
                "CONFIRMED",
                "PROCESSING",
            }:
                # Payment successful
                installment.status = InstallmentSchedule.Status.PAID
                installment.paid_at = now()
                installment.save()

                # Create OrderPayment record
                from pretix.base.models import OrderPayment

                OrderPayment.objects.create(
                    order=installment.order,
                    amount=installment.amount,
                    payment_date=now(),
                    provider="postfinance",
                    state="confirmed",
                    info_data={
                        "transaction_id": transaction.id,
                        "state": (
                            transaction.state.value
                            if transaction.state
                            else None
                        ),
                        "installment_number": installment.installment_number,
                        "type": "installment",
                    },
                )

                logger.info(
                    "Successfully charged installment %s for order %s",
                    installment.installment_number,
                    installment.order.code,
                )

                # Send confirmation email to customer
                _send_installment_payment_success_email(installment)

            else:
                # Payment failed
                installment.status = InstallmentSchedule.Status.FAILED
                installment.failure_reason = (
                    f"PostFinance transaction state: {transaction.state}"
                )
                installment.grace_period_ends = now() + timedelta(days=3)
                installment.save()

                logger.warning(
                    "Failed to charge installment %s for order %s: %s",
                    installment.installment_number,
                    installment.order.code,
                    transaction.state,
                )

                # Send failure notification to customer
                _send_installment_payment_failed_email(installment)

                # Send failure notification to organizer
                _send_organizer_failure_notification(installment)

        except PostFinanceError as e:
            logger.error(
                "PostFinance API error charging installment %s: %s",
                installment.installment_number,
                e,
            )

            installment.status = InstallmentSchedule.Status.FAILED
            installment.failure_reason = str(e)
            installment.grace_period_ends = now() + timedelta(days=3)
            installment.save()

            # Send failure notification to customer
            _send_installment_payment_failed_email(installment)

            # Send failure notification to organizer
            _send_organizer_failure_notification(installment)

        except Exception as e:
            logger.exception(
                "Unexpected error processing installment %s: %s",
                installment.installment_number,
                e,
            )

            installment.status = InstallmentSchedule.Status.FAILED
            installment.failure_reason = str(e)
            installment.grace_period_ends = now() + timedelta(days=3)
            installment.save()

    logger.info("Completed process_due_installments task")


def _send_installment_payment_success_email(installment: InstallmentSchedule) -> None:
    """Send email to customer when installment payment succeeds."""
    try:
        order = installment.order
        event = order.event

        subject = f"Installment Payment Successful - {event.name}"

        message = f"""Dear Customer,

Your installment payment of {installment.amount} {event.currency}
(Installment {installment.installment_number}) has been successfully processed.

Order: {order.code}
Event: {event.name}
Amount: {installment.amount} {event.currency}
Date: {installment.paid_at}

Thank you for your payment.
"""

        send_mail(
            subject,
            message,
            f"noreply@{event.organizer.slug}.pretix.example.com",
            [order.email],
            fail_silently=True,
        )

    except Exception as e:
        logger.error(
            "Failed to send installment success email for installment %s: %s",
            installment.installment_number,
            e,
        )


def _send_installment_payment_failed_email(installment: InstallmentSchedule) -> None:
    """Send email to customer when installment payment fails."""
    try:
        order = installment.order
        event = order.event

        subject = f"Installment Payment Failed - {event.name}"

        message = f"""Dear Customer,

We regret to inform you that your installment payment of
{installment.amount} {event.currency} (Installment {installment.installment_number})
has failed.

Order: {order.code}
Event: {event.name}
Amount: {installment.amount} {event.currency}
Failure Reason: {installment.failure_reason}

We will automatically retry the payment until {installment.grace_period_ends}.
If the payment continues to fail, your order may be cancelled.

Please ensure your payment method is valid and has sufficient funds.
"""

        send_mail(
            subject,
            message,
            f"noreply@{event.organizer.slug}.pretix.example.com",
            [order.email],
            fail_silently=True,
        )

    except Exception as e:
        logger.error(
            "Failed to send installment failure email for installment %s: %s",
            installment.installment_number,
            e,
        )


def _send_organizer_failure_notification(installment: InstallmentSchedule) -> None:
    """Send immediate notification to organizer when installment payment fails."""
    try:
        order = installment.order
        event = order.event

        subject = f"Installment Payment Failed - Order {order.code}"

        message = f"""Dear Organizer,

An installment payment has failed for order {order.code}.

Event: {event.name}
Order: {order.code}
Customer: {order.email}
Installment: {installment.installment_number}
Amount: {installment.amount} {event.currency}
Failure Reason: {installment.failure_reason}

The payment will be automatically retried until the grace period ends.
"""

        # Send to all organizer email addresses
        organizer_emails = [
            email for email in event.organizer.all_emails if email
        ]

        if organizer_emails:
            send_mail(
                subject,
                message,
                f"noreply@{event.organizer.slug}.pretix.example.com",
                organizer_emails,
                fail_silently=True,
            )

    except Exception as e:
        logger.error(
            "Failed to send organizer notification for installment %s: %s",
            installment.installment_number,
            e,
        )


def _send_installment_order_cancelled_email(
    order: Any, installments: list[InstallmentSchedule], total_paid: float
) -> None:
    """Send email to customer when order is cancelled due to expired grace period."""
    try:
        event = order.event

        subject = f"Order Cancelled - {event.name}"

        # Build failed installments list
        failed_list = "\n".join(
            f"- Installment {inst.installment_number}: {inst.amount} "
            f"{event.currency} (Reason: {inst.failure_reason})"
            for inst in installments
        )

        message = f"""Dear Customer,

We regret to inform you that your order {order.code} has been cancelled
due to failed installment payments.

Event: {event.name}
Order: {order.code}

Failed Installments:
{failed_list}

Total Amount Paid: {total_paid} {event.currency}
Refund Status: A refund for the paid amount has been initiated

We apologize for any inconvenience. You may place a new order if you
still wish to attend the event.
"""

        send_mail(
            subject,
            message,
            f"noreply@{event.organizer.slug}.pretix.example.com",
            [order.email],
            fail_silently=True,
        )

    except Exception as e:
        logger.error(
            "Failed to send order cancellation email for order %s: %s",
            order.code,
            e,
        )
