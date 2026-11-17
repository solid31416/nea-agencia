"""Módulo de la agencia — Nea como vendedora (Ajustes → Ventas → Vendedor de
vocero-agencia, `POST /api/bot/cotizacion`).

La herramienta `cotizar` existe solo si el contexto trae `vendedor`. El modelo
manda productos del catálogo y cantidades, nunca precios: el CRM pone los de
la lista y revisa los límites del dueño. Si algo rebasa esos límites
(`paraPersona: true`), la conversación pasa a una persona.
"""
from __future__ import annotations

import json

import httpx

from app.llm import LlmReply, ToolCall
from app.main import create_app
from app.profile import BusinessProfile
from app.prompt import reglas_de_vendedor
from app.tools import vendedor_del_contexto
from tests.conftest import CRM_CONV_ID, CRM_URL, crm_context, despachar, make_ctx, mock_crm_basics

VENDEDOR = {
    "preciosEnChat": True,
    "modoEnvio": "aprobar",
    "moneda": "MXN",
    "iva": "más IVA (16%)",
    "anticipoPct": 50,
    "productos": [
        {"id": "mini", "nombre": "Domo Modelo Mini", "precio": "$125,000.00", "unidad": "pieza", "descripcion": "", "extra": False},
        {"id": "flete", "nombre": "Flete", "precio": "$12,000.00", "unidad": "servicio", "descripcion": "", "extra": True},
    ],
    "puedeCotizarExtras": False,
    "descuentoMaxPct": 0,
    "topeMonto": "$1,000,000.00",
    "piezasMax": 5,
    "pendiente": None,
}


class _Perfil:
    def __init__(self, perfil: BusinessProfile) -> None:
        self._p = perfil

    async def get(self) -> BusinessProfile:
        return self._p


def _con_vendedor(respx_mock, vendedor=VENDEDOR):
    rutas = mock_crm_basics(respx_mock)
    contexto = crm_context()
    if vendedor is not None:
        contexto["vendedor"] = vendedor
    rutas["context"].mock(return_value=httpx.Response(200, json=contexto))
    return rutas


async def _turno(ctx, texto="Sí, cotízame 2 domos mini"):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(ctx=ctx)), base_url="http://bot.test"
    ) as client:
        await despachar(client, ctx, esperar=True, text=texto)


def _ctx(tools=None):
    ctx = make_ctx()
    ctx.profile = _Perfil(BusinessProfile(agent_name="Sofi", tools=tools))
    return ctx


def test_vendedor_del_contexto():
    v = vendedor_del_contexto({"vendedor": VENDEDOR})
    assert v is not None
    # Sin permiso de extras, el flete ni aparece.
    assert [p["id"] for p in v["productos"]] == ["mini"]
    assert v["piezasMax"] == 5 and v["modoEnvio"] == "aprobar"
    assert vendedor_del_contexto({}) is None
    assert vendedor_del_contexto({"vendedor": {"productos": [{"id": "flete", "nombre": "Flete", "extra": True}]}}) is None


def test_reglas_segun_lo_que_dejo_el_dueno():
    v = vendedor_del_contexto({"vendedor": VENDEDOR})
    texto = reglas_de_vendedor(v)
    assert "mini — Domo Modelo Mini: $125,000.00 por pieza" in texto
    assert "NUNCA digas un precio" in texto
    assert "No ofrezcas ni prometas descuentos" in texto
    assert "flete y los extras NO los cotizas" in texto
    assert "más de 5 piezas o un total arriba de $1,000,000.00" in texto
    assert "sin decir que ya se la mandaste" in texto

    otro = vendedor_del_contexto(
        {
            "vendedor": {
                **VENDEDOR,
                "preciosEnChat": False,
                "modoEnvio": "sola",
                "descuentoMaxPct": 5,
                "puedeCotizarExtras": True,
                "topeMonto": None,
                "piezasMax": None,
            }
        }
    )
    texto = reglas_de_vendedor(otro)
    assert "NO digas precios en el chat" in texto
    assert "hasta 5% " in texto
    assert "flete — Flete: $12,000.00 por servicio (extra)" in texto
    assert "el PDF ya le llegó" in texto
    assert "la prepara un asesor" not in texto


async def test_sin_vendedor_no_hay_cotizar(respx_mock):
    ctx = _ctx()
    _con_vendedor(respx_mock, vendedor=None)
    ctx.llm.replies = [LlmReply(content="Con gusto te ayudo.")]
    await _turno(ctx)
    nombres = {t["function"]["name"] for t in ctx.llm.calls[0]["tools"] or []}
    assert "cotizar" not in nombres
    await ctx.crm.aclose()


async def test_cotiza_sin_mandar_precios(respx_mock):
    # Aunque el perfil tenga el catálogo de herramientas recortado, cotizar
    # lo decide Ajustes → Ventas → Vendedor.
    ctx = _ctx(tools=frozenset({"ficha"}))
    rutas = _con_vendedor(respx_mock)
    cot = respx_mock.post(f"{CRM_URL}/api/bot/cotizacion").mock(
        return_value=httpx.Response(
            200,
            json={"estado": "por_aprobar", "folio": "COT-0007", "total": "$290,000.00", "anticipo": "$145,000.00", "resumen": "2 × Domo Modelo Mini"},
        )
    )
    ctx.llm.replies = [
        LlmReply(
            content=None,
            tool_calls=[
                ToolCall(
                    id="c",
                    name="cotizar",
                    arguments={"items": [{"producto_id": "mini", "cantidad": 2, "precio": 1}], "a_nombre_de": "Saúl"},
                )
            ],
        ),
        LlmReply(content="¡Listo! Un asesor la revisa y te la manda en breve."),
    ]
    await _turno(ctx)

    primera = ctx.llm.calls[0]
    herramienta = next(t for t in primera["tools"] if t["function"]["name"] == "cotizar")
    props = herramienta["function"]["parameters"]["properties"]
    assert props["items"]["items"]["properties"]["producto_id"]["enum"] == ["mini"]
    assert "descuento_pct" not in props
    assert "Vendedor: puedes armar la cotización formal" in primera["messages"][0]["content"]
    assert json.loads(cot.calls[0].request.content) == {
        "conversationId": CRM_CONV_ID,
        "items": [{"productoId": "mini", "cantidad": 2}],
        "cliente": "Saúl",
        "descuentoPct": 0,
    }
    resultado = json.loads(ctx.llm.calls[1]["messages"][-1]["content"])
    assert resultado["ok"] is True and resultado["estado"] == "por_aprobar" and resultado["folio"] == "COT-0007"
    assert rutas["messages"].called
    assert not rutas["handoff"].called
    await ctx.crm.aclose()


async def test_un_producto_inventado_no_llega_al_crm(respx_mock):
    ctx = _ctx()
    _con_vendedor(respx_mock)
    cot = respx_mock.post(f"{CRM_URL}/api/bot/cotizacion").mock(return_value=httpx.Response(200, json={}))
    ctx.llm.replies = [
        LlmReply(content=None, tool_calls=[ToolCall(id="c", name="cotizar", arguments={"items": [{"producto_id": "flete", "cantidad": 1}]})]),
        LlmReply(content="El flete te lo confirma un asesor."),
    ]
    await _turno(ctx)
    resultado = json.loads(ctx.llm.calls[1]["messages"][-1]["content"])
    assert resultado["ok"] is False and resultado["error"] == "producto_desconocido"
    assert not cot.called
    await ctx.crm.aclose()


async def test_rebasa_el_limite_pasa_a_una_persona(respx_mock):
    ctx = _ctx()
    rutas = _con_vendedor(respx_mock)
    respx_mock.post(f"{CRM_URL}/api/bot/cotizacion").mock(
        return_value=httpx.Response(
            409,
            json={"error": {"code": "cantidad_maxima", "message": "Son 8 piezas; más de 5 las cotiza una persona"}, "paraPersona": True},
        )
    )
    ctx.llm.replies = [
        LlmReply(content=None, tool_calls=[ToolCall(id="c", name="cotizar", arguments={"items": [{"producto_id": "mini", "cantidad": 8}]})]),
        LlmReply(content="Para 8 domos un asesor te prepara tu cotización en breve."),
    ]
    await _turno(ctx, "quiero 8 domos")
    resultado = json.loads(ctx.llm.calls[1]["messages"][-1]["content"])
    assert resultado["ok"] is False and resultado["error"] == "cantidad_maxima"
    assert "asesor" in resultado["nota"]
    assert rutas["handoff"].called
    await ctx.crm.aclose()


async def test_error_corregible_no_escala(respx_mock):
    ctx = _ctx()
    rutas = _con_vendedor(respx_mock)
    respx_mock.post(f"{CRM_URL}/api/bot/cotizacion").mock(
        return_value=httpx.Response(
            409,
            json={"error": {"code": "persona_armando", "message": "Un asesor ya está armando su cotización"}, "paraPersona": False},
        )
    )
    ctx.llm.replies = [
        LlmReply(content=None, tool_calls=[ToolCall(id="c", name="cotizar", arguments={"items": [{"producto_id": "mini", "cantidad": 1}]})]),
        LlmReply(content="Un asesor ya está preparando tu cotización."),
    ]
    await _turno(ctx)
    resultado = json.loads(ctx.llm.calls[1]["messages"][-1]["content"])
    assert resultado == {"ok": False, "error": "persona_armando", "detalle": "Un asesor ya está armando su cotización"}
    assert not rutas["handoff"].called
    await ctx.crm.aclose()
