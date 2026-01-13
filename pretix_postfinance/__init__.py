from __future__ import annotations

import os
from typing import Any

from django.utils.translation import gettext_lazy as _

# Allow running tests without pretix fully installed
_TESTING = os.environ.get("PRETIX_POSTFINANCE_TESTING", "0") == "1"

# Import PluginConfig or create stub for testing
try:
    from pretix.base.plugins import PluginConfig as _PluginConfigBase
except ImportError:
    if not _TESTING:
        raise RuntimeError("Please use pretix 2024.1.0 or above to run this plugin!")
    # Create a stub for testing
    _PluginConfigBase: Any = object  # type: ignore[no-redef]


class PluginApp(_PluginConfigBase):
    name = "pretix_postfinance"
    verbose_name = _("PostFinance")

    class PretixPluginMeta:
        name = _("PostFinance")
        author = "pretix-postfinance contributors"
        version = "1.0.0"
        category = "PAYMENT"
        description = _(
            "Accept payments via PostFinance Checkout API. "
            "Enables Swiss payment methods including Card, E-Finance, and TWINT."
        )
        visible = True
        compatibility = "pretix>=2024.1.0"

    def ready(self) -> None:
        from . import signals  # noqa: F401


default_app_config = "pretix_postfinance.PluginApp"
