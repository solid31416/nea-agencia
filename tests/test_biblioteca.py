"""Módulo de la agencia — Nea manda fotos, videos y fichas de la Biblioteca
del CRM (`POST /api/bot/biblioteca` de vocero-agencia).

La herramienta `send_media` se le enseña al modelo solo si el contexto del
turno trae `biblioteca` con algo (el CRM la manda con su bandera BIBLIOTECA y
la herramienta encendida) y el perfil tiene `biblioteca` encendida. Las reglas
(ventana, no repetir el mismo día, tope diario) las aplica el CRM; su 409
vuelve al modelo para que siga.
"""
from __future__ import annotations

import json

import httpx

from app.llm import LlmReply, ToolCall
from app.main import create_app
from app.profile import BusinessProfile
from app.tools import CLAVE_DE_HERRAMIENTA, biblioteca_del_contexto
from tests.conftest import CRM_CONV_ID, CRM_URL, crm_context, despachar, make_ctx, mock_crm_basics

ARCHIVOS = [
    {"id": "bib_noche", "titulo": "Domo de noche", "cuando": "Si pregunta cómo se ve", "tipo": "foto", "yaEnviado": False},
    {"id": "bib_armado", "titulo": "Armado en un día", "cuando": "", "tipo": "video", "yaEnviado": True},
]


class _Perfil:
    def __init__(self, perfil: BusinessProfile) -> None:
        self._p = perfil

    async def get(self) -> BusinessProfile:
        return self._p


def _nombres(schemas):
    return {t["function"]["name"] for t in schemas or []}


def _con_biblioteca(respx_mock, archivos=ARCHIVOS):
    rutas = mock_crm_basics(respx_mock)
    contexto = crm_context()
    contexto["biblioteca"] = archivos
    rutas["context"].mock(return_value=httpx.Response(200, json=contexto))
    return rutas


async def _turno(ctx, texto="¿me mandas fotos de cómo se ve?"):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(ctx=ctx)), base_url="http://bot.test"
    ) as client:
        await despachar(client, ctx, esperar=True, text=texto)


def test_biblioteca_del_contexto():
    assert [a["id"] for a in biblioteca_del_contexto({"biblioteca": ARCHIVOS})] == ["bib_noche", "bib_armado"]
    assert biblioteca_del_contexto({}) == []
    assert biblioteca_del_contexto(None) == []
    assert biblioteca_del_contexto({"biblioteca": [{"id": "x"}, "y", {"titulo": "t"}]}) == []


def test_send_media_es_de_la_clave_biblioteca():
    assert CLAVE_DE_HERRAMIENTA["send_media"] == "biblioteca"


async def test_con_biblioteca_se_ensena_y_manda(respx_mock):
    ctx = make_ctx()
    ctx.profile = _Perfil(BusinessProfile(agent_name="Sofi"))
    rutas = _con_biblioteca(respx_mock)
    bib = respx_mock.post(f"{CRM_URL}/api/bot/biblioteca").mock(
        return_value=httpx.Response(200, json={"messageId": "msg_9", "titulo": "Domo de noche", "status": "sent"})
    )
    ctx.llm.replies = [
        LlmReply(content=None, tool_calls=[ToolCall(id="s", name="send_media", arguments={"archivo_id": "bib_noche"})]),
        LlmReply(content="Así se ve de noche 🌙"),
    ]
    await _turno(ctx)

    primera = ctx.llm.calls[0]
    herramienta = next(t for t in primera["tools"] if t["function"]["name"] == "send_media")
    assert herramienta["function"]["parameters"]["properties"]["archivo_id"]["enum"] == ["bib_noche", "bib_armado"]
    sistema = primera["messages"][0]["content"]
    assert "bib_noche — foto: Domo de noche. Cuándo: Si pregunta cómo se ve" in sistema
    assert "bib_armado — video: Armado en un día (ya enviado en esta conversación)" in sistema
    assert bib.called
    assert json.loads(bib.calls[0].request.content) == {"conversationId": CRM_CONV_ID, "archivoId": "bib_noche"}
    resultado = json.loads(ctx.llm.calls[1]["messages"][-1]["content"])
    assert resultado["ok"] is True and resultado["enviado"] == "Domo de noche"
    assert rutas["messages"].called
    await ctx.crm.aclose()


async def test_el_rechazo_del_crm_vuelve_al_modelo(respx_mock):
    ctx = make_ctx()
    ctx.profile = _Perfil(BusinessProfile(agent_name="Sofi"))
    rutas = _con_biblioteca(respx_mock)
    respx_mock.post(f"{CRM_URL}/api/bot/biblioteca").mock(
        return_value=httpx.Response(
            409, json={"error": {"code": "ya_enviado", "message": "Ese archivo ya se le mandó hoy."}}
        )
    )
    ctx.llm.replies = [
        LlmReply(content=None, tool_calls=[ToolCall(id="s", name="send_media", arguments={"archivo_id": "bib_armado"})]),
        LlmReply(content="Arriba te dejé el video del armado."),
    ]
    await _turno(ctx)

    resultado = json.loads(ctx.llm.calls[1]["messages"][-1]["content"])
    assert resultado == {"ok": False, "error": "ya_enviado", "detalle": "Ese archivo ya se le mandó hoy."}
    assert rutas["messages"].called
    await ctx.crm.aclose()


async def test_un_id_inventado_no_llega_al_crm(respx_mock):
    ctx = make_ctx()
    ctx.profile = _Perfil(BusinessProfile(agent_name="Sofi"))
    _con_biblioteca(respx_mock)
    bib = respx_mock.post(f"{CRM_URL}/api/bot/biblioteca").mock(return_value=httpx.Response(200, json={}))
    ctx.llm.replies = [
        LlmReply(content=None, tool_calls=[ToolCall(id="s", name="send_media", arguments={"archivo_id": "bib_x"})]),
        LlmReply(content="Te paso con alguien del equipo para esas fotos."),
    ]
    await _turno(ctx)
    resultado = json.loads(ctx.llm.calls[1]["messages"][-1]["content"])
    assert resultado["ok"] is False and resultado["error"] == "archivo_desconocido"
    assert not bib.called
    await ctx.crm.aclose()


async def test_apagada_en_el_perfil_no_se_ensena(respx_mock):
    ctx = make_ctx()
    ctx.profile = _Perfil(BusinessProfile(agent_name="Sofi", tools=frozenset({"ficha", "salida"})))
    _con_biblioteca(respx_mock)
    await _turno(ctx)
    llamada = ctx.llm.calls[0]
    assert "send_media" not in _nombres(llamada["tools"])
    assert "Biblioteca" not in llamada["messages"][0]["content"]
    await ctx.crm.aclose()


async def test_sin_biblioteca_en_el_contexto_no_existe(respx_mock):
    """Un CRM sin la bandera (o el raíz) no manda `biblioteca`."""
    ctx = make_ctx()
    ctx.profile = _Perfil(BusinessProfile(agent_name="Sofi"))
    mock_crm_basics(respx_mock)
    await _turno(ctx)
    assert "send_media" not in _nombres(ctx.llm.calls[0]["tools"])
    await ctx.crm.aclose()
