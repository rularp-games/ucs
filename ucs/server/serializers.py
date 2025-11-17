from rest_framework import serializers
from .models import Object, Project


class ProjectSerializer(serializers.ModelSerializer):
    """Сериализатор для модели Project"""
    class Meta:
        model = Project
        fields = ['id', 'name', 'description', 'created_at', 'updated_at']


class ObjectSerializer(serializers.ModelSerializer):
    """Сериализатор для модели Object"""
    project = ProjectSerializer(read_only=True)
    project_id = serializers.IntegerField(write_only=True, required=False)
    
    class Meta:
        model = Object
        fields = ['id', 'project', 'project_id', 'name', 'description']
        read_only_fields = ['id']

