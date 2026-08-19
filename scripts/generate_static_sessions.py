#!/usr/bin/env python3
"""Genera una página HTML estática por sesión.

La vista interactiva (session-detail.html?id=...) arma todo con JS, así que el
robot de Google recibe un documento vacío y la marca como soft 404. Este script
escribe el mismo contenido ya renderizado en docs/sessions/<id>.html y arma el
sitemap del subsitio.

    python3 scripts/generate_static_sessions.py

Se ejecuta después del pipeline, cuando docs/data/catalog.json ya está al día.
"""
from __future__ import annotations

import html
import json
import pathlib
import sys
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
DATA = DOCS / "data"
OUT = DOCS / "sessions"
BASE = "https://luisreinoso.dev/asamblea-abierta"

MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
         "agosto", "septiembre", "octubre", "noviembre", "diciembre"]

PAGINAS_FIJAS = ["", "sessions.html", "speakers.html", "topics.html", "download.html"]


def fecha_larga(iso: str) -> str:
    if not iso:
        return "Fecha no disponible"
    try:
        d = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return iso
    return f"{d.day} de {MESES[d.month - 1]} de {d.year}"


def formato_tiempo(segundos: float) -> str:
    s = int(segundos)
    h, m, sec = s // 3600, (s % 3600) // 60, s % 60
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def agrupa_por_orador(segmentos: list[dict]) -> list[dict]:
    """Junta segmentos consecutivos del mismo orador, igual que la vista JS."""
    grupos: list[dict] = []
    actual = None
    for seg in segmentos:
        orador = seg.get("speaker") or {}
        sid = orador.get("id") or "UNIDENTIFIED"
        if actual is None or actual["id"] != sid:
            if actual:
                grupos.append(actual)
            actual = {
                "id": sid,
                "nombre": orador.get("name") or "No identificado",
                "confianza": orador.get("confidence") or 0,
                "inicio": seg.get("start") or 0,
                "textos": [],
            }
        actual["textos"].append((seg.get("text") or "").strip())
    if actual:
        grupos.append(actual)
    return grupos


def descripcion(sesion: dict) -> str:
    clasif = sesion.get("classification") or {}
    texto = clasif.get("summary") or (sesion.get("text") or "")
    texto = " ".join(texto.split())
    if len(texto) > 155:
        texto = texto[:152].rsplit(" ", 1)[0] + "..."
    return texto or "Transcripción de una sesión de la Asamblea Nacional del Ecuador."


def render(sesion: dict) -> str:
    sid = sesion["id"]
    titulo = sesion.get("title") or "Sesión"
    video = sesion.get("source_url") or sesion.get("url") or ""
    minutos = round((sesion.get("duration") or 0) / 60)
    grupos = agrupa_por_orador(sesion.get("segments") or [])
    oradores = sesion.get("speaker_stats") or []
    clasif = sesion.get("classification") or {}

    e = html.escape

    partes = []
    for g in grupos:
        identificado = g["id"] != "UNIDENTIFIED"
        color = "var(--primary-color)" if identificado else "var(--text-light)"
        confianza = f" ({round(g['confianza'] * 100)}%)" if identificado else ""
        etiqueta = formato_tiempo(g["inicio"])
        enlace = f"{video}&t={int(g['inicio'])}s" if video else "#"
        cuerpo = e(" ".join(t for t in g["textos"] if t))
        partes.append(f"""            <div style="margin-bottom: 1.5rem; padding-bottom: 1rem; border-bottom: 1px solid #e0e0e0;">
                <div style="display: flex; align-items: center; margin-bottom: 0.5rem;">
                    <a href="{e(enlace)}" target="_blank" rel="noopener"
                       style="color: var(--primary-color); text-decoration: none; font-weight: bold; font-size: 0.875rem; margin-right: 1rem;"
                       title="Ir al minuto {etiqueta} en YouTube">🕒 {etiqueta}</a>
                    <span style="color: {color}; font-weight: bold;">{e(g['nombre'])}{confianza}</span>
                </div>
                <div style="line-height: 1.8; color: var(--text-dark);">{cuerpo}</div>
            </div>""")
    transcripcion = "\n".join(partes) or "            <p>Transcripción no disponible</p>"

    bloque_oradores = ""
    if oradores:
        filas = "".join(
            f"<li>{e(o.get('name') or 'No identificado')}"
            f" ({round((o.get('total_time') or 0) / 60)} min, {o.get('word_count') or 0} palabras)</li>"
            for o in oradores
        )
        bloque_oradores = f"""
            <div class="card mt-3">
                <h2>Quién habla</h2>
                <ul>{filas}</ul>
            </div>"""

    bloque_resumen = ""
    if clasif.get("summary"):
        bloque_resumen = f"""
            <div class="card mt-3">
                <h2>Resumen</h2>
                <p>{e(clasif['summary'])}</p>
            </div>"""

    temas = clasif.get("topics") or []
    bloque_temas = ""
    if temas:
        etiquetas = "".join(
            f'<span style="display: inline-block; background: var(--primary-color); color: white;'
            f' padding: 0.5rem 1rem; margin: 0.25rem; border-radius: 4px;">{e(t)}</span>'
            for t in temas
        )
        bloque_temas = f"""
            <div class="card mt-3">
                <h2>Temas discutidos</h2>
                <div>{etiquetas}</div>
            </div>"""

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta name="description" content="{e(descripcion(sesion))}">
    <title>{e(titulo)} - Asamblea Abierta</title>
    <link rel="canonical" href="{BASE}/sessions/{e(sid)}.html">
    <link rel="stylesheet" href="../assets/css/main.css?v=4">
</head>
<body>
    <header>
        <div class="container">
            <h1>🏛️ Asamblea Abierta</h1>
            <input type="checkbox" id="nav-toggle" class="nav-toggle">
            <label for="nav-toggle" class="nav-toggle-label" aria-label="Abrir menú">☰</label>
            <nav>
                <ul>
                    <li><a href="../">Inicio</a></li>
                    <li><a href="../sessions.html">Sesiones</a></li>
                    <li><a href="../speakers.html">Asambleístas</a></li>
                    <li><a href="../topics.html">Temas</a></li>
                    <li><a href="../download.html">Descargas</a></li>
                </ul>
            </nav>
        </div>
    </header>

    <section class="section">
        <div class="container">
            <h1>{e(titulo)}</h1>

            <div class="card mt-3">
                <p><strong>📅 Fecha:</strong> {fecha_larga(sesion.get('date') or '')}</p>
                <p><strong>⏱️ Duración:</strong> {minutos} minutos</p>
                <p><strong>🎤 Oradores:</strong> {len(oradores)}</p>
                <p><strong>📺 Video:</strong> <a href="{e(video)}" target="_blank" rel="noopener">Ver en YouTube</a></p>
            </div>
{bloque_resumen}{bloque_temas}{bloque_oradores}

            <div class="card mt-3">
                <h2>Transcripción completa</h2>
                <p style="color: var(--text-light); font-size: 0.875rem;">
                    Transcripción automática generada con IA. Puede contener errores.
                    Cada marca de tiempo abre el video oficial en ese minuto.
                </p>
                <div style="background: var(--bg-light); padding: 1rem; border-radius: 4px; margin-top: 1rem;">
{transcripcion}
                </div>
            </div>

            <div class="card mt-3">
                <h2>Descargar datos</h2>
                <p><a class="btn" href="../data/sessions/{e(sid)}.json" download>Descargar JSON completo</a></p>
            </div>
        </div>
    </section>

    <footer>
        <div class="container">
            <p>
                Asamblea Abierta - Transparencia Legislativa<br>
                Datos de dominio público bajo licencia <a href="https://creativecommons.org/publicdomain/zero/1.0/" target="_blank" rel="noopener">CC0 1.0</a>
            </p>
        </div>
    </footer>
</body>
</html>
"""


def escribe_sitemap(ids_fechas: list[tuple[str, str]]) -> None:
    hoy = datetime.now(timezone.utc).date().isoformat()
    filas = []
    for pagina in PAGINAS_FIJAS:
        filas.append(f"  <url>\n    <loc>{BASE}/{pagina}</loc>\n    <lastmod>{hoy}</lastmod>\n  </url>")
    for sid, fecha in ids_fechas:
        lastmod = (fecha or "")[:10] or hoy
        filas.append(f"  <url>\n    <loc>{BASE}/sessions/{sid}.html</loc>\n    <lastmod>{lastmod}</lastmod>\n  </url>")
    contenido = ('<?xml version="1.0" encoding="UTF-8"?>\n'
                 '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
                 + "\n".join(filas) + "\n</urlset>\n")
    (DOCS / "sitemap.xml").write_text(contenido, encoding="utf-8")


def main() -> int:
    catalogo = json.loads((DATA / "catalog.json").read_text(encoding="utf-8"))
    sesiones = catalogo.get("sessions") or []
    OUT.mkdir(exist_ok=True)

    escritas, saltadas, ids_fechas = 0, 0, []
    for entrada in sesiones:
        sid = entrada.get("id")
        archivo = DATA / "sessions" / f"{sid}.json"
        if not sid or not archivo.exists():
            saltadas += 1
            continue
        sesion = json.loads(archivo.read_text(encoding="utf-8"))
        sesion.setdefault("id", sid)
        sesion.setdefault("title", entrada.get("title"))
        sesion.setdefault("date", entrada.get("date"))
        (OUT / f"{sid}.html").write_text(render(sesion), encoding="utf-8")
        ids_fechas.append((sid, sesion.get("date") or entrada.get("date") or ""))
        escritas += 1

    escribe_sitemap(ids_fechas)
    print(f"paginas generadas: {escritas}")
    if saltadas:
        print(f"sesiones sin JSON, saltadas: {saltadas}")
    print(f"sitemap: docs/sitemap.xml ({len(ids_fechas) + len(PAGINAS_FIJAS)} urls)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
