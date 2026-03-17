from django.apps import AppConfig


class WalletConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'wallet'
    verbose_name = 'Gold Coins Wallet'

    def ready(self):
        import wallet.signals  # noqa: F401 — connects signals on startup