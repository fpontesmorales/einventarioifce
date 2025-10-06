from django import forms
from .models import RelatorioConfig


class RelatorioConfigForm(forms.ModelForm):
    # Assinantes como texto (um por linha: "Nome - Cargo") para edição simples
    assinantes_lista = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 4, "placeholder": "Nome 1 - Cargo 1\nNome 2 - Cargo 2"}),
        help_text="Um por linha no formato: Nome - Cargo. A ordem aqui define a ordem no relatório.",
        label="Assinaturas",
    )

    class Meta:
        model = RelatorioConfig
        fields = [
            "logo",
            "texto_apresentacao",
            "texto_metodologia",
            "texto_conclusao",
            "incluir_mapa_nc",
            "incluir_sem_registro",
            "ocultar_contas_zeradas",
            "ordenar_anexos",
        ]
        widgets = {
            "texto_apresentacao": forms.Textarea(attrs={"rows": 5}),
            "texto_metodologia": forms.Textarea(attrs={"rows": 5}),
            "texto_conclusao": forms.Textarea(attrs={"rows": 5}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Preenche o textarea a partir do JSON
        linhas = []
        for a in (self.instance.assinantes or []):
            nome = a.get("nome", "").strip()
            cargo = a.get("cargo", "").strip()
            if nome or cargo:
                linhas.append(f"{nome} - {cargo}")
        self.fields["assinantes_lista"].initial = "\n".join(linhas)

    def clean(self):
        cleaned = super().clean()
        # Converte o textarea de volta para JSON ordenado
        assinantes_txt = cleaned.get("assinantes_lista") or ""
        out = []
        for i, linha in enumerate(assinantes_txt.splitlines(), start=1):
            if "-" in linha:
                nome, cargo = linha.split("-", 1)
                out.append({"ordem": i, "nome": nome.strip(), "cargo": cargo.strip()})
            elif linha.strip():
                out.append({"ordem": i, "nome": linha.strip(), "cargo": ""})
        self.instance.assinantes = out
        return cleaned
