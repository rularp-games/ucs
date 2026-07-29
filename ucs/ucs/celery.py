import os

from celery import Celery

# Устанавливаем модуль настроек Django для Celery
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'ucs.settings')

app = Celery('ucs')

# Загружаем конфигурацию из настроек Django с префиксом CELERY_
app.config_from_object('django.conf:settings', namespace='CELERY')

# Автоматически обнаруживаем задачи во всех зарегистрированных приложениях Django
app.autodiscover_tasks()


@app.task(bind=True, ignore_result=True)
def debug_task(self):
    print(f'Request: {self.request!r}')
