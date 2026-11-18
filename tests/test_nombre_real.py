"""Módulo de la agencia — el nombre real del lead (no el de su perfil de
WhatsApp) se guarda con update_ficha: `nombre` y `empresa`. El CRM, con su
bandera NOMBRE_REAL, lo pone como nombre del contacto."""
from __future__ import annotations

from app.profile import BusinessProfile
from app.prompt import build_system_prompt
from app.state import Conversation
from app.tools import TOOL_SCHEMAS
from tests.conftest import crm_context


def _ficha():
    return next(t for t in TOOL_SCHEMAS if t["function"]["name"] == "update_ficha")


def test_update_ficha_trae_nombre_y_empresa():
    props = _ficha()["function"]["parameters"]["properties"]
    assert "nombre" in props and "empresa" in props
    assert "perfil de WhatsApp" in props["nombre"]["description"]


def _prompt(perfil: BusinessProfile) -> str:
    contexto = crm_context()
    contexto["contact"]["name"] = "J"
    return build_system_prompt(profile=perfil, context=contexto, conv=Conversation(id=1, wa_identity="5215550000000"))


def test_el_prompt_pide_el_nombre_real():
    prompt = _prompt(BusinessProfile(agent_name="Sofi"))
    assert "- Nombre del lead: J." in prompt
    assert "a nombre de quién va la cotización" in prompt
    assert "nunca el primero" in prompt
    assert "`nombre` (el de la persona)" in prompt
    assert "nombre «Saúl» y empresa «Domos Garza»" in prompt


def test_sin_ficha_no_se_pide():
    prompt = _prompt(BusinessProfile(agent_name="Sofi", tools=frozenset({"salida"})))
    assert "a nombre de quién va la cotización" not in prompt
