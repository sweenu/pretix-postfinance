# pretix-postfinance

PostFinance Checkout payment plugin for pretix.

## Installation

```bash
pip install pretix-postfinance
```

## Development

### Setup

```bash
# Create virtual environment
uv venv

# Install with development dependencies
uv pip install -e ".[dev]"
```

### Running checks

```bash
# Run linting
uv run ruff check .

# Run type checking
PRETIX_POSTFINANCE_TESTING=1 uv run mypy pretix_postfinance/

# Run tests with coverage
PRETIX_POSTFINANCE_TESTING=1 uv run pytest tests/ --cov=pretix_postfinance --cov-report=term-missing -v
```

### Configuration

Configure the plugin in your pretix settings with:

- **Space ID**: Your PostFinance Checkout space ID
- **User ID**: API user ID
- **API Secret**: API authentication secret
- **Environment**: `production` or `sandbox`

## Features

- Payment processing via PostFinance Checkout
- Full and partial refund support
- Webhook handling for payment and refund notifications
- Payment capture support
- Comprehensive error handling and logging
- Admin interface integration

## License

GNU Affero General Public License v3.0 (AGPLv3)
