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
- Test mode support, including an opt-in option to offer the production space
  alongside the test space while in test mode, so it can be verified
  end-to-end before going live

### Testing the production space

Switching on **Offer the production space in test mode** in the payment
settings makes checkout offer two PostFinance options while the event is in
test mode:

- **PostFinance (test space)** — uses the test credentials, no real money
- **PostFinance (production space)** — uses the live credentials

The option is **off by default** and requires test credentials. Because it
lives in the payment settings, only the people who can reach those settings
can expose the production space to buyers in test mode. Leave it off unless
you are actively running that verification, and switch it back off afterwards.

> [!WARNING]
> Payments through the production space option are **real charges**, even
> though the order is a test mode order. Refund or void them in your
> PostFinance dashboard before you disable test mode: pretix offers to delete
> all test mode orders at that point, and deleting the order does not undo the
> charge — it only removes your record of it.

Set up webhooks for both spaces (there is a separate "Setup webhooks" button
next to each set of credentials), otherwise payments in the space without a
webhook are never confirmed automatically.

## License

GNU Affero General Public License v3.0 (AGPLv3)
