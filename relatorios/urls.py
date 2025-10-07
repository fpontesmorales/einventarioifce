from django.urls import path
from . import views

app_name = "relatorios"

urlpatterns = [
    path("", views.index, name="index"),

    # Relatórios principais
    path("final/", views.relatorio_final, name="final"),
    path("operacional/", views.relatorio_operacional, name="operacional"),

    # Relatórios auxiliares (se você mantiver)
    path("inventario-por-conta/", views.inventario_por_conta, name="inventario_por_conta"),
    path("mapa-nao-conformidades/", views.mapa_nao_conformidades, name="mapa_nao_conformidades"),

    # Exportação de fotos (ZIP)
    path("exportar-fotos/", views.exportar_fotos, name="exportar_fotos"),
]
