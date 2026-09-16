"""Local-run settings: same as config.settings but SQLite (no Postgres on this machine).

Usage: python manage.py <cmd> --settings=config.settings_local
Created to run the app without 172.16.10.101. Do not deploy with this.
"""
from config.settings import *  # noqa

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db_local.sqlite3",
    }
}
