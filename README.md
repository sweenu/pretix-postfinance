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
- Optional alternative payment currency: customers can choose to be charged
  in a different currency (e.g. CHF on a EUR event) at a configured exchange
  rate (see below)

### Charging in an alternative currency

Events priced in one currency (e.g. EUR) can offer customers the option to be
charged in another (e.g. CHF for Swiss customers). pretix itself keeps all
accounting (orders, invoices, refunds) in the event currency; only the charge
sent to PostFinance is converted.

To use it, set the **alternative payment currency** and the **exchange rate**
(how much of that currency is charged per 1 unit of the event currency) in
the provider settings. Include a small margin in the rate to cover exchange
rate fluctuations.

Customers then see a "Pay in CHF" checkbox with the converted amount when
they select PostFinance during checkout. The rate in effect when a payment is
started is stored on the payment and is also used to convert partial refunds;
full refunds are refunded by PostFinance at the exact remaining transaction
amount. The charged amount and rate are shown to the customer during checkout
and to organizers in the order's payment details.

## License

GNU Affero General Public License v3.0 (AGPLv3)
