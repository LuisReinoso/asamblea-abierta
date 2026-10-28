#!/usr/bin/env python3
"""Genera una página HTML estática por sesión.

La vista interactiva (session-detail.html?id=...) arma todo con JS, así que el
robot de Google recibe un documento vacío y la marca como soft 404. Este script
escribe el mismo contenido ya renderizado en docs/sessions/<id>.html y arma el
sitemap del subsitio.

La página es también el reproductor: el HTML trae la transcripción, la línea de
tiempo de oradores y los enlaces a YouTube (todo funciona sin JS), y
assets/js/player.js le suma el video incrustado sincronizado con el texto.

    python3 scripts/generate_static_sessions.py

Se ejecuta después del pipeline, cuando docs/data/catalog.json ya está al día
(run_batch.py lo llama tras 08_update_catalog.py).
"""
from __future__ import annotations

import html
import json
import pathlib
import re
import sys
import unicodedata
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

ROOT = pathlib.Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
DATA = DOCS / "data"
OUT = DOCS / "sessions"
BASE = "https://luisreinoso.dev/asamblea-abierta"
ROSTER = ROOT / "data" / "speakers" / "asambleistas.json"

MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
         "agosto", "septiembre", "octubre", "noviembre", "diciembre"]

PAGINAS_FIJAS = ["", "sessions.html", "speakers.html", "topics.html", "download.html"]

NO_ID = "No identificado"
# Un párrafo de lectura cómoda; los turnos largos (un discurso de 7 min es un
# solo segmento de diarización) se parten en párrafos de este tamaño.
PALABRAS_POR_PARRAFO = 90


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


def normaliza(nombre: str) -> str:
    sin_tildes = "".join(c for c in unicodedata.normalize("NFKD", nombre) if not unicodedata.combining(c))
    return " ".join(sin_tildes.lower().split())


def clave_orador(nombre: str) -> str:
    return "unk" if nombre == NO_ID else re.sub(r"[^a-z0-9]+", "-", normaliza(nombre)).strip("-")


def iniciales(nombre: str) -> str:
    if nombre == NO_ID:
        return "?"
    partes = [p for p in nombre.split() if p[:1].isupper()]
    if not partes:
        return "?"
    # Nombre completo ecuatoriano: nombres + dos apellidos. La inicial del primer
    # apellido identifica mejor que la del segundo nombre.
    apellido = partes[-2] if len(partes) >= 3 else partes[-1]
    return (partes[0][0] + apellido[0]).upper()


def video_id(url: str) -> str:
    if not url:
        return ""
    q = parse_qs(urlparse(url).query).get("v")
    if q:
        return q[0]
    return urlparse(url).path.rstrip("/").rsplit("/", 1)[-1]


def numero_sesion(titulo: str) -> str | None:
    m = re.search(r"Sesi[oó]n(?:\s+No\.?)?\s*(\d+)", titulo or "", re.IGNORECASE)
    return str(int(m.group(1))) if m else None


def es_completa(sesion: dict) -> bool:
    return sesion.get("video_type") == "full_session"


def origen_identificacion(orador: dict) -> str:
    if orador.get("name", NO_ID) == NO_ID:
        return ""
    return "identificado por voz" if orador.get("source") == "voiceprint" else "identificado por rótulo"


def parrafos(texto: str, inicio: float, fin: float) -> list[tuple[float, str]]:
    """Parte un segmento largo en párrafos por frases. El tiempo de cada párrafo
    se interpola por posición de palabra: el JSON solo guarda el inicio y el fin
    del turno, así que el resaltado dentro de un turno es aproximado."""
    palabras_totales = len(texto.split()) or 1
    frases = re.split(r"(?<=[.!?¿¡])\s+", " ".join(texto.split()))
    salida, actual, contadas, inicio_parrafo = [], [], 0, 0
    for frase in frases:
        actual.append(frase)
        if sum(len(f.split()) for f in actual) >= PALABRAS_POR_PARRAFO:
            salida.append((inicio_parrafo, " ".join(actual)))
            contadas += sum(len(f.split()) for f in actual)
            inicio_parrafo, actual = contadas, []
    if actual:
        salida.append((inicio_parrafo, " ".join(actual)))
    duracion = max(0.0, fin - inicio)
    return [(inicio + duracion * pos / palabras_totales, t) for pos, t in salida if t]


def turnos(segmentos: list[dict]) -> list[dict]:
    """Junta segmentos consecutivos del mismo orador en un turno."""
    salida: list[dict] = []
    for seg in segmentos:
        orador = seg.get("speaker") or {}
        nombre = orador.get("name") or NO_ID
        ini, fin = seg.get("start") or 0, seg.get("end") or 0
        if salida and salida[-1]["nombre"] == nombre:
            t = salida[-1]
            t["fin"] = fin
        else:
            t = {"nombre": nombre, "clave": clave_orador(nombre), "inicio": ini, "fin": fin,
                 "origen": origen_identificacion(orador), "confianza": orador.get("confidence") or 0,
                 "parrafos": []}
            salida.append(t)
        t["parrafos"].extend(parrafos(seg.get("text") or "", ini, fin))
    return salida


def carga_roster() -> dict[str, dict]:
    try:
        datos = json.loads(ROSTER.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {normaliza(a["name"]): a for a in datos.get("asambleistas", [])}


def resumen_oradores(lista_turnos: list[dict], roster: dict[str, dict]) -> list[dict]:
    por_clave: dict[str, dict] = {}
    for t in lista_turnos:
        o = por_clave.setdefault(t["clave"], {"nombre": t["nombre"], "clave": t["clave"],
                                              "segundos": 0.0, "turnos": 0})
        o["segundos"] += t["fin"] - t["inicio"]
        o["turnos"] += 1
    for o in por_clave.values():
        ficha = roster.get(normaliza(o["nombre"])) or {}
        o["partido"] = ficha.get("party") or ""
        o["provincia"] = ficha.get("province") or ""
    # Los identificados primero, por tiempo; "No identificado" al final.
    return sorted(por_clave.values(), key=lambda o: (o["clave"] == "unk", -o["segundos"]))


def descripcion(sesion: dict) -> str:
    clasif = sesion.get("classification") or {}
    texto = clasif.get("summary") or (sesion.get("text") or "")
    texto = " ".join(texto.split())
    if len(texto) > 155:
        texto = texto[:152].rsplit(" ", 1)[0] + "..."
    return texto or "Transcripción de una sesión de la Asamblea Nacional del Ecuador."


def render(sesion: dict, roster: dict[str, dict], relacionadas: dict) -> str:
    e = html.escape
    sid = sesion["id"]
    titulo = sesion.get("title") or "Sesión"
    url = sesion.get("source_url") or sesion.get("url") or ""
    vid = video_id(url)
    duracion = float(sesion.get("duration") or 0) or 1.0
    lista = turnos(sesion.get("segments") or [])
    oradores = resumen_oradores(lista, roster)
    n_identificados = sum(1 for o in oradores if o["clave"] != "unk")
    completa = es_completa(sesion)
    clasif = sesion.get("classification") or {}

    def enlace_yt(t: float) -> str:
        return f"{url}&t={int(t)}s" if url else "#"

    # Cabecera: qué es esto (sesión completa vs intervención) en una frase.
    if completa:
        etiqueta_tipo = "Sesión del Pleno"
        clips = relacionadas.get("clips_de", {}).get(sid, [])
        explicacion = (f"Grabación completa de la sesión: {formato_tiempo(duracion)} con "
                       f"{n_identificados} oradores identificados.")
        if clips:
            explicacion += f" Hay {len(clips)} intervenciones de esta sesión publicadas también como clips."
    else:
        etiqueta_tipo = "Intervención"
        principal = oradores[0]["nombre"] if oradores and oradores[0]["clave"] != "unk" else ""
        explicacion = "Clip oficial con una sola intervención"
        if principal:
            explicacion += f" de {e(principal)}"
        n = numero_sesion(titulo)
        explicacion += f", tomada de la Sesión {n}." if n else "."
        madre = relacionadas.get("completa_de", {}).get(sid)
        if madre:
            explicacion += f' <a href="{e(madre)}.html">Ver la sesión completa</a>'

    # Línea de tiempo: un bloque por turno, posición y ancho en % de la duración.
    bloques = []
    for t in lista:
        izq = 100 * t["inicio"] / duracion
        ancho = max(0.15, 100 * (t["fin"] - t["inicio"]) / duracion)
        rango = f"{formato_tiempo(t['inicio'])}–{formato_tiempo(t['fin'])}"
        bloques.append(
            f'<a class="tl-block{" tl-block--unk" if t["clave"] == "unk" else ""}" '
            f'href="{e(enlace_yt(t["inicio"]))}" data-seek="{t["inicio"]:.1f}" data-speaker="{e(t["clave"])}" '
            f'style="--x:{izq:.3f}%;--w:{ancho:.3f}%" title="{e(t["nombre"])} · {rango}">'
            f'<span class="sr-only">{e(t["nombre"])}, {rango}</span></a>')

    lista_oradores = "".join(
        f'<li><button type="button" class="speaker-chip{" speaker-chip--unk" if o["clave"] == "unk" else ""}" '
        f'data-speaker="{e(o["clave"])}" data-name="{e(o["nombre"])}" data-initials="{e(iniciales(o["nombre"]))}" '
        f'data-meta="{e(" · ".join(x for x in (o["partido"], o["provincia"]) if x))}">'
        f'<span class="avatar" aria-hidden="true">{e(iniciales(o["nombre"]))}</span>'
        f'<span class="speaker-chip-name">{e(o["nombre"])}</span>'
        f'<span class="speaker-chip-time">{max(1, round(o["segundos"] / 60))} min</span></button></li>'
        for o in oradores)

    articulos = []
    for t in lista:
        origen = (f'<span class="turn-source" title="Confianza {round(t["confianza"] * 100)}%">'
                  f'{e(t["origen"])}</span>') if t["origen"] else ""
        cuerpo = "".join(
            f'<p data-start="{ini:.1f}">{e(texto)}</p>' for ini, texto in t["parrafos"])
        articulos.append(f"""
                <article class="turn{" turn--unk" if t["clave"] == "unk" else ""}" data-speaker="{e(t["clave"])}" data-start="{t["inicio"]:.1f}" data-end="{t["fin"]:.1f}">
                    <div class="turn-head">
                        <a class="turn-time" href="{e(enlace_yt(t["inicio"]))}" data-seek="{t["inicio"]:.1f}" target="_blank" rel="noopener">{formato_tiempo(t["inicio"])}</a>
                        <span class="avatar avatar--sm" aria-hidden="true">{e(iniciales(t["nombre"]))}</span>
                        <span class="turn-speaker">{e(t["nombre"])}</span>
                        {origen}
                    </div>
                    {cuerpo}
                </article>""")
    transcripcion = "".join(articulos) or "<p>Transcripción no disponible</p>"

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
        etiquetas = "".join(f'<span class="tag">{e(t)}</span>' for t in temas)
        bloque_temas = f"""
            <div class="card mt-3">
                <h2>Temas discutidos</h2>
                <div class="tag-list">{etiquetas}</div>
            </div>"""

    bloque_clips = ""
    if completa and relacionadas.get("clips_de", {}).get(sid):
        items = "".join(f'<li><a href="{e(c["id"])}.html">{e(c["title"])}</a></li>'
                        for c in relacionadas["clips_de"][sid])
        bloque_clips = f"""
            <div class="card mt-3">
                <h2>Intervenciones de esta sesión</h2>
                <ul class="related-list">{items}</ul>
            </div>"""

    miniatura = f"https://i.ytimg.com/vi/{e(vid)}/hqdefault.jpg" if vid else ""

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta name="description" content="{e(descripcion(sesion))}">
    <title>{e(titulo)} - Asamblea Abierta</title>
    <link rel="canonical" href="{BASE}/sessions/{e(sid)}.html">
    <link rel="stylesheet" href="../assets/css/main.css?v=5">
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

    <main class="section watch">
        <div class="container">
            <div class="watch-head">
                <span class="kind-badge kind-badge--{"full" if completa else "clip"}">{etiqueta_tipo}</span>
                <h1>{e(titulo)}</h1>
                <p class="watch-meta">{fecha_larga(sesion.get('date') or '')} · {formato_tiempo(duracion)} · {n_identificados} {"orador identificado" if n_identificados == 1 else "oradores identificados"}</p>
                <p class="watch-explain">{explicacion}</p>
            </div>

            <div class="watch-grid">
                <div class="watch-stage">
                    <div class="player" data-video="{e(vid)}">
                        <a class="player-facade" href="{e(url)}" target="_blank" rel="noopener" style="--thumb:url('{miniatura}')">
                            <span class="player-play" aria-hidden="true"></span>
                            <span class="player-cta">Ver con la transcripción</span>
                        </a>
                    </div>

                    <div class="now-speaking" aria-live="polite" hidden>
                        <span class="avatar avatar--lg" aria-hidden="true"></span>
                        <div>
                            <span class="now-label">Hablando ahora</span>
                            <strong class="now-name"></strong>
                            <span class="now-meta"></span>
                        </div>
                    </div>

                    <div class="timeline">
                        <div class="timeline-head">
                            <h2>Quién habla y cuándo</h2>
                            <span class="timeline-hint">Toca un bloque para ir a ese momento</span>
                        </div>
                        <div class="timeline-track">
                            {"".join(bloques)}
                            <span class="tl-playhead" aria-hidden="true"></span>
                        </div>
                        <div class="timeline-axis"><span>0:00</span><span>{formato_tiempo(duracion)}</span></div>
                        <ul class="speaker-list">{lista_oradores}</ul>
                    </div>
                </div>

                <section class="transcript" aria-label="Transcripción">
                    <div class="transcript-head">
                        <h2>Transcripción</h2>
                        <label class="follow-toggle" hidden><input type="checkbox" checked> Seguir el video</label>
                    </div>
                    <p class="transcript-note">Transcripción automática: puede contener errores. Cada marca de tiempo lleva a ese momento del video oficial.</p>
                    {transcripcion}
                </section>
            </div>
{bloque_resumen}{bloque_temas}{bloque_clips}

            <div class="card mt-3">
                <h2>Descargar datos</h2>
                <p><a class="btn" href="../data/sessions/{e(sid)}.json" download>Descargar JSON completo</a></p>
            </div>
        </div>
    </main>

    <footer>
        <div class="container">
            <p>
                Asamblea Abierta - Transparencia Legislativa<br>
                Datos de dominio público bajo licencia <a href="https://creativecommons.org/publicdomain/zero/1.0/" target="_blank" rel="noopener">CC0 1.0</a>
            </p>
        </div>
    </footer>
    <script src="../assets/js/player.js?v=1" defer></script>
</body>
</html>
"""


def relaciona(sesiones: list[dict]) -> dict:
    """Une cada clip con la sesión completa del mismo número y el mismo día. El
    número solo no basta: una "Continuación de la Sesión 101" es otro día que
    la Sesión 101 original, y los clips de ambas dicen "Sesión 101"."""
    def clave(s: dict) -> tuple | None:
        n, dia = numero_sesion(s.get("title") or ""), (s.get("date") or "")[:10]
        return (n, dia) if n and dia else None

    completas = {clave(s): s["id"] for s in sesiones
                 if s.get("video_type") == "full_session" and clave(s)}
    completa_de, clips_de = {}, {}
    for s in sesiones:
        if s.get("video_type") == "full_session":
            continue
        madre = completas.get(clave(s))
        if madre:
            completa_de[s["id"]] = madre
            clips_de.setdefault(madre, []).append({"id": s["id"], "title": s.get("title") or s["id"]})
    return {"completa_de": completa_de, "clips_de": clips_de}


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
    roster = carga_roster()
    relacionadas = relaciona(sesiones)

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
        sesion.setdefault("video_type", entrada.get("video_type"))
        (OUT / f"{sid}.html").write_text(render(sesion, roster, relacionadas), encoding="utf-8")
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
