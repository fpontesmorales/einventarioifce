from django.urls import path
from . import views

app_name = "relatorios"

urlpatterns = [
    path("", views.index, name="index"),
    path("final/", views.relatorio_final, name="final"),
    path("inventario-por-conta/", views.inventario_por_conta, name="inventario_por_conta"),
    path("mapa-nao-conformidades/", views.mapa_nao_conformidades, name="mapa_nao_conformidades"),

    # ETAPA B — Operacional
    path("operacional/", views.relatorio_operacional, name="operacional"),
    path("operacional/fotos.zip", views.operacional_fotos_zip, name="operacional_fotos_zip"),
]
