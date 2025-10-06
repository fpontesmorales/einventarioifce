from django.db import models
from django.utils import timezone
from vistoria.models import Inventario


class RelatorioConfig(models.Model):
    """Configurações persistidas por campanha (inventário)."""
    inventario = models.OneToOneField(
        Inventario, on_delete=models.CASCADE, related_name="config_relatorio"
    )
    # Upload do brasão/logo da capa
    logo = models.ImageField(upload_to="relatorios/logos/", null=True, blank=True)

    # Textos base (pré-carrego com os de 2024 depois, você pode editar)
    texto_apresentacao = models.TextField(blank=True, default="")
    texto_metodologia = models.TextField(blank=True, default="")
    texto_conclusao = models.TextField(blank=True, default="")

    # Lista de assinantes (nome/cargo) – armazeno como JSON
    # Ex.: [{"nome": "Fulano", "cargo": "Presidente da Comissão"}, ...]
    assinantes = models.JSONField(default=list, blank=True)

    # Opções (checkboxes) do relatório final
    incluir_mapa_nc = models.BooleanField(default=True)
    incluir_sem_registro = models.BooleanField(default=False)
    ocultar_contas_zeradas = models.BooleanField(default=True)
    ordenar_anexos = models.CharField(
        max_length=20, choices=[("valor", "Por valor"), ("conta", "Por conta")], default="conta"
    )

    atualizado_em = models.DateTimeField(auto_now=True)
    criado_em = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return f"Config Relatório – {self.inventario.ano}"
