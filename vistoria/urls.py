from django.urls import path
from . import views

app_name = "vistoria_public"

urlpatterns = [
    # Navegação principal
    path("blocos/", views.blocos_view, name="blocos"),
    path("salas/<str:bloco>/", views.salas_por_bloco_view, name="salas_por_bloco"),
    path("salas/id/<int:sala_id>/", views.sala_workspace_view, name="sala_workspace"),

    # Ações na sala
    path("salas/<int:sala_id>/tombo/", views.vistoriar_por_tombo, name="vistoriar_por_tombo"),

    # Vistoria de bem (com tombo)
    path("salas/<int:sala_id>/bem/<str:tombamento>/", views.vistoria_bem_form, name="vistoria_bem_form"),
    path("salas/<int:sala_id>/bem/<str:tombamento>/nao-encontrado/", views.marcar_nao_encontrado, name="marcar_nao_encontrado"),

    # Itens sem registro (extras)
    path("salas/<int:sala_id>/extra/novo/", views.vistoria_extra_form, name="extra_novo"),
    path("salas/<int:sala_id>/extra/<int:extra_id>/", views.vistoria_extra_detalhe, name="vistoria_extra_detalhe"),
]
