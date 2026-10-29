"""Módulo de la agencia — Nea se pone al día con lo que pasó mientras no habló.

El CRM del fork manda `ponerseAlDia` en el despacho (lo que escribió una
persona del equipo y lo que dijo el cliente con la IA en pausa). Nea lo mete
como UNA nota de sistema antes del mensaje del cliente, y no lo repite aunque
el CRM lo vuelva a mandar.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

from app.dispatch import _procesar
from app.ponerse_al_dia import MAX_MENSAJES, MAX_TEXTO, mensajes_al_dia, nota_al_dia
from tests.conftest import crm_context, make_ctx, make_settings

IDENTIDAD = "5215550002222"
CONV = "cv_aldia1"


def cloud():
    ctx = make_ctx(
        make_settings(
            vocero_mode="cloud", crm_organization="a", crm_brain_secret="secreto-de-prueba"
        )
    )
    ctx.crm.post_typing = AsyncMock()
    ctx.crm.send_message = AsyncMock(return_value={"messageId": "msg_out"})
    return ctx


def despacho(dispatch_id: str, mensajes, al_dia=None) -> dict:
    payload = {
        "dispatchId": dispatch_id,
        "organization": {"id": "org_a", "slug": "a"},
        "conversation": {"id": CONV, "channel": "whatsapp"},
        "contact": {"id": "ct_1", "identity": IDENTIDAD, "displayName": "Ana"},
        "messages": [{"id": mid, "type": "text", "text": texto} for mid, texto in mensajes],
    }
    if al_dia is not None:
        payload["ponerseAlDia"] = al_dia
    return payload


def contexto(monkeypatch) -> AsyncMock:
    mock = AsyncMock(return_value=crm_context(conv_id=CONV))
    monkeypatch.setattr("app.turn._fetch_context", mock)
    return mock


AL_DIA = [
    {"id": "msg_eq1", "at": "2026-10-29T00:28:35Z", "de": "equipo", "texto": "Te lo dejo en $500"},
    {"id": "msg_cl1", "at": "2026-10-29T00:28:53Z", "de": "cliente", "texto": "ok gracias"},
]


def notas(ctx) -> list[str]:
    return [m.content for m in ctx.store.messages if m.role == "system"]


# --- Lectura del despacho -----------------------------------------------------


def test_sin_la_clave_no_hay_nada_que_contar():
    assert mensajes_al_dia({}) == []
    assert mensajes_al_dia({"ponerseAlDia": "basura"}) == []


def test_descarta_lo_malformado_y_recorta():
    crudos = [
        {"id": "a", "de": "equipo", "texto": "  hola  "},
        {"id": "", "de": "equipo", "texto": "sin id"},
        {"id": "b", "de": "alguien", "texto": "quién?"},
        {"id": "c", "de": "cliente", "texto": "   "},
        "no soy un dict",
        {"id": "d", "de": "cliente", "texto": "x" * (MAX_TEXTO + 10)},
    ]
    validos = mensajes_al_dia({"ponerseAlDia": crudos})
    assert [m["id"] for m in validos] == ["a", "d"]
    assert validos[0]["texto"] == "hola"
    assert len(validos[1]["texto"]) == MAX_TEXTO


def test_se_queda_con_los_mas_recientes():
    crudos = [{"id": f"m{i}", "de": "cliente", "texto": str(i)} for i in range(MAX_MENSAJES + 5)]
    validos = mensajes_al_dia({"ponerseAlDia": crudos})
    assert len(validos) == MAX_MENSAJES
    assert validos[-1]["id"] == f"m{MAX_MENSAJES + 4}"


def test_la_nota_dice_quien_lo_dijo_y_que_nea_no_lo_escribio():
    nota = nota_al_dia(mensajes_al_dia({"ponerseAlDia": AL_DIA}))
    assert "Una persona del equipo: «Te lo dejo en $500»" in nota
    assert "El cliente: «ok gracias»" in nota
    assert "NO escribiste" in nota


# --- De punta a punta, por el despacho ----------------------------------------


async def test_la_nota_entra_antes_del_mensaje_del_cliente(monkeypatch):
    ctx = cloud()
    contexto(monkeypatch)

    await _procesar(
        ctx, despacho("dsp_1", [("msg_2", "¿entonces son $500?")], AL_DIA), durable=True
    )

    assert len(notas(ctx)) == 1
    llamada = ctx.llm.calls[0]["messages"]
    roles = [m["role"] for m in llamada]
    i_nota = next(i for i, m in enumerate(llamada) if "Te lo dejo en $500" in (m["content"] or ""))
    i_cliente = max(i for i, r in enumerate(roles) if r == "user")
    assert llamada[i_nota]["role"] == "system"
    assert i_nota < i_cliente
    assert llamada[i_cliente]["content"] == "¿entonces son $500?"
    await ctx.crm.aclose()


async def test_lo_ya_contado_no_se_repite(monkeypatch):
    ctx = cloud()
    contexto(monkeypatch)

    await _procesar(ctx, despacho("dsp_1", [("msg_2", "hola")], AL_DIA), durable=True)
    # El CRM vuelve a mandar lo mismo (Nea no ha vuelto a hablar desde ahí) y
    # algo nuevo del equipo.
    mas = AL_DIA + [{"id": "msg_eq2", "de": "equipo", "texto": "la llamada es el jueves"}]
    await _procesar(ctx, despacho("dsp_2", [("msg_3", "va")], mas), durable=True)

    contadas = notas(ctx)
    assert len(contadas) == 2
    assert "Te lo dejo en $500" in contadas[0]
    assert "Te lo dejo en $500" not in contadas[1]
    assert "la llamada es el jueves" in contadas[1]
    await ctx.crm.aclose()


async def test_sin_ponerse_al_dia_el_turno_queda_igual(monkeypatch):
    ctx = cloud()
    contexto(monkeypatch)

    await _procesar(ctx, despacho("dsp_1", [("msg_2", "hola")]), durable=True)

    assert notas(ctx) == []
    assert [m.content for m in ctx.store.messages if m.role == "user"] == ["hola"]
    await ctx.crm.aclose()
