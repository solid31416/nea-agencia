"""Módulo de la agencia (Treviño AI) — Ponerse al día.

El despacho trae la ráfaga del cliente; lo que pasó mientras Nea no habló (una
persona del equipo contestó desde la bandeja, el cliente escribió con la IA en
pausa) no estaba en su historial, y al reactivar la IA contestaba sin saber de
qué se había hablado.

El CRM del fork de la agencia (bandera `PONER_AL_DIA_AL_AGENTE`) manda en el
despacho `ponerseAlDia`: esos mensajes, con quién los dijo. Aquí se vuelven UNA
nota de sistema en el historial, antes del mensaje del cliente que abre el
turno, y cada mensaje se anota como visto para no contarlo dos veces (el CRM
lo repite mientras Nea no vuelva a hablar).

Sin la clave (CRM raíz, bandera apagada) no pasa nada: `mensajes_al_dia` da
una lista vacía. La nota es rol `system`: no cuenta como mensaje del lead ni de
Nea en los gates (hostilidad, candado de cierre, respuestas repetidas).
"""
from __future__ import annotations

import logging
from typing import Any

from app.state import AppContext

logger = logging.getLogger("nea.ponerse_al_dia")

MAX_MENSAJES = 20
MAX_TEXTO = 1000


def mensajes_al_dia(payload: dict[str, Any]) -> list[dict[str, str]]:
    """Lo que viene en `ponerseAlDia`, validado. Lo malformado se descarta."""
    crudos = payload.get("ponerseAlDia")
    if not isinstance(crudos, list):
        return []
    validos: list[dict[str, str]] = []
    for m in crudos[-MAX_MENSAJES:]:
        if not isinstance(m, dict):
            continue
        ident, de, texto = m.get("id"), m.get("de"), m.get("texto")
        if not isinstance(ident, str) or not ident:
            continue
        if de not in ("cliente", "equipo"):
            continue
        if not isinstance(texto, str) or not texto.strip():
            continue
        validos.append({"id": ident, "de": de, "texto": texto.strip()[:MAX_TEXTO]})
    return validos


def nota_al_dia(mensajes: list[dict[str, str]]) -> str:
    """La nota que entra al historial. Deja claro que Nea NO lo escribió."""
    lineas = [
        f"- {'El cliente' if m['de'] == 'cliente' else 'Una persona del equipo'}: «{m['texto']}»"
        for m in mensajes
    ]
    return (
        "Mientras tú no contestabas (la IA estaba en pausa o una persona del "
        "equipo atendió), la conversación siguió así. Tú NO escribiste nada de "
        "esto:\n"
        + "\n".join(lineas)
        + "\nTómalo en cuenta para tu siguiente respuesta: lo que el equipo "
        "dijo o acordó con el cliente vale. No repitas lo que el equipo ya "
        "respondió ni digas que tú lo escribiste."
    )


def marca_al_dia(org_id: str, message_id: str) -> str:
    """Marca de un mensaje del CRM ya contado en una nota (con la organización)."""
    return f"aldia:{org_id}:{message_id}"


async def anotar_al_dia(
    ctx: AppContext, conv_id: int, mensajes: list[dict[str, str]]
) -> int:
    """Escribe la nota con lo que no se había contado. Devuelve cuántos contó.

    Nunca rompe el turno: un fallo aquí solo se registra y el turno sigue sin
    ponerse al día, igual que con un CRM que no manda la clave.
    """
    if not mensajes:
        return 0
    try:
        org_id = (ctx.organizacion or ("", ""))[0]
        marcas = [marca_al_dia(org_id, m["id"]) for m in mensajes]
        vistos = await ctx.store.already_processed(marcas)
        nuevos = [m for m, marca in zip(mensajes, marcas) if marca not in vistos]
        if not nuevos:
            return 0
        await ctx.store.add_message(conv_id, "system", nota_al_dia(nuevos))
        for m in nuevos:
            await ctx.store.mark_processed(marca_al_dia(org_id, m["id"]))
        return len(nuevos)
    except Exception:
        logger.exception("no pude ponerme al día en la conversación %s", conv_id)
        return 0
