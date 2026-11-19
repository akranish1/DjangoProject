from django.apps import AppConfig
from django.contrib import admin


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core"
    verbose_name = "Platform core"

    def ready(self):
        admin.site.site_header = "EventHub administration"
        admin.site.site_title = "EventHub admin"
        admin.site.index_title = "Platform data"
