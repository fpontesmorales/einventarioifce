from django.contrib import admin, messages
from django.urls import path
from django.template.response import TemplateResponse
from django import forms
from django.db import transaction
from django.shortcuts import redirect
from decimal import Decimal, InvalidOperation
import csv, io, re, datetime

from .models import Bem

# --- Form de upload ---
class UploadCSVForm(forms.Form):
    arquivo = forms.FileField(label="Arquivo CSV (SUAP)")

# --- Helpers de parsing/normalização ---
def _norm_str(v):
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    # colapsa espaços múltiplos
    return re.sub(r"\s+", " ", s)

def _parse_date(v):
    s = _norm_str(v)
    if not s:
        return None
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None  # data inválida → ignora (não quebra import)

def _parse_decimal(v):
    s = _norm_str(v)
    if not s:
        return None
    try:
        # Heurística: último separador é decimal
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

# Cabeçalhos esperados -> campos do modelo
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
REQUIRED_HEADERS = {"NUMERO", "DESCRICAO"}  # mínimo para operar

def _normalize_header(h):
    # Normaliza para bater com FIELD_MAP: uppercase e espaços simples
    h = (h or "").strip()
    h = re.sub(r"\s+", " ", h)
    return h.upper()

@admin.register(Bem)
class BemAdmin(admin.ModelAdmin):
    change_list_template = "admin/patrimonio/bem/change_list.html"

    list_display = ("tombamento", "descricao", "status", "setor_responsavel", "sala", "estado_conservacao", "valor_aquisicao")
    search_fields = ("tombamento", "descricao", "numero_serie", "setor_responsavel", "sala", "conta_contabil", "fornecedor", "numero_nota_fiscal")
    list_filter = ("status", "estado_conservacao", "setor_responsavel")
    ordering = ("tombamento",)

    # Desabilita criação manual: apenas via CSV (edição manual continua permitida)
    def has_add_permission(self, request):
        return False

    # URL custom para importar CSV
    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "importar-csv/",
                self.admin_site.admin_view(self.importar_csv_view),
                name="patrimonio_bem_importar_csv",
            ),
        ]
        return custom + urls

    def importar_csv_view(self, request):
        context = dict(
            self.admin_site.each_context(request),
            opts=self.model._meta,
            title="Importar CSV do SUAP",
        )

        if request.method == "POST":
            form = UploadCSVForm(request.POST, request.FILES)
            if form.is_valid():
                f = form.cleaned_data["arquivo"]

                # Lê bytes e decodifica como UTF-8 (aceita BOM); fallback latin-1
                raw = f.read()
                try:
                    text = raw.decode("utf-8-sig", errors="strict")
                except UnicodeDecodeError:
                    text = raw.decode("latin-1")

                # Detectar delimitador: tenta Sniffer; se falhar, conta vírgulas vs ponto-e-vírgulas
                sample = "\n".join(text.splitlines()[:10])
                try:
                    dialect = csv.Sniffer().sniff(sample, delimiters=",;")
                    delim = dialect.delimiter
                except csv.Error:
                    delim = "," if sample.count(",") >= sample.count(";") else ";"

                reader = csv.DictReader(io.StringIO(text), delimiter=delim)
                raw_headers = reader.fieldnames or []
                norm_headers = [_normalize_header(h) for h in raw_headers]

                # Validação de cabeçalhos obrigatórios
                missing = [h for h in REQUIRED_HEADERS if h not in norm_headers]
                if missing:
                    messages.error(request, f"Cabeçalhos obrigatórios ausentes: {', '.join(missing)}.")
                    return TemplateResponse(request, "admin/patrimonio/bem/importar_csv.html", {**context, "form": form})

                # Mapa: header normalizado -> header cru do arquivo
                norm_to_raw = { _normalize_header(h): h for h in raw_headers }

                # Mapa: header normalizado -> campo do modelo
                header_to_field = { h: FIELD_MAP[h] for h in norm_headers if h in FIELD_MAP }

                # Import linha a linha (parcial)
                criados = 0
                atualizados = 0
                erros = 0
                erros_msgs = []
                vistos_no_arquivo = set()
                linha_num = 1  # cabeçalho

                for row in reader:
                    linha_num += 1
                    try:
                        # >>> pular linha totalmente vazia <<<
                        if all((not _norm_str(row.get(h))) for h in raw_headers):
                            continue

                        # Extrai e normaliza valores conforme campo
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

                        # Duplicata dentro do próprio arquivo
                        if tomb in vistos_no_arquivo:
                            raise ValueError(f"Tombamento duplicado no arquivo: {tomb}")
                        vistos_no_arquivo.add(tomb)

                        # update_or_create por tombamento
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

                    except Exception as e:
                        erros += 1
                        if len(erros_msgs) < 10:
                            erros_msgs.append(f"Linha {linha_num}: {str(e)}")

                # Mensagens finais
                if criados or atualizados:
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
