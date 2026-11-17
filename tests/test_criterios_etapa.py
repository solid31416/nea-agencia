"""Módulo de la agencia — cuándo va cada etapa del embudo de venta consultiva."""

from app.prompt import criterios_de_etapa


def test_calificado_pide_uso_ubicacion_y_cantidad():
    out = criterios_de_etapa(["Calificado", "Cita por videollamada"])
    assert len(out) == 2
    assert "«Calificado»" in out[0]
    for palabra in ("uso", "instalar", "piezas"):
        assert palabra in out[0]
    assert "«Cita por videollamada»" in out[1]
    assert "agenda" in out[1]


def test_etapas_sin_criterio_no_agregan_nada():
    assert criterios_de_etapa(["Nuevo", "Interesado"]) == []
    assert criterios_de_etapa([]) == []


def test_sin_acentos_y_mayusculas():
    assert criterios_de_etapa(["CALIFICADÓ"])
    assert criterios_de_etapa(["Reunión"])
