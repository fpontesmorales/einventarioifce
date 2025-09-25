from django.urls import path
from . import views

app_name = "vistoria_public"

urlpatterns = [
    path("blocos/", views.blocos_view, name="blocos"),
    path("blocos/<path:bloco>/salas/", views.salas_por_bloco_view, name="salas_por_bloco"),
    path("salas/<int:sala_id>/", views.sala_workspace_view, name="sala_workspace"),

    # Ações
    path("salas/<int:sala_id>/vistoriar/", views.vistoriar_por_tombo, name="vistoriar_por_tombo"),  # POST: digitar tombo
    path("salas/<int:sala_id>/bem/<str:tombamento>/", views.vistoria_bem_form, name="vistoria_bem_form"),  # GET/POST
    path("salas/<int:sala_id>/bem/<str:tombamento>/nao-encontrei/", views.marcar_nao_encontrado, name="nao_encontrado"),  # POST
    path("salas/<int:sala_id>/extra/novo/", views.vistoria_extra_form, name="extra_novo"),  # GET/POST
]
