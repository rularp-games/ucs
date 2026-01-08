import time
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


@shared_task
def change_value_gradually(property_id, target_value, step, interval=1.0):
    """
    Постепенно изменяет числовое значение свойства с указанным шагом до целевого значения.
    
    Args:
        property_id: ID свойства для изменения
        target_value: Целевое значение
        step: Шаг изменения (положительное число)
        interval: Интервал между шагами в секундах (по умолчанию 1 секунда)
    
    Returns:
        dict: Результат выполнения с начальным и конечным значениями
    """
    from .models import Property
    
    try:
        prop = Property.objects.get(id=property_id)
    except Property.DoesNotExist:
        return {'error': f'Property with id {property_id} not found'}
    
    if prop.property_type != 'number':
        return {'error': f'Property type must be "number", got "{prop.property_type}"'}
    
    current_value = prop.value_number or 0.0
    initial_value = current_value
    step = abs(step)  # Убедимся, что шаг положительный
    
    # Определяем направление изменения
    if current_value < target_value:
        # Увеличиваем значение
        while current_value < target_value:
            current_value = min(current_value + step, target_value)
            prop.value_number = current_value
            prop.save(update_fields=['value_number', 'updated_at'])
            if current_value < target_value:
                time.sleep(interval)
    elif current_value > target_value:
        # Уменьшаем значение
        while current_value > target_value:
            current_value = max(current_value - step, target_value)
            prop.value_number = current_value
            prop.save(update_fields=['value_number', 'updated_at'])
            if current_value > target_value:
                time.sleep(interval)
    
    return {
        'property_id': property_id,
        'initial_value': initial_value,
        'final_value': current_value,
        'target_value': target_value,
        'steps_taken': abs(target_value - initial_value) / step if step else 0
    }
