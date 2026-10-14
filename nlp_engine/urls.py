from django.urls import path
from . import views

app_name = 'nlp_engine'

urlpatterns = [
    path('search/', views.api_nlp_search, name='nlp_search'),
    path('command/', views.api_nlp_command, name='nlp_command'),
]
