from django.db import models
from django.db.models import Q
from django.contrib.auth import get_user_model

User = get_user_model()

# --- Compat: funções usadas por migrações antigas ---
def upload_foto_bem(instance, filename):
    safe = str(filename).replace(" ", "_")
    return f"vistorias/{safe}"

def upload_foto_extra(instance, filename):
    safe = str(filename).replace(" ", "_")
    return f"vistorias/{safe}"
# -----------------------------------------------------


def split_sala_bloco(s: str | None):
    """
    Entrada: "NOME DA SALA (ALGO OPCIONAL)(BLOCO X)" -> ("NOME DA SALA (ALGO OPCIONAL)", "BLOCO X")
    Se não tiver bloco no sufixo, retorna (nome, None).
    """
    if not s:
        return (None, None)
    s = s.strip()
    if not s:
        return (None, None)
    if s.endswith(")") and "(" in s:
        try:
            ini = s.rindex("(")
            nome = s[:ini].strip()
            bloco = s[ini + 1 : -1].strip() or None
            return (nome or None, bloco)
        except ValueError:
            return (s, None)
    return (s, None)


class Inventario(models.Model):
    ano = models.IntegerField()
    ativo = models.BooleanField(default=False)
    incluir_livros = models.BooleanField(default=True)

    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-ano"]

    def __str__(self):
        return f"Inventário {self.ano} ({'Ativo' if self.ativo else 'Inativo'})"

    # ====== MÉTODOS QUE AS VIEWS USAM ======
    def bens_elegiveis_qs(self):
        """
        Bens no escopo do inventário:
        - exclui BAIXADOS (usa o(s) campo(s) existentes no modelo)
        - exclui livros (ED == '4490.52.18') quando incluir_livros=False
        """
        from patrimonio.models import Bem  # evitar import circular

        qs = Bem.objects.all()
        field_names = {f.name for f in Bem._meta.get_fields() if hasattr(f, "attname")}

        # Excluir baixados
        if "baixado" in field_names:
            qs = qs.filter(baixado=False)
        else:
            cond = Q()
            if "situacao" in field_names:
                cond |= Q(situacao__iexact="BAIXADO")
            if "status" in field_names:
                cond |= Q(status__iexact="BAIXADO")
            if cond:
                qs = qs.exclude(cond)

        # Excluir livros se necessário
        if not self.incluir_livros:
            if "ed" in field_names:
                qs = qs.exclude(ed__iexact="4490.52.18")
            elif "elemento_despesa" in field_names:
                qs = qs.exclude(elemento_despesa__iexact="4490.52.18")

        return qs

    def bem_e_elegivel(self, bem) -> bool:
        """Validação por objeto."""
        baixado = getattr(bem, "baixado", None)
        if baixado is True:
            return False
        situacao = (getattr(bem, "situacao", "") or getattr(bem, "status", "") or "").strip().upper()
        if situacao == "BAIXADO":
            return False

        if not self.incluir_livros:
            ed = (getattr(bem, "ed", "") or getattr(bem, "elemento_despesa", "") or "").strip()
            if ed == "4490.52.18":
                return False
        return True


class VistoriaBem(models.Model):
    class Status(models.TextChoices):
        ENCONTRADO = "ENCONTRADO", "Encontrado"
        NAO_ENCONTRADO = "NAO_ENCONTRADO", "Não encontrado"

    inventario = models.ForeignKey(Inventario, on_delete=models.CASCADE)
    bem = models.ForeignKey("patrimonio.Bem", on_delete=models.CASCADE)

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ENCONTRADO)

    # conferências
    confere_descricao = models.BooleanField(default=True)
    confere_numero_serie = models.BooleanField(default=True)
    confere_local = models.BooleanField(default=True)
    confere_estado = models.BooleanField(default=True)
    confere_responsavel = models.BooleanField(default=True)

    # observados quando NÃO confere
    descricao_obs = models.TextField(null=True, blank=True)
    numero_serie_obs = models.CharField(max_length=200, null=True, blank=True)
    sala_obs_nome = models.CharField(max_length=255, null=True, blank=True)
    sala_obs_bloco = models.CharField(max_length=255, null=True, blank=True)
    estado_obs = models.CharField(max_length=200, null=True, blank=True)
    responsavel_obs = models.CharField(max_length=200, null=True, blank=True)

    # etiqueta
    class EtiquetaCondicao(models.TextChoices):
        BOA = "BOA", "Boa"
        DANIFICADA = "DANIFICADA", "Danificada"
        ILEGIVEL = "ILEGIVEL", "Ilegível"

    etiqueta_possui = models.BooleanField(default=True)
    etiqueta_condicao = models.CharField(max_length=20, choices=EtiquetaCondicao.choices, null=True, blank=True)

    avaria_texto = models.TextField(null=True, blank=True)
    observacoes = models.TextField(null=True, blank=True)

    foto_marcadagua = models.ImageField(upload_to="vistorias/", null=True, blank=True)

    # Campo persistido no banco (NOT NULL em migração antiga)
    divergente = models.BooleanField(default=False)

    criado_por = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name="vistorias_criadas")
    atualizado_por = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name="vistorias_atualizadas")
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("inventario", "bem")]

    # ---- helpers ----
    def _recompute_divergente(self) -> bool:
        val = any([
            not self.confere_descricao,
            not self.confere_numero_serie,
            not self.confere_local,
            not self.confere_estado,
            not self.confere_responsavel,
        ])
        self.divergente = val
        return val

    def _suap_sala_tuple(self):
        return split_sala_bloco(self.bem.sala or "")

    def _obs_sala_tuple(self):
        nome = (self.sala_obs_nome or "").strip() or None
        bloco = (self.sala_obs_bloco or "").strip() or None
        if not nome and not bloco:
            return None
        return (nome, bloco)

    def encontrado_em_outra_sala(self) -> bool:
        """
        True apenas se:
        - status = ENCONTRADO,
        - confere_local = False,
        - e a sala observada for diferente da sala do SUAP.
        """
        if self.status != self.Status.ENCONTRADO:
            return False
        if self.confere_local:
            return False
        obs = self._obs_sala_tuple()
        if not obs:
            return False
        suap = self._suap_sala_tuple()

        def norm(t):
            n, b = t
            n = (n or "").strip() or None
            b = (b or "").strip() or None
            return (n, b)

        return norm(obs) != norm(suap)

    # Garante o preenchimento do campo divergente no banco
    def save(self, *args, **kwargs):
        self._recompute_divergente()
        super().save(*args, **kwargs)


class VistoriaExtra(models.Model):
    inventario = models.ForeignKey(Inventario, on_delete=models.CASCADE)
    descricao_obs = models.TextField()
    sala_obs_nome = models.CharField(max_length=255, null=True, blank=True)
    sala_obs_bloco = models.CharField(max_length=255, null=True, blank=True)
    numero_serie_obs = models.CharField(max_length=200, null=True, blank=True)
    estado_obs = models.CharField(max_length=200, null=True, blank=True)
    responsavel_obs = models.CharField(max_length=200, null=True, blank=True)

    etiqueta_possui = models.BooleanField(default=False)
    etiqueta_condicao = models.CharField(
        max_length=20, choices=VistoriaBem.EtiquetaCondicao.choices, null=True, blank=True
    )
    observacoes = models.TextField(null=True, blank=True)

    foto_marcadagua = models.ImageField(upload_to="vistorias/", null=True, blank=True)

    criado_por = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name="extras_criados")
    criado_em = models.DateTimeField(auto_now_add=True)
