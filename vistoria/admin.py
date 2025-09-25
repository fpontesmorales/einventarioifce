from django.contrib import admin
from .models import Inventario, VistoriaBem, VistoriaExtra


# ---------------- Inventário ----------------
@admin.register(Inventario)
class InventarioAdmin(admin.ModelAdmin):
    list_display = ("ano", "ativo", "incluir_livros", "criado_em", "atualizado_em")
    list_filter = ("ativo", "incluir_livros")
    ordering = ("-ano",)
    actions = ["ativar_unico", "desativar_selecionados"]

    @admin.action(description="Ativar (desativando os demais)")
    def ativar_unico(self, request, queryset):
        if queryset.exists():
            Inventario.objects.update(ativo=False)
            inv = queryset.order_by("-ano").first()
            inv.ativo = True
            inv.save()
            self.message_user(request, f"Inventário {inv.ano} ativado; os demais foram desativados.")

    @admin.action(description="Desativar selecionados")
    def desativar_selecionados(self, request, queryset):
        n = queryset.update(ativo=False)
        self.message_user(request, f"{n} inventário(s) desativado(s).")


# ---------------- Vistoria de Bem ----------------
@admin.register(VistoriaBem)
class VistoriaBemAdmin(admin.ModelAdmin):
    raw_id_fields = ("bem",)
    list_display = (
        "bem",
        "inventario",
        "status",
        "divergente",
        "movido",
        "etiqueta_possui",
        "etiqueta_condicao",
        "atualizado_em",
        "atualizado_por",
    )
    list_filter = (
        "inventario",
        "status",
        "divergente",
        "etiqueta_possui",
        "etiqueta_condicao",
    )
    search_fields = (
        "bem__tombamento",
        "bem__descricao",
        "sala_obs_nome",
        "sala_obs_bloco",
        "responsavel_obs",
    )
    readonly_fields = ("criado_em", "atualizado_em", "foto_preview")

    fieldsets = (
        (None, {
            "fields": ("inventario", "bem", "status", "divergente",
                       "criado_em", "atualizado_em", "criado_por", "atualizado_por")
        }),
        ("Conferências", {
            "fields": (
                "confere_descricao", "descricao_obs",
                "confere_numero_serie", "numero_serie_obs",
                "confere_local", "sala_obs_nome", "sala_obs_bloco",
                "confere_estado", "estado_obs",
                "confere_responsavel", "responsavel_obs",
            )
        }),
        ("Etiqueta e anotações", {
            "fields": ("etiqueta_possui", "etiqueta_condicao", "avaria_texto", "observacoes")
        }),
        ("Foto", {
            "fields": ("foto_marcadagua", "foto_preview")
        }),
    )

    @admin.display(boolean=True, description="Movido?")
    def movido(self, obj: VistoriaBem) -> bool:
        return obj.encontrado_em_outra_sala()

    @admin.display(description="Prévia")
    def foto_preview(self, obj: VistoriaBem):
        if obj and obj.foto_marcadagua:
            from django.utils.safestring import mark_safe
            return mark_safe(f'<img src="{obj.foto_marcadagua.url}" style="max-width:320px;height:auto;border:1px solid #ddd;border-radius:8px;" />')
        return "—"


# ---------------- Itens sem registro ----------------
@admin.register(VistoriaExtra)
class VistoriaExtraAdmin(admin.ModelAdmin):
    list_display = ("descricao_obs", "sala_obs_nome", "sala_obs_bloco", "inventario", "criado_em", "criado_por")
    list_filter = ("inventario", "sala_obs_bloco")
    search_fields = ("descricao_obs", "sala_obs_nome", "sala_obs_bloco", "responsavel_obs")
    readonly_fields = ("criado_em",)
