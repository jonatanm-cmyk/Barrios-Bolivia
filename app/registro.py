"""Registro de zonas buscadas que no existen o no tienen polígono.

Cada evento se escribe como una línea JSON en tres destinos, los que estén disponibles:
  - el log del proceso (siempre; en Vercel queda en los logs de la función),
  - un archivo .jsonl local (solo si el disco es escribible, es decir, en local),
  - un webhook (cualquier URL que reciba un POST JSON) si está definida WEBHOOK_ZONAS_NO_ENCONTRADAS.
"""
import json
import logging
import os
import threading
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("geoubicacion.registro")

RUTA = Path(os.environ.get("REGISTRO_ZONAS", Path(__file__).resolve().parent.parent / "registro" / "zonas_no_encontradas.jsonl"))
WEBHOOK = os.environ.get("WEBHOOK_ZONAS_NO_ENCONTRADAS", "").strip()
_lock = threading.Lock()


def _enviar_webhook(evento: dict) -> None:
    try:
        req = urllib.request.Request(WEBHOOK, data=json.dumps(evento).encode(), method="POST",
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=3).close()
    except Exception as e:  # noqa: BLE001 — el registro nunca debe tumbar la consulta
        log.warning("webhook de registro falló: %s", e)


def registrar(tipo: str, consulta: str, **detalle) -> None:
    """tipo: no_encontrada | sin_poligono | aproximado | coordenada_sin_barrio | fuera_de_bolivia."""
    evento = {"fecha": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "tipo": tipo, "consulta": consulta, **detalle}
    linea = json.dumps(evento, ensure_ascii=False)
    log.info("ZONA_NO_ENCONTRADA %s", linea)
    try:
        with _lock:
            RUTA.parent.mkdir(parents=True, exist_ok=True)
            with RUTA.open("a", encoding="utf-8") as f:
                f.write(linea + "\n")
    except OSError:
        pass  # disco de solo lectura (Vercel): queda el log y el webhook
    if WEBHOOK:
        threading.Thread(target=_enviar_webhook, args=(evento,), daemon=True).start()


def resumen(limite: int = 100) -> dict:
    """Consultas registradas agrupadas por tipo y texto, las más repetidas primero."""
    if not RUTA.exists():
        return {"disponible": False, "motivo": "sin registro local (en Vercel revisar logs o webhook)"}
    conteo, ultima = Counter(), {}
    with RUTA.open(encoding="utf-8") as f:
        for linea in f:
            try:
                e = json.loads(linea)
            except ValueError:
                continue
            k = (e["tipo"], e["consulta"])
            conteo[k] += 1
            ultima[k] = e
    return {"disponible": True, "total_eventos": sum(conteo.values()), "consultas": [
        {"tipo": t, "consulta": c, "veces": n, "ultima_vez": ultima[(t, c)]["fecha"],
         **{k: v for k, v in ultima[(t, c)].items() if k not in ("fecha", "tipo", "consulta")}}
        for (t, c), n in conteo.most_common(limite)]}
