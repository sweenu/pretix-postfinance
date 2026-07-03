# pretix-postfinance

PostFinance Checkout payment plugin for pretix.

## Installation

### PyPI

```bash
pip install pretix-postfinance
```

### NixOS

For NixOS users, the plugin can be installed using the flake:

```nix
{ inputs, pkgs, ... }:
{
  services.pretix = {
    enable = true;
    plugins = [
      inputs.pretix-postfinance.packages.${pkgs.stdenv.hostPlatform.system}.default
    ];
  };
}
```

## Development

### Setup with uv

```bash
# Create virtual environment
uv venv

# Install with development dependencies
uv pip install -e ".[dev]"
```

### Setup with Nix

```bash
# Enter development shell
nix develop

# Or use direnv
direnv allow
```

### Running checks

```bash
# Run linting
uv run ruff check .

# Run type checking
uv run ty check pretix_postfinance/

# Run tests with coverage
uv run pytest tests/ --cov=pretix_postfinance --cov-report=term-missing -v
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
- Optional "PostFinance (CHF)" payment method that charges customers in CHF
  on events priced in another currency (see below)

### Charging in CHF on non-CHF events

The plugin registers a second payment method, **PostFinance (CHF)**, intended
for events priced in EUR (or any non-CHF currency) that want to offer their
Swiss customers the option to pay in Swiss francs. pretix itself keeps all
accounting (orders, invoices, refunds) in the event currency; only the charge
sent to PostFinance is converted.

To use it:

1. Configure and enable the regular PostFinance provider first — the CHF
   method reuses its API credentials and webhooks.
2. Enable **PostFinance (CHF)** in the event's payment settings and set the
   **exchange rate** (how many CHF are charged per 1 unit of the event
   currency). Include a small margin to cover exchange rate fluctuations.

The rate in effect when a payment is started is stored on the payment and is
also used to convert partial refunds back to CHF; full refunds are refunded
by PostFinance at the exact remaining transaction amount. The charged CHF
amount and rate are shown to the customer during checkout and to organizers
in the order's payment details.

## License

GNU Affero General Public License v3.0 (AGPLv3)
