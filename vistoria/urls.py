from django.urls import path
from . import views

app_name = "vistoria_public"

urlpatterns = [
    # LISTAGENS
    path("blocos/", views.blocos_view, name="blocos"),

    # --- ROTAS DE SALA (ESPECÍFICAS) - precisam vir ANTES da catch-all ---
    path("salas/id/<int:sala_id>/", views.sala_workspace_view, name="sala_workspace"),
    path("salas/<int:sala_id>/vistoriar/", views.vistoriar_por_tombo, name="vistoriar_por_tombo"),
    path("salas/<int:sala_id>/bem/<str:tombamento>/", views.vistoria_bem_form, name="vistoria_bem_form"),
    path("salas/<int:sala_id>/nao-encontrado/<str:tombamento>/", views.marcar_nao_encontrado, name="nao_encontrado"),
    path("salas/<int:sala_id>/extra/novo/", views.vistoria_extra_form, name="extra_novo"),

    # --- CATCH-ALL por BLOCO (DEIXE POR ÚLTIMO) ---
    path("salas/<path:bloco>/", views.salas_por_bloco_view, name="salas_por_bloco"),

    # RELATÓRIOS (CSV)
    path("relatorios/resumo.csv", views.relatorio_resumo_csv, name="relatorio_resumo_csv"),
    path("relatorios/detalhes.csv", views.relatorio_detalhes_csv, name="relatorio_detalhes_csv"),
]
