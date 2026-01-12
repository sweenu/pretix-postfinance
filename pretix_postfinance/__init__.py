from django.utils.translation import gettext_lazy as _

try:
    from pretix.base.plugins import PluginConfig
except ImportError:
    raise RuntimeError("Please use pretix 2024.1.0 or above to run this plugin!")


class PluginApp(PluginConfig):
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
