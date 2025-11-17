from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from .models import Object
from .serializers import ObjectSerializer


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
        Опциональная фильтрация по project_id через query параметр
        Пример: /api/objects/?project_id=1
        """
        queryset = Object.objects.select_related('project').all()
        project_id = self.request.query_params.get('project_id', None)
        if project_id is not None:
            queryset = queryset.filter(project_id=project_id)
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
