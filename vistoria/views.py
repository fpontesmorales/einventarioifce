from collections import defaultdict
from urllib.parse import unquote
import csv

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import Group
from django.db import transaction
from django.http import Http404, HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render

from patrimonio.models import Bem, Sala
from .models import Inventario, VistoriaBem, VistoriaExtra, split_sala_bloco
from .utils import watermark_and_compress


# ======================== ACESSO / PERMISSÃO ========================
def _inventario_ativo_or_none():
    return Inventario.objects.filter(ativo=True).first()

def _require_inventario_ativo():
    inv = _inventario_ativo_or_none()
    if not inv:
        raise Http404("Não há inventário ativo no momento.")
    return inv

def _is_vistoriador(user):
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    return Group.objects.filter(user=user, name__iexact="Vistoriadores").exists()

def vistoriador_required(view_func):
    @login_required(login_url="/admin/login/")
    def _wrapped(request, *args, **kwargs):
        if not _is_vistoriador(request.user):
            raise Http404("Você não tem permissão para acessar a Vistoria.")
        return view_func(request, *args, **kwargs)
    return _wrapped


# ======================== HELPERS DE AGREGAÇÃO ========================
def _bens_por_sala_dict(qs_bens):
    d = defaultdict(list)
    for b in qs_bens.only("id", "sala"):
        nome, bloco = split_sala_bloco(b.sala or "")
        key = (nome or "SEM SALA", bloco or None)
        d[key].append(b.id)
    return d

def _sala_lookup_by_key():
    m = {}
    for s in Sala.objects.all().only("id", "nome", "bloco"):
        m[(s.nome, s.bloco or None)] = s
    return m

def _stats_por_bloco(inv: Inventario):
    elegiveis = inv.bens_elegiveis_qs().only("id", "sala")
    ids_elegiveis = [b.id for b in elegiveis]
    by_sala = _bens_por_sala_dict(elegiveis)

    blocos = defaultdict(lambda: {
        "total": 0, "vistoriados": 0, "nao_encontrados": 0, "movidos": 0, "sem_registro": 0
    })

    for (nome, bloco), bem_ids in by_sala.items():
        bl = bloco or "SEM BLOCO"
        blocos[bl]["total"] += len(bem_ids)

    v_qs = (VistoriaBem.objects.select_related("bem")
            .filter(inventario=inv, bem_id__in=ids_elegiveis))
    for v in v_qs:
        suap_nome, suap_bloco = split_sala_bloco(v.bem.sala or "")
        bl = (suap_bloco or "SEM BLOCO")
        blocos[bl]["vistoriados"] += 1
        if v.status == VistoriaBem.Status.NAO_ENCONTRADO:
            blocos[bl]["nao_encontrados"] += 1
        if v.encontrado_em_outra_sala():
            blocos[bl]["movidos"] += 1

    for x in VistoriaExtra.objects.filter(inventario=inv):
        bl = (x.sala_obs_bloco or "SEM BLOCO")
        blocos[bl]["sem_registro"] += 1

    cards = []
    for bl, data in blocos.items():
        data["pendentes"] = max(0, data["total"] - data["vistoriados"])
        cards.append({"bloco": bl, **data})
    cards.sort(key=lambda c: c["total"], reverse=True)
    return cards

def _stats_salas_do_bloco(inv: Inventario, bloco_alvo: str | None):
    elegiveis = inv.bens_elegiveis_qs().only("id", "sala")
    by_sala = _bens_por_sala_dict(elegiveis)
    sala_map = _sala_lookup_by_key()

    target = {}
    for (nome, bloco), bem_ids in by_sala.items():
        bl = bloco or "SEM BLOCO"
        if bl == (bloco_alvo or "SEM BLOCO"):
            s = sala_map.get((nome, bloco))
            if s:
                target[s.id] = {
                    "sala": s,
                    "bem_ids": set(bem_ids),
                    "total": len(bem_ids),
                    "vistoriados": 0,
                    "movidos_fora": 0,
                    "movidos_recebidos": 0,
                    "sem_registro": 0,
                }

    ids_elegiveis = [b.id for b in elegiveis]
    v_qs = (VistoriaBem.objects.select_related("bem")
            .filter(inventario=inv, bem_id__in=ids_elegiveis))

    for v in v_qs:
        suap_nome, suap_bloco = split_sala_bloco(v.bem.sala or "")
        suap_bloco = suap_bloco or "SEM BLOCO"
        s = sala_map.get((suap_nome, None if suap_bloco == "SEM BLOCO" else suap_bloco))
        if s and s.id in target:
            target[s.id]["vistoriados"] += 1

        if v.encontrado_em_outra_sala():
            obs_key = ((v.sala_obs_nome or "").strip() or None, (v.sala_obs_bloco or "").strip() or None)
            if s and s.id in target:
                target[s.id]["movidos_fora"] += 1
            s_obs = sala_map.get(obs_key)
            if s_obs and s_obs.id in target:
                target[s_obs.id]["movidos_recebidos"] += 1

    for x in VistoriaExtra.objects.filter(inventario=inv):
        s_obs = sala_map.get(((x.sala_obs_nome or "").strip() or None, (x.sala_obs_bloco or "").strip() or None))
        if s_obs and s_obs.id in target:
            target[s_obs.id]["sem_registro"] += 1

    items = []
    for data in target.values():
        data["pendentes"] = max(0, data["total"] - data["vistoriados"])
        items.append(data)
    items.sort(key=lambda d: d["total"], reverse=True)
    return items

def _listas_da_sala(inv: Inventario, sala_obj: Sala):
    elegiveis = inv.bens_elegiveis_qs()
    bens_sala = []
    for b in elegiveis.only("id", "sala", "descricao", "tombamento"):
        nome, bloco = split_sala_bloco(b.sala or "")
        if (nome or None, bloco or None) == (sala_obj.nome, sala_obj.bloco or None):
            bens_sala.append(b)
    ids = [b.id for b in bens_sala]

    v_map = {v.bem_id: v for v in VistoriaBem.objects.filter(inventario=inv, bem_id__in=ids).select_related("bem")}

    nao_vistoriados, vistoriados_ok, vistoriados_div, nao_encontrados, movidos = [], [], [], [], []
    for b in bens_sala:
        v = v_map.get(b.id)
        if not v:
            nao_vistoriados.append(b)
            continue
        if v.status == VistoriaBem.Status.NAO_ENCONTRADO:
            nao_encontrados.append((b, v))
        else:
            if v.encontrado_em_outra_sala():
                movidos.append((b, v))
            elif v.divergente:
                vistoriados_div.append((b, v))
            else:
                vistoriados_ok.append((b, v))

    extras = list(VistoriaExtra.objects.filter(
        inventario=inv,
        sala_obs_nome=sala_obj.nome,
        sala_obs_bloco=(sala_obj.bloco or None),
    ))

    return {
        "nao_vistoriados": nao_vistoriados,
        "vistoriados_ok": vistoriados_ok,
        "vistoriados_div": vistoriados_div,
        "nao_encontrados": nao_encontrados,
        "movidos": movidos,
        "extras": extras,
    }


# ======================== VIEWS (LEITURA) ========================
@vistoriador_required
def blocos_view(request):
    inv = _require_inventario_ativo()

    q = (request.GET.get("q") or "").strip()
    salas_encontradas = []
    if q:
        salas_encontradas = list(Sala.objects.filter(nome__icontains=q).order_by("nome")[:20])
        if len(salas_encontradas) == 1 and salas_encontradas[0].nome.strip().lower() == q.lower():
            return redirect("vistoria_public:sala_workspace", sala_id=salas_encontradas[0].id)

    cards = _stats_por_bloco(inv)
    ctx = {"inventario": inv, "cards": cards, "q": q, "salas_encontradas": salas_encontradas}
    return render(request, "vistoria/blocos.html", ctx)

@vistoriador_required
def salas_por_bloco_view(request, bloco):
    inv = _require_inventario_ativo()
    bloco = (unquote(bloco) or "SEM BLOCO")
    items = _stats_salas_do_bloco(inv, bloco if bloco != "SEM BLOCO" else None)
    ctx = {"inventario": inv, "bloco": bloco, "items": items}
    return render(request, "vistoria/salas_por_bloco.html", ctx)

@vistoriador_required
def sala_workspace_view(request, sala_id: int):
    inv = _require_inventario_ativo()
    sala = get_object_or_404(Sala, id=sala_id)
    listas = _listas_da_sala(inv, sala)

    total_elegiveis = (
        len(listas["nao_vistoriados"])
        + len(listas["vistoriados_ok"])
        + len(listas["vistoriados_div"])
        + len(listas["nao_encontrados"])
    )
    vistoriados_count = (
        len(listas["vistoriados_ok"])
        + len(listas["vistoriados_div"])
        + len(listas["nao_encontrados"])
    )
    pct_vistoriado = int((vistoriados_count * 100) / total_elegiveis) if total_elegiveis else 0

    ctx = {
        "inventario": inv,
        "sala": sala,
        "total_elegiveis": total_elegiveis,
        "vistoriados_count": vistoriados_count,
        "pct_vistoriado": pct_vistoriado,
        **listas,
    }
    return render(request, "vistoria/sala_workspace.html", ctx)


# ======================== AÇÕES ========================
@vistoriador_required
def vistoriar_por_tombo(request, sala_id: int):
    if request.method != "POST":
        return HttpResponseBadRequest("Método inválido.")
    inv = _require_inventario_ativo()
    sala = get_object_or_404(Sala, id=sala_id)

    tomb = (request.POST.get("tombamento") or "").strip()
    if not tomb:
        messages.warning(request, "Informe um tombamento.")
        return redirect("vistoria_public:sala_workspace", sala_id=sala.id)

    try:
        bem = Bem.objects.get(tombamento=tomb)
    except Bem.DoesNotExist:
        messages.error(request, f"Tombo {tomb} não existe no SUAP. Use '+ Item sem registro'.")
        return redirect("vistoria_public:sala_workspace", sala_id=sala.id)

    if not inv.bem_e_elegivel(bem):
        messages.error(request, "Este bem não faz parte do escopo da campanha (baixado ou livro fora do escopo).")
        return redirect("vistoria_public:sala_workspace", sala_id=sala.id)

    return redirect("vistoria_public:vistoria_bem_form", sala_id=sala.id, tombamento=bem.tombamento)


@vistoriador_required
def vistoria_bem_form(request, sala_id: int, tombamento: str):
    inv = _require_inventario_ativo()
    sala = get_object_or_404(Sala, id=sala_id)
    bem = get_object_or_404(Bem, tombamento=tombamento)

    if not inv.bem_e_elegivel(bem):
        messages.error(request, "Este bem não faz parte do escopo da campanha (baixado ou livro fora do escopo).")
        return redirect("vistoria_public:sala_workspace", sala_id=sala.id)

    salas = list(Sala.objects.all().order_by("nome"))
    v = VistoriaBem.objects.filter(inventario=inv, bem=bem).first()

    if request.method == "POST":
        acao = request.POST.get("acao")
        if acao == "excluir":
            if v:
                v.delete()
                messages.success(request, f"Vistoria do tombo {bem.tombamento} excluída.")
            return redirect("vistoria_public:sala_workspace", sala_id=sala.id)

        if acao == "salvar_nao_encontrado":
            with transaction.atomic():
                v = v or VistoriaBem(inventario=inv, bem=bem, criado_por=request.user)
                v.status = VistoriaBem.Status.NAO_ENCONTRADO
                v.atualizado_por = request.user
                v.confere_descricao = True
                v.confere_numero_serie = True
                v.confere_local = True
                v.confere_estado = True
                v.confere_responsavel = True
                v.descricao_obs = v.numero_serie_obs = v.sala_obs_nome = v.sala_obs_bloco = v.estado_obs = v.responsavel_obs = None
                v.etiqueta_possui = True
                v.etiqueta_condicao = None
                v.avaria_texto = v.observacoes = None
                v.save()
            messages.success(request, f"Tombo {bem.tombamento} marcado como NÃO ENCONTRADO (reversível).")
            return redirect("vistoria_public:sala_workspace", sala_id=sala.id)

        # salvar_encontrado
        foto = request.FILES.get("foto")
        if not (v and v.foto_marcadagua) and not foto:
            messages.error(request, "Foto obrigatória para ENCONTRADO.")
            return redirect("vistoria_public:vistoria_bem_form", sala_id=sala.id, tombamento=bem.tombamento)

        conf_desc = bool(request.POST.get("confere_descricao"))
        conf_serie = bool(request.POST.get("confere_numero_serie"))
        conf_local = bool(request.POST.get("confere_local"))
        conf_estado = bool(request.POST.get("confere_estado"))
        conf_resp = bool(request.POST.get("confere_responsavel"))

        desc_obs = (request.POST.get("descricao_obs") or "").strip() or None
        serie_obs = (request.POST.get("numero_serie_obs") or "").strip() or None
        sala_obs_id = request.POST.get("sala_obs_id")
        estado_obs = (request.POST.get("estado_obs") or "").strip() or None
        resp_obs = (request.POST.get("responsavel_obs") or "").strip() or None

        sala_obs_nome = sala_obs_bloco = None
        if not conf_local:
            if sala_obs_id:
                try:
                    s_obs = Sala.objects.get(id=int(sala_obs_id))
                    sala_obs_nome, sala_obs_bloco = s_obs.nome, s_obs.bloco
                except (Sala.DoesNotExist, ValueError):
                    sala_obs_nome, sala_obs_bloco = sala.nome, sala.bloco
            else:
                sala_obs_nome, sala_obs_bloco = sala.nome, sala.bloco

        etiqueta_possui = bool(request.POST.get("etiqueta_possui"))
        etiqueta_cond = (request.POST.get("etiqueta_condicao") or "").strip() or None
        avaria = (request.POST.get("avaria_texto") or "").strip() or None
        obs = (request.POST.get("observacoes") or "").strip() or None

        with transaction.atomic():
            v = v or VistoriaBem(inventario=inv, bem=bem, criado_por=request.user)
            v.status = VistoriaBem.Status.ENCONTRADO
            v.atualizado_por = request.user

            v.confere_descricao = conf_desc
            v.confere_numero_serie = conf_serie
            v.confere_local = conf_local
            v.confere_estado = conf_estado
            v.confere_responsavel = conf_resp

            v.descricao_obs = desc_obs if not conf_desc else None
            v.numero_serie_obs = serie_obs if not conf_serie else None
            v.sala_obs_nome = sala_obs_nome if not conf_local else None
            v.sala_obs_bloco = sala_obs_bloco if not conf_local else None
            v.estado_obs = estado_obs if not conf_estado else None
            v.responsavel_obs = resp_obs if not conf_resp else None

            v.etiqueta_possui = etiqueta_possui
            v.etiqueta_condicao = etiqueta_cond if etiqueta_possui else None
            v.avaria_texto = avaria
            v.observacoes = obs

            if foto:
                wm_text = f"{bem.tombamento} — {(bem.descricao or '')[:80]}"
                watermarked = watermark_and_compress(foto, wm_text)
                v.foto_marcadagua.save(f"bem_{bem.tombamento}.jpg", watermarked, save=False)

            v.save()

        messages.success(request, f"Vistoria do tombo {bem.tombamento} salva.")
        return redirect("vistoria_public:sala_workspace", sala_id=sala.id)

    # GET → dados SUAP para exibir
    suap_nome, suap_bloco = split_sala_bloco(bem.sala or "")
    suap_serie = getattr(bem, "numero_serie", None) or getattr(bem, "serie", None) or ""
    suap_estado = getattr(bem, "estado", None) or getattr(bem, "estado_conservacao", None) or ""
    suap_resp = (
        getattr(bem, "carga_atual", None)
        or getattr(bem, "responsavel", None)
        or getattr(bem, "carga_responsavel", None)
        or ""
    )

    ctx = {
        "inventario": inv,
        "sala": sala,
        "bem": bem,
        "suap_nome": suap_nome,
        "suap_bloco": suap_bloco,
        "suap_serie": suap_serie,
        "suap_estado": suap_estado,
        "suap_resp": suap_resp,
        "salas": salas,
        "vistoria": v,
        "sala_default_id": sala.id,
        "etiqueta_choices": VistoriaBem.EtiquetaCondicao.choices,
    }
    return render(request, "vistoria/vistoria_bem_form.html", ctx)


@vistoriador_required
def marcar_nao_encontrado(request, sala_id: int, tombamento: str):
    if request.method != "POST":
        return HttpResponseBadRequest("Método inválido.")
    inv = _require_inventario_ativo()
    sala = get_object_or_404(Sala, id=sala_id)
    bem = get_object_or_404(Bem, tombamento=tombamento)

    if not inv.bem_e_elegivel(bem):
        messages.error(request, "Este bem não faz parte do escopo da campanha (baixado ou livro fora do escopo).")
        return redirect("vistoria_public:sala_workspace", sala_id=sala.id)

    with transaction.atomic():
        v = VistoriaBem.objects.filter(inventario=inv, bem=bem).first()
        v = v or VistoriaBem(inventario=inv, bem=bem, criado_por=request.user)
        v.status = VistoriaBem.Status.NAO_ENCONTRADO
        v.atualizado_por = request.user
        v.confere_descricao = v.confere_numero_serie = v.confere_local = v.confere_estado = v.confere_responsavel = True
        v.descricao_obs = v.numero_serie_obs = v.sala_obs_nome = v.sala_obs_bloco = v.estado_obs = v.responsavel_obs = None
        v.avaria_texto = v.observacoes = None
        v.save()

    messages.success(request, f"Tombo {bem.tombamento} marcado como NÃO ENCONTRADO (reversível).")
    return redirect("vistoria_public:sala_workspace", sala_id=sala.id)


@vistoriador_required
def vistoria_extra_form(request, sala_id: int):
    inv = _require_inventario_ativo()
    sala = get_object_or_404(Sala, id=sala_id)

    if request.method == "POST":
        foto = request.FILES.get("foto")
        if not foto:
            messages.error(request, "Foto obrigatória para item sem registro.")
            return redirect("vistoria_public:extra_novo", sala_id=sala.id)

        desc = (request.POST.get("descricao_obs") or "").strip()
        if not desc:
            messages.error(request, "Informe a descrição observada.")
            return redirect("vistoria_public:extra_novo", sala_id=sala.id)

        serie = (request.POST.get("numero_serie_obs") or "").strip() or None
        estado = (request.POST.get("estado_obs") or "").strip() or None
        resp = (request.POST.get("responsavel_obs") or "").strip() or None
        etiqueta_possui = bool(request.POST.get("etiqueta_possui"))
        etiqueta_cond = (request.POST.get("etiqueta_condicao") or "").strip() or None
        obs = (request.POST.get("observacoes") or "").strip() or None

        with transaction.atomic():
            wm_text = f"SEM TOMBO — {desc[:60]}"
            watermarked = watermark_and_compress(foto, wm_text)
            x = VistoriaExtra(
                inventario=inv,
                descricao_obs=desc,
                sala_obs_nome=sala.nome,
                sala_obs_bloco=sala.bloco,
                numero_serie_obs=serie,
                estado_obs=estado,
                responsavel_obs=resp,
                etiqueta_possui=etiqueta_possui,
                etiqueta_condicao=(etiqueta_cond if etiqueta_possui else None),
                observacoes=obs,
                criado_por=request.user,
            )
            x.foto_marcadagua.save("extra.jpg", watermarked, save=False)
            x.save()

        messages.success(request, "Item sem registro salvo.")
        return redirect("vistoria_public:sala_workspace", sala_id=sala.id)

    ctx = {
        "inventario": inv,
        "sala": sala,
        "etiqueta_choices": VistoriaBem.EtiquetaCondicao.choices,
    }
    return render(request, "vistoria/vistoria_extra_form.html", ctx)


# ======================== RELATÓRIOS (CSV) ========================
@vistoriador_required
def relatorio_resumo_csv(request):
    inv = _require_inventario_ativo()
    bloco_f = (request.GET.get("bloco") or "").strip() or None

    elegiveis = list(inv.bens_elegiveis_qs().only("id", "sala", "descricao"))
    sala_map = _sala_lookup_by_key()

    stats = {}  # key: (nome, bloco)
    for b in elegiveis:
        nome, bloco = split_sala_bloco(b.sala or "")
        key = ((nome or "SEM SALA"), (bloco or "SEM BLOCO"))
        st = stats.setdefault(key, {"total": 0, "vistoriados": 0, "ok": 0, "div": 0, "naoencontrado": 0, "mov_fora": 0, "mov_receb": 0, "extra": 0})
        st["total"] += 1

    ids_elegiveis = [b.id for b in elegiveis]
    v_qs = (VistoriaBem.objects.select_related("bem")
            .filter(inventario=inv, bem_id__in=ids_elegiveis))

    for v in v_qs:
        suap_nome, suap_bloco = split_sala_bloco(v.bem.sala or "")
        src_key = ((suap_nome or "SEM SALA"), (suap_bloco or "SEM BLOCO"))
        st = stats.setdefault(src_key, {"total": 0, "vistoriados": 0, "ok": 0, "div": 0, "naoencontrado": 0, "mov_fora": 0, "mov_receb": 0, "extra": 0})
        st["vistoriados"] += 1

        if v.status == VistoriaBem.Status.NAO_ENCONTRADO:
            st["naoencontrado"] += 1
        else:
            if v.encontrado_em_outra_sala():
                st["mov_fora"] += 1
                obs_key = ((v.sala_obs_nome or "SEM SALA"), (v.sala_obs_bloco or "SEM BLOCO"))
                stats.setdefault(obs_key, {"total": 0, "vistoriados": 0, "ok": 0, "div": 0, "naoencontrado": 0, "mov_fora": 0, "mov_receb": 0, "extra": 0})
                stats[obs_key]["mov_receb"] += 1
            else:
                if v.divergente:
                    st["div"] += 1
                else:
                    st["ok"] += 1

    for x in VistoriaExtra.objects.filter(inventario=inv):
        obs_key = ((x.sala_obs_nome or "SEM SALA"), (x.sala_obs_bloco or "SEM BLOCO"))
        stats.setdefault(obs_key, {"total": 0, "vistoriados": 0, "ok": 0, "div": 0, "naoencontrado": 0, "mov_fora": 0, "mov_receb": 0, "extra": 0})
        stats[obs_key]["extra"] += 1

    # Filtro por bloco, se informado
    items = []
    for (nome, bloco), st in stats.items():
        if bloco_f and bloco != bloco_f:
            continue
        total = st["total"]
        vist = st["vistoriados"]
        pend = max(0, total - vist)
        pct = int((vist * 100) / total) if total else 0
        items.append([bloco, nome, total, vist, st["ok"], st["div"], st["naoencontrado"], st["mov_fora"], st["mov_receb"], st["extra"], pend, pct])

    # Ordena por total desc
    items.sort(key=lambda r: r[2], reverse=True)

    # CSV (delimitador ; e BOM para Excel pt-BR)
    resp = HttpResponse(content_type="text/csv; charset=utf-8")
    resp["Content-Disposition"] = 'attachment; filename="resumo_salas.csv"'
    resp.write("\ufeff")
    w = csv.writer(resp, delimiter=";")
    w.writerow(["Bloco", "Sala", "Total elegíveis", "Vistoriados", "OK", "Divergentes", "Não encontrados", "Movidos (saíram)", "Movidos (recebidos)", "Sem registro", "Pendentes", "% Vistoriado"])
    for row in items:
        w.writerow(row)
    return resp


@vistoriador_required
def relatorio_detalhes_csv(request):
    inv = _require_inventario_ativo()
    sala_id = request.GET.get("sala_id")
    bloco_f = (request.GET.get("bloco") or "").strip() or None

    # CSV
    resp = HttpResponse(content_type="text/csv; charset=utf-8")
    resp["Content-Disposition"] = 'attachment; filename="detalhes_vistorias.csv"'
    resp.write("\ufeff")
    w = csv.writer(resp, delimiter=";")

    w.writerow([
        "Tipo", "Tombamento", "Descrição SUAP", "Sala SUAP", "Bloco SUAP",
        "Status", "Encontrado em outra sala?", "Sala observada", "Bloco observado",
        "Divergente?", "Divergências", "Etiqueta possui?", "Condição etiqueta",
        "Avaria", "Observações", "Responsável SUAP", "Responsável observado"
    ])

    # Vistorias de bens
    v_qs = VistoriaBem.objects.select_related("bem").filter(inventario=inv)
    if sala_id:
        sala = get_object_or_404(Sala, id=int(sala_id))
        # filtra pelo SUAP (sala original)
        v_qs = [v for v in v_qs if split_sala_bloco(v.bem.sala or "") == (sala.nome, sala.bloco)]
    elif bloco_f:
        v_qs = [v for v in v_qs if (split_sala_bloco(v.bem.sala or "")[1] or "SEM BLOCO") == bloco_f]

    for v in v_qs:
        b = v.bem
        suap_nome, suap_bloco = split_sala_bloco(b.sala or "")
        diverg_fields = []
        if v.status == VistoriaBem.Status.ENCONTRADO:
            if not v.confere_descricao: diverg_fields.append("Descrição")
            if not v.confere_numero_serie: diverg_fields.append("Nº de série")
            if not v.confere_local: diverg_fields.append("Local")
            if not v.confere_estado: diverg_fields.append("Estado")
            if not v.confere_responsavel: diverg_fields.append("Responsável")

        w.writerow([
            "BEM",
            b.tombamento,
            (b.descricao or "").strip(),
            suap_nome or "",
            suap_bloco or "",
            ("ENCONTRADO" if v.status == VistoriaBem.Status.ENCONTRADO else "NÃO ENCONTRADO"),
            ("SIM" if v.encontrado_em_outra_sala() else "NÃO"),
            (v.sala_obs_nome or ""),
            (v.sala_obs_bloco or ""),
            ("SIM" if getattr(v, "divergente", False) else "NÃO"),
            ", ".join(diverg_fields),
            ("SIM" if v.etiqueta_possui else "NÃO"),
            (v.etiqueta_condicao or ""),
            (v.avaria_texto or ""),
            (v.observacoes or ""),
            (getattr(b, "carga_atual", None) or getattr(b, "responsavel", None) or getattr(b, "carga_responsavel", None) or ""),
            (v.responsavel_obs or ""),
        ])

    # Extras
    x_qs = VistoriaExtra.objects.filter(inventario=inv)
    if sala_id:
        sala = get_object_or_404(Sala, id=int(sala_id))
        x_qs = x_qs.filter(sala_obs_nome=sala.nome, sala_obs_bloco=sala.bloco)
    elif bloco_f:
        x_qs = [x for x in x_qs if (x.sala_obs_bloco or "SEM BLOCO") == bloco_f]

    for x in x_qs:
        w.writerow([
            "EXTRA",
            "SEM TOMBO",
            (x.descricao_obs or "").strip(),
            (x.sala_obs_nome or ""),
            (x.sala_obs_bloco or ""),
            "EXTRA",
            "",
            "",
            "",
            "",
            "",
            ("SIM" if x.etiqueta_possui else "NÃO"),
            (x.etiqueta_condicao or ""),
            "",
            (x.observacoes or ""),
            "",
            (x.responsavel_obs or ""),
        ])

    return resp
