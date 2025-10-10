# relatorios/urls.py
from django.urls import path
from . import execucao
from .views import relatorio_final, relatorio_operacional, inventario_por_conta, mapa_nao_conformidades, exportar_fotos

app_name = "relatorios"

urlpatterns = [
    path("final/", relatorio_final, name="final"),
    path("operacional/", relatorio_operacional, name="operacional"),
    path("execucao/", execucao.relatorio_execucao, name="execucao"),  # <<< NOVO
    path("inventario-por-conta/", inventario_por_conta, name="inventario_por_conta"),
    path("mapa-nc/", mapa_nao_conformidades, name="mapa_nc"),
    path("exportar-fotos/", exportar_fotos, name="exportar_fotos"),
]
