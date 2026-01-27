from django.apps import AppConfig


class SubscriptionsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'subscriptions'
    verbose_name = 'Subscriptions & Plans'
    
    def ready(self):
        """Import signals when app is ready"""
        import subscriptions.signals