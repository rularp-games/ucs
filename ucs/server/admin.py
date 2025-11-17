from django.contrib import admin
from .models import Project, Object, Property


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ('name', 'created_at', 'updated_at')
    list_filter = ('created_at', 'updated_at')
    search_fields = ('name', 'description')
    readonly_fields = ('created_at', 'updated_at')
    fieldsets = (
        ('Основная информация', {
            'fields': ('name', 'description')
        }),
        ('Даты', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )


@admin.register(Object)
class ObjectAdmin(admin.ModelAdmin):
    list_display = ('name', 'project', 'description')
    list_filter = ('project',)
    search_fields = ('name', 'description', 'project__name')
    autocomplete_fields = ('project',)


@admin.register(Property)
class PropertyAdmin(admin.ModelAdmin):
    list_display = ('name', 'project', 'property_type', 'get_value_display', 'created_at')
    list_filter = ('property_type', 'project', 'created_at')
    search_fields = ('name', 'description', 'project__name')
    readonly_fields = ('created_at', 'updated_at')
    autocomplete_fields = ('project',)
    fieldsets = (
        ('Основная информация', {
            'fields': ('project', 'name', 'description', 'property_type')
        }),
        ('Значения', {
            'fields': ('value_number', 'value_boolean', 'value_text'),
            'description': 'Заполните только одно поле в зависимости от типа свойства'
        }),
        ('Даты', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    
    def get_value_display(self, obj):
        """Отображает значение свойства в списке"""
        return obj.get_value()
    get_value_display.short_description = 'Значение'
