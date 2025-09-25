from django.contrib import admin, messages
from django.db import transaction
from django.utils.html import format_html

from .models import Inventario, VistoriaBem, VistoriaExtra, split_sala_bloco

# -------- Inventário --------
@admin.register(Inventario)
class InventarioAdmin(admin.ModelAdmin):
    list_display = ("ano", "ativo", "incluir_livros", "data_inicio", "data_fim", "criado_por", "criado_em")
    list_filter = ("ativo", "incluir_livros")
    search_fields = ("ano",)
    actions = ["ativar", "desativar", "habilitar_livros", "desabilitar_livros"]
    fields = ("ano", "ativo", "incluir_livros", "data_inicio", "data_fim")  # mostra o checkbox no form

    def save_model(self, request, obj, form, change):
        # garante 1 ativo por vez de forma amigável
        if obj.ativo:
            with transaction.atomic():
                Inventario.objects.exclude(pk=obj.pk).update(ativo=False)
                obj.criado_por = obj.criado_por or request.user
                super().save_model(request, obj, form, change)
        else:
            obj.criado_por = obj.criado_por or request.user
            super().save_model(request, obj, form, change)

    @admin.action(description="Ativar inventário (desativa os demais)")
    def ativar(self, request, queryset):
        if queryset.count() != 1:
            self.message_user(request, "Selecione exatamente um inventário para ativar.", level=messages.WARNING)
            return
        inv = queryset.first()
        with transaction.atomic():
            Inventario.objects.exclude(pk=inv.pk).update(ativo=False)
            inv.ativo = True
            inv.save()
        self.message_user(request, f"Inventário {inv.ano} ativado com sucesso.", level=messages.SUCCESS)

    @admin.action(description="Desativar inventário")
    def desativar(self, request, queryset):
        updated = queryset.update(ativo=False)
        self.message_user(request, f"{updated} inventário(s) desativado(s).", level=messages.SUCCESS)

    @admin.action(description="Habilitar livros (ED 4490.52.18)")
    def habilitar_livros(self, request, queryset):
        updated = queryset.update(incluir_livros=True)
        self.message_user(request, f"Livros habilitados em {updated} inventário(s).", level=messages.SUCCESS)

    @admin.action(description="Desabilitar livros (ED 4490.52.18)")
    def desabilitar_livros(self, request, queryset):
        updated = queryset.update(incluir_livros=False)
        self.message_user(request, f"Livros desabilitados em {updated} inventário(s).", level=messages.SUCCESS)


# -------- Vistoria de Bem --------
@admin.register(VistoriaBem)
class VistoriaBemAdmin(admin.ModelAdmin):
    list_display = (
        "inventario",
        "bem_link",
        "status",
        "divergente",
        "sala_suap_fmt",
        "sala_obs_fmt",
        "atualizado_em",
        "atualizado_por",
    )
    list_filter = ("inventario", "status", "divergente")
    search_fields = ("bem__tombamento", "bem__descricao", "sala_obs_nome", "sala_obs_bloco")
    autocomplete_fields = ("bem",)
    readonly_fields = ("criado_em", "atualizado_em")

    def bem_link(self, obj):
        return format_html("<strong>{}</strong> — {}", obj.bem.tombamento, obj.bem.descricao[:80])
    bem_link.short_description = "Bem"

    def sala_suap_fmt(self, obj):
        nome, bloco = split_sala_bloco(obj.bem.sala or "")
        if nome:
            return f"{nome} — {bloco}" if bloco else nome
        return "-"
    sala_suap_fmt.short_description = "Sala (SUAP)"

    def sala_obs_fmt(self, obj):
        if obj.sala_obs_nome:
            return f"{obj.sala_obs_nome} — {obj.sala_obs_bloco}" if obj.sala_obs_bloco else obj.sala_obs_nome
        return "-"
    sala_obs_fmt.short_description = "Sala observada"


# -------- Vistoria Extra (sem SUAP) --------
@admin.register(VistoriaExtra)
class VistoriaExtraAdmin(admin.ModelAdmin):
    list_display = ("inventario", "descricao_curta", "sala_fmt", "criado_por", "criado_em")
    list_filter = ("inventario",)
    search_fields = ("descricao_obs", "sala_obs_nome", "sala_obs_bloco")

    def descricao_curta(self, obj):
        return (obj.descricao_obs or "")[:80]
    descricao_curta.short_description = "Descrição"

    def sala_fmt(self, obj):
        if obj.sala_obs_nome:
            return f"{obj.sala_obs_nome} — {obj.sala_obs_bloco}" if obj.sala_obs_bloco else obj.sala_obs_nome
        return "-"
    sala_fmt.short_description = "Sala"
