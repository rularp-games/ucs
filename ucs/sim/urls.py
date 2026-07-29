from django.urls import path

from . import views

app_name = 'sim'

urlpatterns = [
    path('', views.index, name='index'),
    path('table/', views.table, name='table'),
    path('restart/', views.restart, name='restart'),
    path('report/', views.report, name='report'),
]
