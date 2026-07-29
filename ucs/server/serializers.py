from rest_framework import serializers
from .models import Object, Project, Property


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


class PropertySerializer(serializers.ModelSerializer):
    """Сериализатор для модели Property"""
    object = ObjectSerializer(read_only=True)
    object_id = serializers.IntegerField(write_only=True, required=False)
    value = serializers.SerializerMethodField()
    
    class Meta:
        model = Property
        fields = [
            'id', 'object', 'object_id', 'name', 'description', 
            'property_type', 'value_number', 'value_boolean', 
            'value_text', 'value', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']
    
    def get_value(self, obj):
        """Возвращает значение свойства в зависимости от типа"""
        return obj.get_value()

