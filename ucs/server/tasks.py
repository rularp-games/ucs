import time
from celery import shared_task, current_task


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


@shared_task(bind=True)
def change_value_gradually(self, property_id, target_value, step, interval=1.0):
    """
    Постепенно изменяет числовое значение свойства с указанным шагом до целевого значения.
    Поддерживает отмену через revoke.
    
    Args:
        property_id: ID свойства для изменения
        target_value: Целевое значение
        step: Шаг изменения (положительное число)
        interval: Интервал между шагами в секундах (по умолчанию 1 секунда)
    
    Returns:
        dict: Результат выполнения с начальным и конечным значениями
    """
    from .models import Property
    from celery.exceptions import Ignore
    
    try:
        prop = Property.objects.get(id=property_id)
    except Property.DoesNotExist:
        return {'error': f'Property with id {property_id} not found'}
    
    if prop.property_type != 'number':
        return {'error': f'Property type must be "number", got "{prop.property_type}"'}
    
    current_value = prop.value_number or 0.0
    initial_value = current_value
    step = abs(step)  # Убедимся, что шаг положительный
    steps_taken = 0
    
    # Вычисляем общее количество шагов для прогресса
    total_steps = int(abs(target_value - initial_value) / step) if step else 0
    
    def check_revoked():
        """Проверяем, была ли задача отменена"""
        # Проверка через AsyncResult
        from celery.result import AsyncResult
        result = AsyncResult(self.request.id)
        return result.state == 'REVOKED'
    
    def update_progress(current, target, steps):
        """Обновляем прогресс задачи"""
        if total_steps > 0:
            progress = int((steps / total_steps) * 100)
        else:
            progress = 100
        self.update_state(
            state='PROGRESS',
            meta={
                'current_value': current,
                'target_value': target,
                'progress': progress,
                'steps_taken': steps,
                'total_steps': total_steps
            }
        )
    
    # Определяем направление изменения
    if current_value < target_value:
        # Увеличиваем значение
        while current_value < target_value:
            # Проверяем отмену
            if self.is_aborted():
                return {
                    'status': 'aborted',
                    'property_id': property_id,
                    'initial_value': initial_value,
                    'final_value': current_value,
                    'target_value': target_value,
                    'steps_taken': steps_taken
                }
            
            current_value = min(current_value + step, target_value)
            prop.value_number = current_value
            prop.save(update_fields=['value_number', 'updated_at'])
            steps_taken += 1
            update_progress(current_value, target_value, steps_taken)
            
            if current_value < target_value:
                time.sleep(interval)
    elif current_value > target_value:
        # Уменьшаем значение
        while current_value > target_value:
            # Проверяем отмену
            if self.is_aborted():
                return {
                    'status': 'aborted',
                    'property_id': property_id,
                    'initial_value': initial_value,
                    'final_value': current_value,
                    'target_value': target_value,
                    'steps_taken': steps_taken
                }
            
            current_value = max(current_value - step, target_value)
            prop.value_number = current_value
            prop.save(update_fields=['value_number', 'updated_at'])
            steps_taken += 1
            update_progress(current_value, target_value, steps_taken)
            
            if current_value > target_value:
                time.sleep(interval)
    
    return {
        'status': 'completed',
        'property_id': property_id,
        'initial_value': initial_value,
        'final_value': current_value,
        'target_value': target_value,
        'steps_taken': steps_taken
    }
