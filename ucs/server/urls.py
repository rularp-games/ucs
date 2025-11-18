from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import ObjectViewSet, PropertyViewSet

router = DefaultRouter()
router.register(r'objects', ObjectViewSet, basename='object')
router.register(r'properties', PropertyViewSet, basename='property')

urlpatterns = [
    path('', include(router.urls)),
]

