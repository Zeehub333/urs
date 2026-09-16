from django.apps import AppConfig


class UrsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'urs'

    def ready(self):
        """بدء كنّاس المزامنة التلقائية مع عملية السيرفر فقط.

        - runserver بالتحميل التلقائي: الأب (مراقب الملفات) يُتجاهل، والابن
          (RUN_MAIN=true) يبدأ الكنّاس — بلا تكرار.
        - runserver --noreload: العملية الوحيدة تبدأ الكنّاس.
        - باقي الأوامر (migrate/check/shell): لا خيوط خلفية إطلاقاً.
        """
        import os
        import sys
        try:
            argv = sys.argv or []
            if len(argv) < 2 or argv[1] != "runserver":
                return
            if os.environ.get("RUN_MAIN") != "true" and "--noreload" not in argv:
                return
            from .iot_sync import ensure_sweeper
            ensure_sweeper()
        except Exception:
            pass
