from django.urls import path
from . import views

app_name = "relatorios"

urlpatterns = [
    # Relatório Final (novo)
    path("final/", views.relatorio_final, name="final"),

    # Já existentes:
    path("inventario-por-conta/", views.inventario_por_conta, name="inventario_por_conta"),
    path("mapa-nao-conformidades/", views.mapa_nao_conformidades, name="mapa_nao_conformidades"),

    # Opcional: índice simples
    path("", views.inventario_por_conta, name="index"),
]
