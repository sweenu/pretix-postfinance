# Development Guide

This document helps AI agents understand the pretix-postfinance project structure and development workflow.

## Project Overview

PostFinance Checkout payment plugin for pretix.

## Key Files

- **`pretix_postfinance/payment.py`**: Main payment provider (BasePaymentProvider subclass)
- **`pretix_postfinance/views.py`**: Admin views for capture/refund + webhook handler
- **`pretix_postfinance/api.py`**: PostFinance Checkout SDK wrapper
- **`pretix_postfinance/_types.py`**: Type definitions for pretix-specific types
- **`tests/`**: pytest test suite

## Architecture

### Payment Flow
1. User initiates payment -> `payment_form_render()`
2. Payment created via PostFinance API -> transaction ID stored in `info_data`
3. User redirected to PostFinance checkout
4. Webhook receives transaction state updates -> `_process_transaction_state()`
5. Payment marked as confirmed/failed in pretix

### Refund Flow
1. Admin initiates refund -> `PostFinanceRefundView`
2. Refund created via API -> refund ID stored
3. Webhook receives refund state updates -> `_process_refund_state()`
4. Refund history tracked in `info_data['refund_history']`

## Development Commands

All commands match the GitHub workflow exactly:

```bash
# Lint
uv run ruff check .

# Type check
uv run mypy pretix_postfinance/

# Test
uv run pytest tests/ --cov=pretix_postfinance --cov-report=term-missing -v
```

## Important Conventions

2. **Type Hints**: Strict mypy with django-stubs, use `PretixHttpRequest` for views
3. **Payment Info Storage**: Use `payment.info_data` dict for transaction/refund metadata
4. **Error Handling**: Store `error_code` and `error_status_code` in info_data
5. **Import Sorting**: stdlib -> third-party -> local (enforced by ruff)

## Testing Strategy

- Unit tests for API client and utilities
- Mocked PostFinance SDK services
- Coverage reporting in CI with diff on PRs

## CI/CD

GitHub workflow runs on PRs:
- **test**: pytest with coverage (Python 3.9-3.14)
- **coverage-diff**: Shows coverage change in PR comments
- **typecheck**: mypy strict type checking
- **lint**: ruff linting

## Type System Notes

- Use `PretixHttpRequest` instead of `HttpRequest` for views that access `request.event`
- Django plugin configured in `pyproject.toml` with `django_settings_module = "tests.settings"`
- Ignore missing imports for `pretix.*` and `postfinancecheckout.*`

## Installment Payment System

The installment payment feature allows customers to pay for orders in multiple monthly installments using saved payment methods.

### Key Components

- **`pretix_postfinance/models.py`**: `InstallmentSchedule` model tracks individual installments
- **`pretix_postfinance/installments.py`**: Calculation utilities for installment schedules
- **`pretix_postfinance/tasks.py`**: Background tasks for automatic installment processing
- **`pretix_postfinance/payment.py`**: Payment provider methods for installment workflow

### Installment Workflow

1. **Checkout**: Customer selects number of installments (2-12) and agrees to terms
2. **First Payment**: Initial payment creates token via `tokenization_mode=FORCE_CREATION`
3. **Schedule Creation**: `_handle_installment_payment()` creates InstallmentSchedule records
4. **Automatic Charging**: `process_due_installments()` task charges subsequent installments
5. **Failure Handling**: `retry_failed_installments()` retries failed payments during grace period
6. **Cancellation**: `cancel_expired_grace_periods()` handles expired grace periods

### Important Patterns

- **Token Management**: First payment uses `TokenizationMode.FORCE_CREATION` to save card token
- **Session Data**: Installment count stored in session as `payment_postfinance_num_installments`
- **Status Tracking**: InstallmentSchedule uses status field (scheduled/paid/failed/cancelled)
- **Email Notifications**: Automatic emails sent for success, failure, reminders, and cancellations

### Development Notes

- Installment calculations use `ROUND_HALF_UP` for precise amount calculations
- All installments must complete at least 30 days before event date
- Grace period is 3 days for failed payments before cancellation
- Organizer notifications sent immediately on payment failure

### Testing Considerations

- Mock PostFinance token responses for installment testing
- Test installment schedule creation with various num_installments values
- Verify background tasks handle edge cases (expired grace periods, etc.)
- Test email template rendering with different installment scenarios
