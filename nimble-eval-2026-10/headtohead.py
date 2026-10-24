#!/usr/bin/env python3
# Head-to-head: tev1:4b (decisor productivo del local-router) vs nimble-v4-max (API hosted Bespoke).
# Bateria A: 15 turnos etiquetados -> grid de 6 familias (criterios verbatim de grid.py).
# Bateria B: 3 payloads moderacion/triage (caso de uso game servers).
# Secuencial (CPU time-slicing), resultados a results.jsonl, todo stdlib.

import json
import os
import statistics
import time
import urllib.request

SCRATCH = "/root/.hermes/cache/scratch/tev1-vs-nimble"
os.makedirs(SCRATCH, exist_ok=True)
RESULTS = os.path.join(SCRATCH, "results.jsonl")

KEY = None
with open(os.path.expanduser("~/.hermes/.env")) as f:
    for line in f:
        if line.startswith("BESPOKE_API_KEY="):
            KEY = line.strip().split("=", 1)[1]
assert KEY, "BESPOKE_API_KEY no encontrada en .env"

# ---- criterios VERBATIM de /root/.hermes/plugins/local-router/grid.py ----
GRID_CRITERIA = {
    "chatter": "TASK FAMILY: light chat and quick lookups — greetings, thanks, jokes, one-line factual questions. The cheapest model handles this perfectly.",
    "ops": "TASK FAMILY: routine machine operations — start/restart a service, read one log, check a status, simple config edits. Straightforward steps, low risk.",
    "ops_hard": "TASK FAMILY: complex machine operations — multi-service migrations, debugging why something fails intermittently, risky config changes where a wrong move costs downtime.",
    "writing": "TASK FAMILY: summarising, translating, extracting structure, or light drafting over longer material. Fast and cheap is fine, but it must handle full sentences well.",
    "code": "TASK FAMILY: producing or fixing code — write scripts, refactor, debug logic errors, review diffs. Precise technical text generation.",
    "deep": "TASK FAMILY: heavy thinking in prose — compare options with tradeoffs, weigh decisions, synthesize many threads into a recommendation, do math. Slow careful reasoning pays.",
}
ROUTE_QUESTION = {
    "route": {
        "type": "choice",
        "instructions": "Which model profile fits this user message best? Pick by the dominant activity the message asks for.",
        "criteria": GRID_CRITERIA,
    }
}
CONTEXT = "Routing a chat model for Christian's personal agent (Telegram + cron tasks)."

LOCAL_URL = "http://localhost:11434/v1/systemone"
REMOTE_URL = "https://api.bespokelabs.ai/v1/systemone"
THRESHOLD = 0.5  # umbral productivo
PROD_TIMEOUT_MS = 12000  # DECISION_TIMEOUT_S productivo

# ---------------- Bateria A: turnos etiquetados ----------------
BATTERY_A = [
    ("A1", "chatter", "Gracias por todo, que tengas buena noche", ["assistant: Listo, el respaldo quedó verificado."]),
    ("A2", "chatter", "hola! cómo estás hoy?", []),
    ("A3", "ops", "Reinicia el servidor de Minecraft SCL-3 por favor", []),
    ("A4", "ops", "Muéstrame las últimas 20 líneas del log de suwayomi", []),
    ("A5", "ops", "Revisa si el disco USB de respaldo sigue montado y dime el espacio libre", []),
    ("A6", "ops_hard", "El panel de Pterodactyl devuelve 502 intermitentemente: a veces carga y a veces no. Investiga por qué falla solo a ratos y déjalo estable", []),
    ("A7", "ops_hard", "Migra todos los contenedores del nodo viejo al disco nuevo sin cortar el servicio a los clientes", []),
    ("A8", "writing", "Resume el acta de la reunión en 5 puntos con los compromisos y responsables", []),
    ("A9", "writing", "Traduce este correo al inglés y responde cordialmente", []),
    ("A10", "writing", "Extrae de este contrato los plazos y las multas y ponlos en una tabla", []),
    ("A11", "code", "Escribe un script en Python que revise los contenedores Docker cada 5 minutos y avise por Telegram si alguno muere", []),
    ("A12", "code", "El plugin local-router lanza TypeError: classify() missing 1 required positional argument. Arregla el bug en decide.py", []),
    ("A13", "deep", "Compara WireGuard contra Tailscale para conectar el panel en remoto: costos, riesgos y ventajas. Recomiéndame uno", []),
    ("A14", "deep", "Considera todo lo que hablamos: la mudanza del nodo, el precio de los planes y las quejas de jugadores por lag. ¿Me conviene migrar ahora o esperar el fin de temporada?", []),
    ("A15", "deep", "Con estos números de ventas de septiembre y el costo mensual del servidor, conviene subir el precio del plan Pro o crear un plan intermedio?", []),
]

# ---------------- Bateria B: moderacion / triage ----------------
MOD_QUESTIONS = {
    "acoso": {
        "type": "noul",
        "instructions": "¿Contiene este mensaje acoso o insultos dirigidos a otro jugador?",
    },
    "accion": {
        "type": "choice",
        "instructions": "¿Qué acción de moderación corresponde aplicar?",
        "criteria": {
            "nada": "Broma inofensiva entre amigos",
            "warn": "Amonestación leve, insulto ocasional",
            "mute": "Silencio temporal, insultos reiterados con lenguaje ofensivo",
            "ban": "Baneo permanente, acoso grave, odio o amenazas",
        },
    },
}
TICKET_QUESTIONS = {
    "urgencia": {
        "type": "score",
        "instructions": "¿Qué tan urgente es este ticket?",
        "criteria": [
            "Consulta rutinaria, sin afectación de servicio",
            "Problema menor con workaround",
            "Servicio degradado que afecta a varios usuarios",
            "Caída total que afecta a usuarios que pagan",
        ],
    },
    "reembolso": {
        "type": "noul",
        "instructions": "¿El cliente menciona o insinúa pedir reembolso o compensación?",
    },
    "equipo": {
        "type": "choice",
        "instructions": "¿A qué equipo debe escalarse?",
        "criteria": {
            "ninguno": "No requiere escalamiento",
            "soporte": "Atención general al cliente",
            "infra": "Problema de infraestructura, contenedores o nodos",
            "direccion": "Riesgo comercial, cliente amenaza con irse",
        },
    },
}
BATTERY_B = [
    (
        "B1_toxico",
        "Mensaje del chat del servidor Minecraft: \"sos un manco de mierda, vete a llorar con tu mamá, jugador basura, no mereces estar aqui\"",
        MOD_QUESTIONS,
        {"acoso": (">=", 0.7), "accion": ("in", ["warn", "mute", "ban"])},
    ),
    (
        "B2_benigno",
        "Mensaje del chat del servidor Minecraft: \"jajaja me ganaste de nuevo, que manco soy hoy, revancha en 5 minutos?\"",
        MOD_QUESTIONS,
        {"acoso": ("<=", 0.3), "accion": ("in", ["nada", "warn"])},
    ),
    (
        "B3_ticket",
        "Ticket de soporte del panel de game servers: \"El servidor de Minecraft SCL_San_Enrique #3 no arranca desde hace 2 horas. Afecta a 40 jugadores que ya pagaron este mes. El panel muestra el contenedor en estado starting y no responde.\"",
        TICKET_QUESTIONS,
        {"urgencia": (">=", 2.0), "reembolso": ("<=", 0.3), "equipo": ("in", ["infra"])},
    ),
]


def state_for(user_message, recent):
    return {"user_message": user_message, "recent": recent, "context": CONTEXT}


def call_local(state, questions, timeout=30):
    payload = json.dumps(
        {"model": "tev1:4b", "state": state, "questions": questions, "keep_alive": "2h"}
    ).encode("utf-8")
    req = urllib.request.Request(
        LOCAL_URL, data=payload, headers={"Content-Type": "application/json"}, method="POST"
    )
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = json.loads(r.read().decode("utf-8"))
    return body, int((time.time() - t0) * 1000)


def call_remote(state, questions, timeout=25):
    payload = json.dumps(
        {"model": "nimble-v4-max", "state": state, "questions": questions}
    ).encode("utf-8")
    req = urllib.request.Request(
        REMOTE_URL,
        data=payload,
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + KEY},
        method="POST",
    )
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = json.loads(r.read().decode("utf-8"))
    return body, int((time.time() - t0) * 1000)


def route_of(body):
    a = body.get("answers", {}).get("route", {})
    return a.get("choice"), float(a.get("confidence", 0.0))


def save(rec):
    with open(RESULTS, "a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


# ---------------- ejecucion ----------------
print("=== WARM-UP tev1:4b (paga el cold-load, keep_alive 2h) ===")
try:
    _, ms = call_local(state_for("warm up ping", []), ROUTE_QUESTION, timeout=150)
    print(f"warm-up OK: {ms} ms")
except Exception as e:
    print(f"WARM-UP FALLO: {type(e).__name__}: {e} — ollama local puede estar caido")

def run_battery_A():
    rows = []
    for iid, expected, msg, recent in BATTERY_A:
        st = state_for(msg, recent)
        rec = {"id": iid, "expected": expected, "msg": msg[:60]}
        try:
            b1, ms1 = call_local(st, ROUTE_QUESTION)
            rec["tev1"] = dict(zip(("pick", "conf"), route_of(b1))) | {"ms": ms1}
        except Exception as e:
            rec["tev1"] = {"error": f"{type(e).__name__}: {e}"}
        try:
            b2, ms2 = call_remote(st, ROUTE_QUESTION)
            pick, conf = route_of(b2)
            rec["nimble"] = {"pick": pick, "conf": conf, "ms": ms2,
                             "tok": b2.get("usage", {}).get("input_tokens", 0)}
        except Exception as e:
            rec["nimble"] = {"error": f"{type(e).__name__}: {e}"}
        save(rec)
        rows.append(rec)
        t = rec.get("tev1", {}); n = rec.get("nimble", {})
        def _c(d):
            v = d.get("conf")
            return f"{v:.2f}" if isinstance(v, (int, float)) else str(v)
        print(f"{iid} exp={expected:9s} | tev1 {t.get('pick','ERR'):9s} c={_c(t):>4s} {t.get('ms','-')}ms"
              f" | nimble {n.get('pick','ERR'):9s} c={_c(n):>4s} {n.get('ms','-')}ms")
    return rows


def score_B(iid, expected, answers):
    out = {}
    for q, (op, target) in expected.items():
        a = answers.get(q, {})
        if q == "urgencia":
            val = float(a.get("score", -1))
        elif q == "acoso" or q == "reembolso":
            val = float(a.get("noul", -1))
        else:
            val = a.get("choice")
        if op == ">=":
            ok = val >= target
        elif op == "<=":
            ok = val <= target
        else:
            ok = val in target
        out[q] = {"val": val, "ok": ok}
    return out


def run_battery_B():
    rows = []
    for iid, state_text, questions, expected in BATTERY_B:
        st = {"user_message": state_text, "recent": [], "context": CONTEXT}
        rec = {"id": iid}
        try:
            b1, ms1 = call_local(st, questions)
            rec["tev1"] = {"answers": b1.get("answers", {}), "ms": ms1,
                           "score": score_B(iid, expected, b1.get("answers", {}))}
        except Exception as e:
            rec["tev1"] = {"error": f"{type(e).__name__}: {e}"}
        try:
            b2, ms2 = call_remote(st, questions)
            rec["nimble"] = {"answers": b2.get("answers", {}), "ms": ms2,
                             "score": score_B(iid, expected, b2.get("answers", {})),
                             "tok": b2.get("usage", {}).get("input_tokens", 0)}
        except Exception as e:
            rec["nimble"] = {"error": f"{type(e).__name__}: {e}"}
        save(rec)
        rows.append(rec)
        for side in ("tev1", "nimble"):
            r = rec.get(side, {})
            if "score" in r:
                marks = " ".join(f"{q}={s['val']}{'' if s['ok'] else ' X'}" for q, s in r["score"].items())
                print(f"{iid:10s} {side:6s} {marks}  ({r['ms']}ms)")
            else:
                print(f"{iid:10s} {side:6s} ERROR {r.get('error','?')}")
    return rows


def summarize(rowsA, rowsB):
    print("\n=== RESUMEN BATERIA A (grid 6 familias, umbral productivo 0.5) ===")
    for side in ("tev1", "nimble"):
        ok = miss = fall = misroute = 0
        lats = []
        for r in rowsA:
            d = r.get(side, {})
            if "error" in d or "pick" not in d:
                misroute += 0
                continue
            lats.append(d["ms"])
            if d["pick"] == r["expected"]:
                if d["conf"] >= THRESHOLD:
                    ok += 1
                else:
                    fall += 1  # correcto pero bajo umbral -> no se enruta
            else:
                if d["conf"] >= THRESHOLD:
                    misroute += 1
                else:
                    fall += 1  # equivocado pero bajo umbral -> fallback seguro
        lat = f"p50={statistics.median(lats):.0f}ms max={max(lats)}ms" if lats else "sin datos"
        over = sum(1 for l in lats if l > PROD_TIMEOUT_MS)
        print(f"{side:6s}: enrutados-bien={ok}/15  misroutes={misroute}  bajo-umbral={fall}  latencia {lat}  sobre-timeout-productivo={over}")
    tok = sum(r.get("nimble", {}).get("tok", 0) for r in rowsA + rowsB)
    print(f"costo nimble total: {tok} tok in = USD {tok * 0.04 / 1e6:.6f}")
    print("\n=== RESUMEN BATERIA B (moderacion/triage, por criterio) ===")
    for side in ("tev1", "nimble"):
        total_ok = tot = 0
        for r in rowsB:
            for q, s in r.get(side, {}).get("score", {}).items():
                tot += 1
                total_ok += 1 if s["ok"] else 0
        print(f"{side:6s}: {total_ok}/{tot} criterios correctos")


if __name__ == "__main__":
    print("=== BATERIA A: 15 turnos grid ===")
    rowsA = run_battery_A()
    print("\n=== BATERIA B: moderacion/triage ===")
    rowsB = run_battery_B()
    summarize(rowsA, rowsB)