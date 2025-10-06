from django.contrib import admin
from .models import RelatorioConfig


@admin.register(RelatorioConfig)
class RelatorioConfigAdmin(admin.ModelAdmin):
    list_display = ("inventario", "atualizado_em")
    search_fields = ("inventario__ano",)
