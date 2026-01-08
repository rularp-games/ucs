from celery import shared_task


@shared_task
def example_task(x, y):
    """Пример задачи Celery: сложение двух чисел."""
    return x + y


@shared_task
def send_notification(user_id, message):
    """Пример асинхронной отправки уведомления."""
    # Здесь можно добавить логику отправки email, push-уведомлений и т.д.
    print(f"Отправка уведомления пользователю {user_id}: {message}")
    return True
