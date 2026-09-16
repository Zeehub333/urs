from django.contrib import admin
from .models import App, Report, FilePermission, UserDriver, Connection, IoTMirror, Country, City, Currency, Company, Branch


@admin.register(Country)
class CountryAdmin(admin.ModelAdmin):
    list_display = ["code", "name", "is_default", "is_active"]
    list_filter = ["is_default", "is_active"]


@admin.register(City)
class CityAdmin(admin.ModelAdmin):
    list_display = ["name", "country", "is_active"]
    list_filter = ["country"]


@admin.register(Currency)
class CurrencyAdmin(admin.ModelAdmin):
    list_display = ["code", "name", "exchange_rate", "is_default", "is_active"]
    list_filter = ["is_default", "is_active"]


@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    list_display = ["code", "name", "fiscal_year", "is_active"]


@admin.register(Branch)
class BranchAdmin(admin.ModelAdmin):
    list_display = ["company", "code", "name", "schema_name", "is_active"]
    readonly_fields = ["schema_name"]


@admin.register(Connection)
class ConnectionAdmin(admin.ModelAdmin):
    list_display = ["name", "conn_type", "engine", "host", "is_local", "is_queryable"]
    list_filter = ["conn_type", "engine", "is_local", "is_queryable"]


@admin.register(IoTMirror)
class IoTMirrorAdmin(admin.ModelAdmin):
    list_display = ["table_name", "connection", "endpoint", "local_connection", "status", "progress_pct", "last_sync_at"]
    list_filter = ["status", "endpoint", "auto_sync"]


admin.site.register(App)
admin.site.register(Report)
admin.site.register(FilePermission)
admin.site.register(UserDriver)
