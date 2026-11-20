from django.urls import path

from . import views

app_name = 'ls'

urlpatterns = [
    path('', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('post/', views.post, name='post'),
    path('post/model/<str:param>/', views.model, name='model'),
    path('post/codes/', views.codes, name='codes'),
    path('post/cards/', views.cards, name='cards'),
    path('post/known/', views.known, name='known'),
    path('radio/', views.radio, name='radio'),
    path('master/', views.master_index, name='master_index'),
    path('master/<int:game_id>/', views.master_game, name='master_game'),
    path('master/<int:game_id>/action/', views.master_action, name='master_action'),
    path('master/<int:game_id>/player/<int:player_id>/', views.master_player, name='master_player'),
]
