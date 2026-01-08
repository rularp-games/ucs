from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView
from django.shortcuts import get_object_or_404
from celery.result import AsyncResult
from .models import Object, Property
from .serializers import ObjectSerializer, PropertySerializer
from .tasks import change_value_gradually


class ObjectViewSet(viewsets.ModelViewSet):
    """
    ViewSet для работы с объектами через REST API.
    
    list: Получить список всех объектов
    retrieve: Получить конкретный объект по ID
    create: Создать новый объект
    update: Обновить объект
    partial_update: Частично обновить объект
    destroy: Удалить объект
    """
    queryset = Object.objects.all()
    serializer_class = ObjectSerializer
    
    def get_queryset(self):
        """
        Опциональная фильтрация через query параметры:
        - project_id: фильтрация по проекту
        - name: поиск по имени (точное совпадение)
        - name__icontains: поиск по имени (частичное совпадение, без учета регистра)
        
        Примеры:
            /api/objects/?project_id=1
            /api/objects/?name=Airlock
            /api/objects/?name__icontains=air
        """
        queryset = Object.objects.select_related('project').all()
        
        # Фильтрация по project_id
        project_id = self.request.query_params.get('project_id', None)
        if project_id is not None:
            queryset = queryset.filter(project_id=project_id)
        
        # Фильтрация по имени (точное совпадение)
        name = self.request.query_params.get('name', None)
        if name is not None:
            queryset = queryset.filter(name=name)
        
        # Фильтрация по имени (частичное совпадение)
        name_contains = self.request.query_params.get('name__icontains', None)
        if name_contains is not None:
            queryset = queryset.filter(name__icontains=name_contains)
        
        return queryset
    
    def perform_create(self, serializer):
        """Создание объекта с обработкой project_id"""
        project_id = self.request.data.get('project_id')
        if project_id:
            from .models import Project
            project = get_object_or_404(Project, id=project_id)
            serializer.save(project=project)
        else:
            serializer.save()
    
    @action(detail=True, methods=['get'], url_path='properties')
    def properties(self, request, pk=None):
        """
        Получить все свойства для конкретного объекта
        Пример: GET /api/objects/{id}/properties/
        """
        obj = self.get_object()
        properties = Property.objects.filter(object=obj).select_related('object', 'object__project')
        serializer = PropertySerializer(properties, many=True)
        return Response(serializer.data)


class PropertyViewSet(viewsets.ModelViewSet):
    """
    ViewSet для работы со свойствами через REST API.
    
    list: Получить список всех свойств
    retrieve: Получить конкретное свойство по ID
    create: Создать новое свойство
    update: Обновить свойство
    partial_update: Частично обновить свойство
    destroy: Удалить свойство
    """
    queryset = Property.objects.all()
    serializer_class = PropertySerializer
    
    def get_queryset(self):
        """
        Опциональная фильтрация через query параметры:
        - object_id: фильтрация по объекту
        - name: поиск по имени свойства (точное совпадение)
        
        Примеры:
            /api/properties/?object_id=1
            /api/properties/?name=pressure
            /api/properties/?object_id=1&name=pressure
        """
        queryset = Property.objects.select_related('object', 'object__project').all()
        
        object_id = self.request.query_params.get('object_id', None)
        if object_id is not None:
            queryset = queryset.filter(object_id=object_id)
        
        name = self.request.query_params.get('name', None)
        if name is not None:
            queryset = queryset.filter(name=name)
        
        return queryset
    
    def perform_create(self, serializer):
        """Создание свойства с обработкой object_id"""
        object_id = self.request.data.get('object_id')
        if object_id:
            obj = get_object_or_404(Object, id=object_id)
            serializer.save(object=obj)
        else:
            serializer.save()

    @action(detail=False, methods=['patch'], url_path='bulk-update')
    def bulk_update(self, request):
        """
        Атомарное обновление нескольких свойств за один запрос.
        
        PATCH /api/properties/bulk-update/
        
        Тело запроса:
        {
            "properties": [
                {"id": 1, "value_boolean": true},
                {"id": 2, "value_number": 0.5},
                {"id": 3, "value_text": "some text"}
            ]
        }
        
        Или по имени (требуется object_id):
        {
            "object_id": 1,
            "properties": [
                {"name": "locked", "value_boolean": true},
                {"name": "pressure", "value_number": 0.5}
            ]
        }
        
        Возвращает:
        {
            "status": "success",
            "updated": 3,
            "properties": [...]
        }
        """
        from django.db import transaction
        
        properties_data = request.data.get('properties', [])
        object_id = request.data.get('object_id', None)
        
        if not properties_data:
            return Response(
                {'error': 'Параметр "properties" обязателен и должен быть непустым списком'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        updated_properties = []
        
        try:
            with transaction.atomic():
                for prop_data in properties_data:
                    # Находим свойство по ID или по имени
                    prop_id = prop_data.get('id')
                    prop_name = prop_data.get('name')
                    
                    if prop_id:
                        prop = get_object_or_404(Property, id=prop_id)
                    elif prop_name and object_id:
                        prop = get_object_or_404(Property, name=prop_name, object_id=object_id)
                    else:
                        return Response(
                            {'error': 'Каждое свойство должно иметь "id" или "name" (с object_id)'},
                            status=status.HTTP_400_BAD_REQUEST
                        )
                    
                    # Обновляем значения
                    if 'value_boolean' in prop_data:
                        prop.value_boolean = prop_data['value_boolean']
                    if 'value_number' in prop_data:
                        prop.value_number = prop_data['value_number']
                    if 'value_text' in prop_data:
                        prop.value_text = prop_data['value_text']
                    
                    prop.save()
                    updated_properties.append(prop)
        
        except Exception as e:
            return Response(
                {'error': f'Ошибка обновления: {str(e)}'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        serializer = PropertySerializer(updated_properties, many=True)
        return Response({
            'status': 'success',
            'updated': len(updated_properties),
            'properties': serializer.data
        })

    @action(detail=True, methods=['post'], url_path='change-value')
    def change_value(self, request, pk=None):
        """
        Запускает задачу Celery для постепенного изменения числового значения свойства.
        
        POST /api/properties/{id}/change-value/
        
        Тело запроса:
        {
            "target_value": 100.0,  # Целевое значение (обязательно)
            "step": 5.0,            # Шаг изменения (обязательно)
            "interval": 1.0         # Интервал между шагами в секундах (опционально, по умолчанию 1.0)
        }
        
        Возвращает:
        {
            "status": "started",
            "task_id": "...",
            "property_id": 1,
            "current_value": 0.0,
            "target_value": 100.0,
            "step": 5.0,
            "interval": 1.0
        }
        """
        property_obj = self.get_object()
        
        # Проверяем, что свойство имеет числовой тип
        if property_obj.property_type != 'number':
            return Response(
                {'error': f'Свойство должно иметь тип "number", текущий тип: "{property_obj.property_type}"'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Получаем параметры из запроса
        target_value = request.data.get('target_value')
        step = request.data.get('step')
        interval = request.data.get('interval', 1.0)
        
        # Валидация обязательных параметров
        if target_value is None:
            return Response(
                {'error': 'Параметр "target_value" обязателен'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        if step is None:
            return Response(
                {'error': 'Параметр "step" обязателен'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            target_value = float(target_value)
            step = float(step)
            interval = float(interval)
        except (TypeError, ValueError):
            return Response(
                {'error': 'Параметры "target_value", "step" и "interval" должны быть числами'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        if step <= 0:
            return Response(
                {'error': 'Параметр "step" должен быть положительным числом'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        if interval <= 0:
            return Response(
                {'error': 'Параметр "interval" должен быть положительным числом'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Запускаем задачу Celery
        task = change_value_gradually.delay(
            property_id=property_obj.id,
            target_value=target_value,
            step=step,
            interval=interval
        )
        
        return Response({
            'status': 'started',
            'task_id': task.id,
            'property_id': property_obj.id,
            'current_value': property_obj.value_number or 0.0,
            'target_value': target_value,
            'step': step,
            'interval': interval
        }, status=status.HTTP_202_ACCEPTED)


class TaskStatusView(APIView):
    """
    API для получения статуса и управления Celery задачами.
    """
    
    def get(self, request, task_id):
        """
        Получить статус Celery задачи.
        
        GET /api/tasks/{task_id}/
        
        Возвращает:
        {
            "task_id": "...",
            "status": "PENDING|STARTED|PROGRESS|SUCCESS|FAILURE|REVOKED",
            "result": {...},  # Результат или информация о прогрессе
            "ready": true/false
        }
        """
        result = AsyncResult(task_id)
        
        response_data = {
            'task_id': task_id,
            'status': result.status,
            'ready': result.ready(),
        }
        
        # Добавляем результат или информацию о прогрессе
        if result.status == 'PROGRESS':
            response_data['result'] = result.info
        elif result.ready():
            try:
                response_data['result'] = result.result
            except Exception as e:
                response_data['result'] = {'error': str(e)}
        
        return Response(response_data)
    
    def delete(self, request, task_id):
        """
        Отменить Celery задачу.
        
        DELETE /api/tasks/{task_id}/
        
        Query параметры:
        - terminate: true/false - принудительно завершить задачу (по умолчанию false)
        
        Возвращает:
        {
            "task_id": "...",
            "status": "revoked",
            "terminated": true/false
        }
        """
        from ucs.celery import app
        
        terminate = request.query_params.get('terminate', 'false').lower() == 'true'
        
        # Отменяем задачу
        app.control.revoke(task_id, terminate=terminate)
        
        return Response({
            'task_id': task_id,
            'status': 'revoked',
            'terminated': terminate
        })
