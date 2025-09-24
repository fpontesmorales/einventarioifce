from django.db import models

class Sala(models.Model):
    nome = models.CharField("Sala", max_length=255)
    bloco = models.CharField("Bloco", max_length=64, blank=True, null=True)

    class Meta:
        verbose_name = "Sala"
        verbose_name_plural = "Salas"
        unique_together = (("nome", "bloco"),)
        ordering = ["nome"]

    def __str__(self):
        return f"{self.nome} — {self.bloco}" if self.bloco else self.nome

class Bem(models.Model):
    # Mapeamento 1:1 com o cabeçalho do CSV do SUAP
    tombamento = models.CharField("NUMERO", max_length=50, unique=True, db_index=True)  # NUMERO
    status = models.CharField("STATUS", max_length=32, blank=True, null=True)  # STATUS
    ed = models.CharField("ED", max_length=64, blank=True, null=True)  # ED
    conta_contabil = models.CharField("CONTA CONTABIL", max_length=64, blank=True, null=True)  # CONTA CONTABIL
    descricao = models.TextField("DESCRICAO")  # DESCRICAO
    rotulos = models.TextField("RÓTULOS", blank=True, null=True)  # RÓTULOS
    carga_atual = models.CharField("CARGA ATUAL", max_length=255, blank=True, null=True)  # CARGA ATUAL
    setor_responsavel = models.CharField("SETOR DO RESPONSÁVEL", max_length=255, blank=True, null=True, db_index=True)  # SETOR DO RESPONSÁVEL
    campus_carga = models.CharField("CAMPUS DA CARGA", max_length=255, blank=True, null=True)  # CAMPUS DA CARGA
    carga_contabil = models.CharField("CARGA CONTÁBIL", max_length=255, blank=True, null=True)  # CARGA CONTÁBIL
    valor_aquisicao = models.DecimalField("VALOR AQUISIÇÃO", max_digits=14, decimal_places=2, blank=True, null=True)  # VALOR AQUISIÇÃO
    valor_depreciado = models.DecimalField("VALOR DEPRECIADO", max_digits=14, decimal_places=2, blank=True, null=True)  # VALOR DEPRECIADO
    numero_nota_fiscal = models.CharField("NUMERO NOTA FISCAL", max_length=64, blank=True, null=True)  # NUMERO NOTA FISCAL
    numero_serie = models.CharField("NÚMERO DE SÉRIE", max_length=128, blank=True, null=True, db_index=True)  # NÚMERO DE SÉRIE
    data_entrada = models.DateField("DATA DA ENTRADA", blank=True, null=True)  # DATA DA ENTRADA
    data_carga = models.DateField("DATA DA CARGA", blank=True, null=True)  # DATA DA CARGA
    fornecedor = models.CharField("FORNECEDOR", max_length=255, blank=True, null=True)  # FORNECEDOR
    sala = models.CharField("SALA", max_length=255, blank=True, null=True, db_index=True)  # SALA (texto do SUAP)
    estado_conservacao = models.CharField("ESTADO DE CONSERVAÇÃO", max_length=32, blank=True, null=True)  # ESTADO DE CONSERVAÇÃO

    # Meta e trilhas
    criado_em = models.DateTimeField("Criado em", auto_now_add=True)
    atualizado_em = models.DateTimeField("Atualizado em", auto_now=True)

    class Meta:
        verbose_name = "Bem"
        verbose_name_plural = "Bens"
        ordering = ["tombamento"]

    def __str__(self):
        return f"{self.tombamento} — {self.descricao[:60] if self.descricao else ''}"
