from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import ObjectViewSet, PropertyViewSet, TaskStatusView

router = DefaultRouter()
router.register(r'objects', ObjectViewSet, basename='object')
router.register(r'properties', PropertyViewSet, basename='property')

urlpatterns = [
    path('', include(router.urls)),
    path('tasks/<str:task_id>/', TaskStatusView.as_view(), name='task-status'),
]

