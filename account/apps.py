from django.apps import AppConfig


class AccountsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'account'
    verbose_name = 'Accounts & Authentication'
    
    def ready(self):
        """Import signals when app is ready"""
        import account.signals