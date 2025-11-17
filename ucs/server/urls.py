from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import ObjectViewSet

router = DefaultRouter()
router.register(r'objects', ObjectViewSet, basename='object')

urlpatterns = [
    path('', include(router.urls)),
]

