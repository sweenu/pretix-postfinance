from __future__ import annotations

import os
from typing import Any

from django.utils.translation import gettext_lazy as _

from . import __version__

# Allow running tests without pretix fully installed
_TESTING = os.environ.get("PRETIX_POSTFINANCE_TESTING", "0") == "1"


# Import PluginConfig or create stub for testing
try:
    from pretix.base.plugins import PluginConfig as _PluginConfigBase
except ImportError:
    if not _TESTING:
        raise RuntimeError("Please use pretix 2.7 or above to run this plugin!") from None
    # Create a stub for testing
    _PluginConfigBase: Any = object  # type: ignore[no-redef]


class PluginApp(_PluginConfigBase):
    default = True
    name = "pretix_postfinance"
    verbose_name = "PostFinance"

    class PretixPluginMeta:
        name = _("PostFinance")
        author = "Sweenu"
        description = _("PostFinance Checkout payment plugin for pretix")
        visible = True
        picture = "pretix_postfinance/pf_logo.svg"
        version = __version__
        category = "PAYMENT"
        compatibility = "pretix>=2.7.0"
        settings_links = (
            (
                (_("Payment"), _("PostFinance")),
                "control:event.settings.payment.provider",
                {"provider": "postfinance"},
            ),
        )

    def ready(self) -> None:
        from . import signals  # noqa: F401
