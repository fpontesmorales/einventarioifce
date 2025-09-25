from django.conf import settings
from django.db import models
from django.db.models import Q
from datetime import datetime
import uuid

# --- Utilitário compartilhado (regra SALA → nome/bloco) ---
def split_sala_bloco(sala_str: str):
    """
    Extrai o BLOCO como o *último* conteúdo entre parênteses no fim do texto da SALA.
    Ex.: "LAB METRO (LAB MÚSICA)(BLOCO ADMINISTRATIVO 01)"
         -> nome: "LAB METRO (LAB MÚSICA)"
         -> bloco: "BLOCO ADMINISTRATIVO 01"
    """
    if not sala_str:
        return None, None
    s0 = str(sala_str).strip()
    if s0.endswith(")") and "(" in s0:
        i = s0.rfind("(")
        j = s0.rfind(")")
        if 0 <= i < j:
            bloco = s0[i + 1 : j].strip() or None
            nome = (s0[:i].strip() or s0)
            return nome, bloco
    return s0, None


def upload_foto_bem(instance, filename: str):
    # Guarda apenas a foto COM marca d'água (original não é salvo)
    ext = filename.split(".")[-1].lower() if "." in filename else "jpg"
    ano = instance.inventario.ano if instance.inventario_id else datetime.now().year
    tomb = instance.bem.tombamento if instance.bem_id else "sem_tombo"
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    fname = f"{ts}_{uuid.uuid4().hex[:8]}.{ext}"
    return f"inventarios/{ano}/bens/{tomb}/{fname}"


def upload_foto_extra(instance, filename: str):
    ext = filename.split(".")[-1].lower() if "." in filename else "jpg"
    ano = instance.inventario.ano if instance.inventario_id else datetime.now().year
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    fname = f"{ts}_{uuid.uuid4().hex[:8]}.{ext}"
    return f"inventarios/{ano}/extras/{fname}"


# ------------ Inventário (1 ativo por vez) ------------
class Inventario(models.Model):
    ano = models.PositiveIntegerField("Ano", unique=True)
    ativo = models.BooleanField("Ativo", default=False)
    incluir_livros = models.BooleanField(
        "Incluir LIVROS (ED = 4490.52.18) no inventário?",
        default=False,
        help_text="Quando desmarcado, livros ficam fora do escopo da campanha."
    )
    data_inicio = models.DateField("Início", blank=True, null=True)
    data_fim = models.DateField("Fim", blank=True, null=True)

    criado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="Criado por",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="inventarios_criados",
    )
    criado_em = models.DateTimeField("Criado em", auto_now_add=True)
    atualizado_em = models.DateTimeField("Atualizado em", auto_now=True)

    class Meta:
        verbose_name = "Inventário"
        verbose_name_plural = "Inventários"
        constraints = [
            # Permite no máximo UMA linha com ativo=True
            models.UniqueConstraint(
                fields=["ativo"],
                condition=Q(ativo=True),
                name="unico_inventario_ativo",
            ),
        ]
        ordering = ["-ano"]

    def __str__(self):
        return f"Inventário {self.ano} ({'Ativo' if self.ativo else 'Inativo'})"

    # --------- Escopo: quem entra na campanha ---------
    def bens_elegiveis_qs(self):
        """
        Retorna queryset de Bens elegíveis:
        - exclui STATUS 'baixado' (case-insensitive)
        - exclui LIVROS (ED = 4490.52.18) quando incluir_livros=False
        """
        from patrimonio.models import Bem  # import local p/ evitar ciclos
        qs = Bem.objects.all()
        qs = qs.exclude(status__iexact="baixado")
        if not self.incluir_livros:
            qs = qs.exclude(ed__iexact="4490.52.18")
        return qs

    def bem_e_elegivel(self, bem) -> bool:
        """Checagem rápida para um único Bem (sem consultar o banco de novo)."""
        status = (bem.status or "").strip().lower()
        if status == "baixado":
            return False
        ed = (bem.ed or "").strip()
        if not self.incluir_livros and ed.lower() == "4490.52.18".lower():
            return False
        return True


# ------------ Vistoria de Bem (vinculado ao SUAP) ------------
class VistoriaBem(models.Model):
    class Status(models.TextChoices):
        ENCONTRADO = "ENCONTRADO", "Encontrado"
        NAO_ENCONTRADO = "NAO_ENCONTRADO", "Não encontrado"

    class EtiquetaCondicao(models.TextChoices):
        BOA = "BOA", "Boa"
        RASURADA = "RASURADA", "Rasurada"
        DESCOLANDO = "DESCOLANDO", "Descolando"
        FORA_PADRAO = "FORA_PADRAO", "Fora do padrão"
        DIFICIL_VISUALIZACAO = "DIFICIL_VISUALIZACAO", "Difícil visualização"

    inventario = models.ForeignKey(
        "vistoria.Inventario",
        verbose_name="Inventário",
        on_delete=models.CASCADE,
        related_name="vistorias_bens",
    )
    bem = models.ForeignKey(
        "patrimonio.Bem",
        verbose_name="Bem (SUAP)",
        on_delete=models.CASCADE,
        related_name="vistorias",
    )

    # Foto com marca d'água (apenas a versão marcada é salva)
    foto_marcadagua = models.FileField("Foto do bem (marcada)", upload_to=upload_foto_bem, blank=True, null=True)

    # Resultado principal
    status = models.CharField("Resultado", max_length=20, choices=Status.choices, default=Status.ENCONTRADO)

    # Percepção do vistoriador sobre conferências (pré-preenchido como True)
    confere_descricao = models.BooleanField("Descrição confere com SUAP?", default=True)
    confere_numero_serie = models.BooleanField("Número de série confere com SUAP?", default=True)
    confere_local = models.BooleanField("Local confere com SUAP?", default=True)
    confere_estado = models.BooleanField("Estado de conservação confere com SUAP?", default=True)
    confere_responsavel = models.BooleanField("Responsável/carga confere com SUAP?", default=True)

    # Observados (preenchidos quando alguma conferência for 'Não')
    descricao_obs = models.TextField("Descrição observada (se divergente)", blank=True, null=True)
    numero_serie_obs = models.CharField("Número de série observado (se divergente)", max_length=255, blank=True, null=True)
    sala_obs_nome = models.CharField("Sala observada (se divergente)", max_length=255, blank=True, null=True)
    sala_obs_bloco = models.CharField("Bloco observado (se divergente)", max_length=64, blank=True, null=True)
    estado_obs = models.CharField("Estado observado (se divergente)", max_length=64, blank=True, null=True)
    responsavel_obs = models.CharField("Responsável/usuário observado (se divergente)", max_length=255, blank=True, null=True)

    # Etiqueta (informação complementar, não entra no cálculo de divergência)
    etiqueta_possui = models.BooleanField("Possui etiqueta?", default=True)
    etiqueta_condicao = models.CharField(
        "Condição da etiqueta",
        max_length=32,
        choices=EtiquetaCondicao.choices,
        blank=True,
        null=True,
    )

    avaria_texto = models.TextField("Avaria (se houver)", blank=True, null=True)
    observacoes = models.TextField("Observações", blank=True, null=True)

    # Sinalizador final
    divergente = models.BooleanField("Possui divergência?", default=False, db_index=True)

    # Auditoria
    criado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="Criado por",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="vistorias_bem_criadas",
    )
    atualizado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="Atualizado por",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="vistorias_bem_atualizadas",
    )
    criado_em = models.DateTimeField("Criado em", auto_now_add=True)
    atualizado_em = models.DateTimeField("Atualizado em", auto_now=True)

    class Meta:
        verbose_name = "Vistoria de Bem"
        verbose_name_plural = "Vistorias de Bens"
        unique_together = (("inventario", "bem"),)  # uma por bem na campanha
        indexes = [
            models.Index(fields=["inventario", "status"]),
            models.Index(fields=["inventario", "divergente"]),
        ]
        ordering = ["-atualizado_em"]

    def __str__(self):
        return f"{self.bem.tombamento} — {self.get_status_display()}"

    # --- utilitários de comparação ---
    def suap_nome_bloco(self):
        if not self.bem_id:
            return None, None
        return split_sala_bloco(self.bem.sala or "")

    def encontrado_em_outra_sala(self) -> bool:
        """True se ENCONTRADO e sala observada difere da sala do SUAP."""
        if self.status != self.Status.ENCONTRADO:
            return False
        suap_nome, suap_bloco = self.suap_nome_bloco()
        obs_nome = (self.sala_obs_nome or "").strip() or None
        obs_bloco = (self.sala_obs_bloco or "").strip() or None
        return (obs_nome, obs_bloco) != (suap_nome, suap_bloco)

    def recomputar_divergencia(self):
        self.divergente = not (
            self.confere_descricao
            and self.confere_numero_serie
            and self.confere_local
            and self.confere_estado
            and self.confere_responsavel
        )

    def save(self, *args, **kwargs):
        # Divergência automática
        self.recomputar_divergencia()
        super().save(*args, **kwargs)


# ------------ Vistoria Extra (item sem registro no SUAP) ------------
class VistoriaExtra(models.Model):
    inventario = models.ForeignKey(
        "vistoria.Inventario",
        verbose_name="Inventário",
        on_delete=models.CASCADE,
        related_name="vistorias_extras",
    )

    # Foto com marca d'água (apenas versão final)
    foto_marcadagua = models.FileField("Foto do bem (marcada)", upload_to=upload_foto_extra)

    # Descrição e dados observados
    descricao_obs = models.TextField("Descrição observada")
    sala_obs_nome = models.CharField("Sala observada", max_length=255)
    sala_obs_bloco = models.CharField("Bloco observado", max_length=64, blank=True, null=True)
    numero_serie_obs = models.CharField("Número de série observado", max_length=255, blank=True, null=True)
    estado_obs = models.CharField("Estado observado", max_length=64, blank=True, null=True)
    responsavel_obs = models.CharField("Responsável/usuário observado", max_length=255, blank=True, null=True)

    etiqueta_possui = models.BooleanField("Possui etiqueta?", default=False)
    etiqueta_condicao = models.CharField(
        "Condição da etiqueta",
        max_length=32,
        choices=VistoriaBem.EtiquetaCondicao.choices,
        blank=True,
        null=True,
    )

    observacoes = models.TextField("Observações", blank=True, null=True)

    criado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="Criado por",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="vistorias_extra_criadas",
    )
    criado_em = models.DateTimeField("Criado em", auto_now_add=True)

    class Meta:
        verbose_name = "Vistoria (sem registro SUAP)"
        verbose_name_plural = "Vistorias (sem registro SUAP)"
        ordering = ["-criado_em"]

    def __str__(self):
        return f"Extra — {self.descricao_obs[:50]}"
