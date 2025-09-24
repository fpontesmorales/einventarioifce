from django.contrib import admin, messages
from django.urls import path
from django.template.response import TemplateResponse
from django import forms
from django.db import transaction
from django.shortcuts import redirect
from decimal import Decimal, InvalidOperation
import csv, io, re, datetime

from .models import Bem, Sala

# --------- Utils de normalização ----------
def _norm_str(v):
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    return re.sub(r"\s+", " ", s)

def _split_sala_bloco(sala_str):
    """
    Extrai o BLOCO como o *último* conteúdo entre parênteses no fim do texto da SALA.
    Ex.: "LAB METRO (LAB MÚSICA)(BLOCO ADMINISTRATIVO 01)"
         -> nome: "LAB METRO (LAB MÚSICA)"
         -> bloco: "BLOCO ADMINISTRATIVO 01"
    """
    s0 = _norm_str(sala_str)
    if not s0:
        return None, None
    if s0.endswith(")") and "(" in s0:
        i = s0.rfind("(")
        j = s0.rfind(")")
        if 0 <= i < j:
            bloco = _norm_str(s0[i+1:j])
            nome = _norm_str(s0[:i])
            return (nome or s0), bloco
    return s0, None

def _parse_date(v):
    s = _norm_str(v)
    if not s:
        return None
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None

def _parse_decimal(v):
    s = _norm_str(v)
    if not s:
        return None
    try:
        if "," in s and "." in s:
            if s.rfind(",") > s.rfind("."):
                s = s.replace(".", "").replace(",", ".")
            else:
                s = s.replace(",", "")
        elif "," in s:
            s = s.replace(",", ".")
        return Decimal(s)
    except (InvalidOperation, ValueError):
        return None

# --------- Admin de Sala ----------
@admin.register(Sala)
class SalaAdmin(admin.ModelAdmin):
    list_display = ("nome", "bloco", "setores", "total_bens")
    list_filter = ("bloco",)
    search_fields = ("nome", "bloco")

    def setores(self, obj):
        """
        Lista de setores (distintos) presentes nos Bens dessa sala.
        """
        setores = set()
        for s in Bem.objects.filter(sala__isnull=False).values_list("sala", "setor_responsavel"):
            nome, bloco = _split_sala_bloco(s[0])
            if nome == obj.nome and (bloco or None) == (obj.bloco or None):
                setor = _norm_str(s[1])
                if setor:
                    setores.add(setor)
        # ordena e junta numa string
        return ", ".join(sorted(setores)) if setores else "-"
    setores.short_description = "Setores"

    def total_bens(self, obj):
        count = 0
        for sala_txt in Bem.objects.filter(sala__isnull=False).values_list("sala", flat=True):
            nome, bloco = _split_sala_bloco(sala_txt)
            if nome == obj.nome and (bloco or None) == (obj.bloco or None):
                count += 1
        return count
    total_bens.short_description = "Bens"

# --------- Form de upload ----------
class UploadCSVForm(forms.Form):
    arquivo = forms.FileField(label="Arquivo CSV (SUAP)")

# --------- Mapeamento de cabeçalhos ----------
FIELD_MAP = {
    "NUMERO": "tombamento",
    "STATUS": "status",
    "ED": "ed",
    "CONTA CONTABIL": "conta_contabil",
    "DESCRICAO": "descricao",
    "RÓTULOS": "rotulos",
    "CARGA ATUAL": "carga_atual",
    "SETOR DO RESPONSÁVEL": "setor_responsavel",
    "CAMPUS DA CARGA": "campus_carga",
    "CARGA CONTÁBIL": "carga_contabil",
    "VALOR AQUISIÇÃO": "valor_aquisicao",
    "VALOR DEPRECIADO": "valor_depreciado",
    "NUMERO NOTA FISCAL": "numero_nota_fiscal",
    "NÚMERO DE SÉRIE": "numero_serie",
    "DATA DA ENTRADA": "data_entrada",
    "DATA DA CARGA": "data_carga",
    "FORNECEDOR": "fornecedor",
    "SALA": "sala",
    "ESTADO DE CONSERVAÇÃO": "estado_conservacao",
}
REQUIRED_HEADERS = {"NUMERO", "DESCRICAO"}

def _normalize_header(h):
    h = (h or "").strip()
    h = re.sub(r"\s+", " ", h)
    return h.upper()

def _rebuild_salas_from_pairs(desired_pairs):
    """
    Reconstrói Salas com base no conjunto de pares (nome, bloco).
    - Cria novas que faltam.
    - Remove as que não estiverem mais presentes.
    """
    desired = set()
    for nome, bloco in desired_pairs:
        n = _norm_str(nome)
        b = _norm_str(bloco)
        if n:
            desired.add((n, b))

    existentes = { (s.nome, (s.bloco or None)): s for s in Sala.objects.all() }
    keep = set(desired)

    # criar faltantes
    to_create = keep - set(existentes.keys())
    if to_create:
        Sala.objects.bulk_create([Sala(nome=n, bloco=b) for (n, b) in to_create])

    # remover obsoletas
    to_delete = set(existentes.keys()) - keep
    if to_delete:
        Sala.objects.filter(id__in=[existentes[k].id for k in to_delete]).delete()

# --------- Bem + Importação ----------
@admin.register(Bem)
class BemAdmin(admin.ModelAdmin):
    change_list_template = "admin/patrimonio/bem/change_list.html"

    list_display = ("tombamento", "descricao", "status", "setor_responsavel", "sala", "estado_conservacao", "valor_aquisicao")
    search_fields = ("tombamento", "descricao", "numero_serie", "setor_responsavel", "sala", "conta_contabil", "fornecedor", "numero_nota_fiscal")
    list_filter = ("status", "estado_conservacao", "setor_responsavel")
    ordering = ("tombamento",)

    def has_add_permission(self, request):
        return False  # apenas via CSV (edição manual ainda permitida)

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path("importar-csv/", self.admin_site.admin_view(self.importar_csv_view), name="patrimonio_bem_importar_csv"),
        ]
        return custom + urls

    def importar_csv_view(self, request):
        context = dict(self.admin_site.each_context(request), opts=self.model._meta, title="Importar CSV do SUAP")

        if request.method == "POST":
            form = UploadCSVForm(request.POST, request.FILES)
            if form.is_valid():
                f = form.cleaned_data["arquivo"]

                raw = f.read()
                try:
                    text = raw.decode("utf-8-sig", errors="strict")
                except UnicodeDecodeError:
                    text = raw.decode("latin-1")

                # Delimitador
                sample = "\n".join(text.splitlines()[:10])
                try:
                    import csv as _csv
                    dialect = _csv.Sniffer().sniff(sample, delimiters=",;")
                    delim = dialect.delimiter
                except Exception:
                    delim = "," if sample.count(",") >= sample.count(";") else ";"

                reader = csv.DictReader(io.StringIO(text), delimiter=delim)
                raw_headers = reader.fieldnames or []
                norm_headers = [_normalize_header(h) for h in raw_headers]

                # Cabeçalhos obrigatórios
                missing = [h for h in REQUIRED_HEADERS if h not in norm_headers]
                if missing:
                    messages.error(request, f"Cabeçalhos obrigatórios ausentes: {', '.join(missing)}.")
                    return TemplateResponse(request, "admin/patrimonio/bem/importar_csv.html", {**context, "form": form})

                norm_to_raw = { _normalize_header(h): h for h in raw_headers }
                header_to_field = { h: FIELD_MAP[h] for h in norm_headers if h in FIELD_MAP }

                criados = 0
                atualizados = 0
                erros = 0
                erros_msgs = []
                vistos_no_arquivo = set()
                linha_num = 1

                # pares (nome, bloco) desejados
                desired_salas = set()

                for row in reader:
                    linha_num += 1
                    try:
                        # pular linha totalmente vazia
                        if all((not _norm_str(row.get(h))) for h in raw_headers):
                            continue

                        data = {}
                        for hdr_norm, field in header_to_field.items():
                            raw_key = norm_to_raw.get(hdr_norm)
                            raw_val = row.get(raw_key)
                            if field in {"valor_aquisicao", "valor_depreciado"}:
                                data[field] = _parse_decimal(raw_val)
                            elif field in {"data_entrada", "data_carga"}:
                                data[field] = _parse_date(raw_val)
                            else:
                                data[field] = _norm_str(raw_val)

                        tomb = data.get("tombamento")
                        desc = data.get("descricao")
                        if not tomb or not desc:
                            raise ValueError("Campos obrigatórios faltando (NUMERO e/ou DESCRICAO).")

                        if tomb in vistos_no_arquivo:
                            raise ValueError(f"Tombamento duplicado no arquivo: {tomb}")
                        vistos_no_arquivo.add(tomb)

                        # cria/atualiza Bem
                        defaults = data.copy()
                        defaults.pop("tombamento", None)
                        with transaction.atomic():
                            obj, created = Bem.objects.update_or_create(
                                tombamento=tomb,
                                defaults=defaults,
                            )
                        if created:
                            criados += 1
                        else:
                            atualizados += 1

                        # acumula sala desejada (nome, bloco)
                        sala_txt = _norm_str(data.get("sala"))
                        nome_sala, bloco = _split_sala_bloco(sala_txt)
                        if nome_sala:
                            desired_salas.add((nome_sala, bloco))

                    except Exception as e:
                        erros += 1
                        if len(erros_msgs) < 10:
                            erros_msgs.append(f"Linha {linha_num}: {str(e)}")

                # Reconstruir Salas com base no desejado
                _rebuild_salas_from_pairs(desired_salas)

                # Mensagens finais
                messages.success(request, f"Importação concluída: {criados} criado(s), {atualizados} atualizado(s).")
                if erros:
                    msg = f"{erros} linha(s) com erro."
                    if erros_msgs:
                        msg += " Exemplos: " + " | ".join(erros_msgs)
                    messages.warning(request, msg)

                return redirect("admin:patrimonio_bem_changelist")

        else:
            form = UploadCSVForm()

        return TemplateResponse(request, "admin/patrimonio/bem/importar_csv.html", {**context, "form": form})
