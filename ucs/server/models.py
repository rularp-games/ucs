from django.db import models
from django.core.exceptions import ValidationError


class Project(models.Model):
    """Модель проекта"""
    name = models.CharField(max_length=255, verbose_name='Название проекта')
    description = models.TextField(blank=True, null=True, verbose_name='Описание')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Дата создания')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Дата обновления')

    class Meta:
        verbose_name = 'Проект'
        verbose_name_plural = 'Проекты'
        ordering = ['-created_at']

    def __str__(self):
        return self.name


class Object(models.Model):
    """Модель объекта, зависящая от проекта"""
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name='objects',
        verbose_name='Проект'
    )
    name = models.CharField(max_length=255, verbose_name='Название объекта')
    description = models.TextField(blank=True, null=True, verbose_name='Описание')

    class Meta:
        verbose_name = 'Объект'
        verbose_name_plural = 'Объекты'

    def __str__(self):
        return f"{self.name} ({self.project.name})"


class Property(models.Model):
    """Модель свойства, зависящая от проекта"""
    
    TYPE_CHOICES = [
        ('number', 'Число'),
        ('boolean', 'Булево значение'),
        ('text', 'Текст'),
    ]
    
    project = models.ForeignKey(
        Object,
        on_delete=models.CASCADE,
        related_name='properties',
        verbose_name='Проект'
    )
    name = models.CharField(max_length=255, verbose_name='Название свойства')
    description = models.TextField(blank=True, null=True, verbose_name='Описание')
    property_type = models.CharField(
        max_length=10,
        choices=TYPE_CHOICES,
        verbose_name='Тип свойства'
    )
    
    # Значения в зависимости от типа
    value_number = models.FloatField(null=True, blank=True, verbose_name='Числовое значение')
    value_boolean = models.BooleanField(null=True, blank=True, verbose_name='Булево значение')
    value_text = models.TextField(null=True, blank=True, verbose_name='Текстовое значение')
    
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Дата создания')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Дата обновления')

    class Meta:
        verbose_name = 'Свойство'
        verbose_name_plural = 'Свойства'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.name} ({self.project.name})"
    
    def get_value(self):
        """Возвращает значение свойства в зависимости от типа"""
        if self.property_type == 'number':
            return self.value_number
        elif self.property_type == 'boolean':
            return self.value_boolean
        elif self.property_type == 'text':
            return self.value_text
        return None
    
    def set_value(self, value):
        """Устанавливает значение свойства в зависимости от типа"""
        if self.property_type == 'number':
            self.value_number = float(value) if value is not None else None
            self.value_boolean = None
            self.value_text = None
        elif self.property_type == 'boolean':
            self.value_boolean = bool(value) if value is not None else None
            self.value_number = None
            self.value_text = None
        elif self.property_type == 'text':
            self.value_text = str(value) if value is not None else None
            self.value_number = None
            self.value_boolean = None
    
    def clean(self):
        """Валидация модели"""
        super().clean()
        
        # Проверяем, что заполнено только одно поле значения в зависимости от типа
        if self.property_type == 'number':
            if self.value_boolean is not None or self.value_text:
                raise ValidationError('Для числового свойства должны быть заполнены только числовые значения')
        elif self.property_type == 'boolean':
            if self.value_number is not None or self.value_text:
                raise ValidationError('Для булевого свойства должны быть заполнены только булевы значения')
        elif self.property_type == 'text':
            if self.value_number is not None or self.value_boolean is not None:
                raise ValidationError('Для текстового свойства должны быть заполнены только текстовые значения')
