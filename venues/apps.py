from django.apps import AppConfig


class VenuesConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'venues'
    verbose_name = 'Venue Management'
    
    def ready(self):
        """Import signals when app is ready"""
        import venues.signals