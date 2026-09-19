"""
Genera los workflows de n8n.        python n8n/generar.py

POR QUÉ UN GENERADOR Y NO JSON A MANO
─────────────────────────────────────
El JSON de n8n es posicional, repetitivo y sin esquema: las conexiones se
refieren a los nodos por nombre, las coordenadas se escriben a mano y un
nodo huérfano o un nombre mal escrito no dan error — el workflow importa
bien y simplemente no hace nada. A mano se rompe callado.

Aquí los workflows son código: `wire()` declara el grafo por nombres y el
resto se deriva. Editar un workflow es editar este archivo y re-ejecutarlo.
Si lo editas en la UI de n8n, exporta y vuelca los cambios acá o se
pierden en la próxima corrida.

QUÉ ORQUESTA Y QUÉ NO
─────────────────────
El agente expone tres verbos y n8n decide el camino entre ellos:

    GET  /candidates   qué hay        →  POLÍTICA: qué mirar
    POST /forecast     qué opina      →  POLÍTICA: ¿vale la pena operarlo?
    POST /trade        actúa

Los dos nodos `Política · …` son lo que antes estaba enterrado en Python.
Se editan aquí sin tocar el agente.

Lo que NO es editable: los tres vetos y el risk kernel. Son seguridad, no
política — `/trade` los aplica siempre. Y `/trade` exige un `/forecast`
previo, fresco y no repetido, así que un workflow mal editado no puede
saltarse el análisis ni duplicar una posición. Ver core/api.py.
"""
from __future__ import annotations

import json
import pathlib

OUT = pathlib.Path(__file__).resolve().parent

AG = "{{ $env.AGENT_URL || 'http://agent:10000' }}"
"""
La URL del agente NO va fija. Dentro de Docker los servicios se ven por
nombre (`http://agent:10000`), pero si corres el agente nativo —máquina
sin RAM para Ollama, o depurando— n8n sigue en su contenedor y `agent` no
resuelve a nada. Ahí `AGENT_URL=http://host.docker.internal:10000` y los
cinco workflows se mudan sin tocarlos.
"""

CRED = {"authentication": "genericCredentialType", "genericAuthType": "httpHeaderAuth"}


def node(name, type_, ver, params, x, y, **extra):
    return {"parameters": params, "name": name, "type": f"n8n-nodes-base.{type_}",
            "typeVersion": ver, "position": [x, y], "id": name.lower().replace(" ", "-")[:36],
            **extra}


def http(name, method, path, x, y, timeout=300000, **extra):
    return node(name, "httpRequest", 4.2, {
        "method": method, "url": f"={AG}{path}", **CRED,  # noqa: RUF027 — expresión n8n
        "options": {"timeout": timeout,
                    "response": {"response": {"neverError": True, "fullResponse": True}}},
    }, x, y, **extra)


def code(name, js, x, y, **extra):
    return node(name, "code", 2, {"jsCode": js}, x, y, **extra)


def iff(name, left, op_type, op, right, x, y):
    return node(name, "if", 2.2, {"conditions": {
        "options": {"caseSensitive": True, "version": 2},
        "conditions": [{"leftValue": left, "rightValue": right,
                        "operator": {"type": op_type, "operation": op}}],
        "combinator": "and"}}, x, y)


def sched(name, rule, x, y):
    return node(name, "scheduleTrigger", 1.2, {"rule": {"interval": [rule]}}, x, y)


def telegram(name, text, x, y):
    return node(name, "httpRequest", 4.2, {
        "method": "POST",
        "url": "=https://api.telegram.org/bot{{ $env.TELEGRAM_BOT_TOKEN }}/sendMessage",
        "sendBody": True, "specifyBody": "json",
        "jsonBody": "={{ JSON.stringify({chat_id: $env.TELEGRAM_CHAT_ID, "
                    f"text: {text}, parse_mode: 'HTML'}}) }}}}",
        "options": {"timeout": 20000,
                    "response": {"response": {"neverError": True}}},
    }, x, y)


def wire(*pairs):
    """wire(('A','B'), ('IF','SI',0), ('IF','NO',1)) → dict de conexiones."""
    conns: dict = {}
    for p in pairs:
        src, dst = p[0], p[1]
        out = p[2] if len(p) > 2 else 0
        mains = conns.setdefault(src, {"main": []})["main"]
        while len(mains) <= out:
            mains.append([])
        mains[out].append({"node": dst, "type": "main", "index": 0})
    return conns


def save(fname, name, nodes, conns, tags=("mcp-trade",)):
    wf = {"name": name, "nodes": nodes, "connections": conns,
          "settings": {"executionOrder": "v1", "timezone": "America/Bogota",
                       "errorWorkflow": ""},
          "tags": [{"name": t} for t in tags]}
    p = OUT / fname
    p.write_text(json.dumps(wf, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{fname:26} {len(nodes):2} nodos")


# ═══════════════════════════════════════════════════════════════════════
# 1 — CICLO: un mercado = una ejecución visible, reintentable, ramificable
# ═══════════════════════════════════════════════════════════════════════
RESUMEN_JS = r"""
// Un item por mercado. Distingue tres cosas que antes se confundían:
// lo que el AGENTE descartó, lo que la POLÍTICA descartó, y lo que operó.
const rs = $input.all().map(i => i.json);
const ordenes  = rs.filter(r => r.opero);
const porAgente = rs.filter(r => !r.operar && (r.vetado || !r.ok));
const porPolitica = rs.filter(r => !r.operar && r.ok && !r.vetado);
const rechazadas = rs.filter(r => r.operar && !r.opero);

const razones = {};
for (const r of rs) if (!r.operar) razones[r.razon] = (razones[r.razon] || 0) + 1;

const linea = r => (r.opero ? '🟢 ' : r.vetado ? '🛑 ' : '· ')
  + `<b>${(r.pregunta || r.slug || '').slice(0, 68)}</b>\n`
  + (r.opero
      ? `   ${r.accion} $${r.size_usd} · p=${r.p} vs ${r.precio_mercado} · edge ${r.edge}`
      : `   ${r.razon || r.motivo || ''}`.slice(0, 110));

const destacados = rs.filter(r => r.opero || r.vetado);

return [{ json: {
  vistos: rs.length,
  ordenes: ordenes.length,
  freno_el_agente: porAgente.length,
  freno_la_politica: porPolitica.length,
  freno_el_kernel: rechazadas.length,
  razones,
  costo_usd: +rs.reduce((a, r) => a + (r.costo_usd || 0), 0).toFixed(6),
  hay_algo: destacados.length > 0,
  texto: `<b>Ciclo</b> · ${rs.length} mercados · ${ordenes.length} órdenes\n`
       + `frenó: agente ${porAgente.length} · política ${porPolitica.length} · `
       + `kernel ${rechazadas.length}\n`
       + Object.entries(razones).map(([k, v]) => `${k}: ${v}`).join(' · ')
       + (destacados.length ? '\n\n' + destacados.slice(0, 8).map(linea).join('\n') : ''),
} }];
""".strip()

QUE_MIRAR_JS = r"""
// POLÍTICA: qué mercados vale la pena analizar. Editable sin tocar código.
//
// Categorías válidas (el agente las publica en /candidates):
//   politica · geopolitica · economia · empresas · deportes · cripto · otros
const cs = $json.body.candidatos || [];
const VALIDAS = $json.body.categorias || [];

const SIN_ALPHA = ['cripto'];   // PolyBench: miscalibración severa ahí
const DIAS_MIN  = 2;            // resolver mañana no da tiempo a nada
const TOPE      = 8;            // cuántos analizar por ciclo

// Un nombre mal escrito no da error: simplemente no filtra nada, y te
// quedas creyendo que excluiste algo. Mejor que se note.
const fantasmas = SIN_ALPHA.filter(x => VALIDAS.length && !VALIDAS.includes(x));
if (fantasmas.length) {
  throw new Error(`SIN_ALPHA tiene categorías que no existen: ${fantasmas.join(', ')}. `
                + `Válidas: ${VALIDAS.join(', ')}`);
}

const pasan = cs
  .filter(c => !SIN_ALPHA.includes(c.categoria))
  .filter(c => (c.dias ?? 999) >= DIAS_MIN)
  .sort((a, b) => b.puntaje - a.puntaje)
  .slice(0, TOPE);

return pasan.map(c => ({ json: c }));
""".strip()

OPERAR_JS = r"""
// POLÍTICA: con el pronóstico en la mano, ¿vale la pena operarlo?
// Esto es lo que antes estaba enterrado en Python.
//
// Los tres vetos y el risk kernel NO están aquí: los aplica el agente
// siempre. Son seguridad, no política — apagarlos no debe ser editable.
const f = $json.body ?? $json;

const DIVERGENCIA_MIN = 0.06;   // menos que esto no paga el spread
const SOLO_ESCALADOS  = false;  // true = exige segunda opinión del ensemble

let operar = true, razon = 'pasa';
if (!f.ok)                            { operar = false; razon = f.motivo || f.parado_en || 'análisis falló'; }
else if (f.vetado)                    { operar = false; razon = 'vetado por el agente'; }
else if (f.divergencia == null)        { operar = false; razon = 'sin divergencia'; }
else if (f.divergencia < DIVERGENCIA_MIN) { operar = false; razon = `divergencia ${f.divergencia} < ${DIVERGENCIA_MIN}`; }
else if (SOLO_ESCALADOS && !f.escalado)   { operar = false; razon = 'sin segunda opinión'; }

return [{ json: { ...f, operar, razon } }];
""".strip()

n = [
    sched("Cada 4 horas", {"field": "hours", "hoursInterval": 4}, -860, 300),
    node("Disparo manual", "manualTrigger", 1, {}, -860, 460),
    http("Pedir candidatos", "GET", "/candidates", -640, 380, timeout=120000),
    iff("¿Hay candidatos?", "={{ $json.body.n }}", "number", "gt", 0, -420, 380),
    code("Política · qué mirar", QUE_MIRAR_JS, -200, 300),
    node("Por mercado", "splitInBatches", 3,
         {"batchSize": 1, "options": {"reset": False}}, 20, 300),
    http("Pronosticar", "POST", "/forecast", 240, 440, timeout=900000),
    code("Política · ¿operar?", OPERAR_JS, 460, 440),
    iff("¿Operar?", "={{ $json.operar }}", "boolean", "true", True, 680, 440),
    http("Operar", "POST", "/trade", 900, 360, timeout=120000),
    code("Anotar",
         "// Une la decisión de la política con lo que devolvió /trade.\n"
         "const p = $('Política · ¿operar?').item.json;\n"
         "return [{ json: { ...p, ...($json.body ?? $json) } }];", 1120, 360),
    code("Resumir ciclo", RESUMEN_JS, 240, 120),
    iff("¿Algo que contar?", "={{ $json.hay_algo }}", "boolean", "true", True, 460, 120),
    telegram("Avisar ciclo", "$json.texto", 680, 120),
    code("Sin candidatos", "return [{json:{nota: $json.body.nota || 'sin candidatos'}}];",
         -200, 520),
]
for i in (6, 9):   # Pronosticar y Operar mandan el slug
    n[i]["parameters"] |= {
        "sendBody": True, "specifyBody": "json",
        "jsonBody": "={{ JSON.stringify({ slug: $json.slug }) }}"}
    # Un mercado que revienta no puede tumbar el ciclo.
    n[i] |= {"onError": "continueRegularOutput", "retryOnFail": True,
             "maxTries": 2, "waitBetweenTries": 5000, "alwaysOutputData": True}

save("1-ciclo.json", "1 · Ciclo (el orquestador decide)", n, wire(
    ("Cada 4 horas", "Pedir candidatos"),
    ("Disparo manual", "Pedir candidatos"),
    ("Pedir candidatos", "¿Hay candidatos?"),
    ("¿Hay candidatos?", "Política · qué mirar", 0),
    ("¿Hay candidatos?", "Sin candidatos", 1),
    ("Política · qué mirar", "Por mercado"),
    ("Por mercado", "Resumir ciclo", 0),      # done
    ("Por mercado", "Pronosticar", 1),        # loop
    ("Pronosticar", "Política · ¿operar?"),
    ("Política · ¿operar?", "¿Operar?"),
    ("¿Operar?", "Operar", 0),
    ("¿Operar?", "Por mercado", 1),
    ("Operar", "Anotar"),
    ("Anotar", "Por mercado"),
    ("Resumir ciclo", "¿Algo que contar?"),
    ("¿Algo que contar?", "Avisar ciclo", 0),
))

# ═══════════════════════════════════════════════════════════════════════
# 2 — VIGILANCIA: escalera de alertas con memoria entre ejecuciones
# ═══════════════════════════════════════════════════════════════════════
SALUD_JS = r"""
// Esto es lo que un cron no puede hacer: recordar la ejecución anterior.
// Sin memoria, o avisas en cada parpadeo de red (y dejas de leer las
// alertas) o no avisas nunca. Con memoria avisas UNA vez al tercer fallo
// seguido, y una vez cuando vuelve.
const s = $getWorkflowStaticData('global');
const r = $input.first().json;
const body = r.body || {};
const vivo = r.statusCode === 200 && body.killed !== true;

s.fallos = vivo ? 0 : (s.fallos || 0) + 1;
s.avisado = s.avisado || false;

let avisar = false, texto = '';
const UMBRAL = 3;   // 3 × 10 min = 30 min caído antes de molestar

if (!vivo && s.fallos === UMBRAL) {
  avisar = true; s.avisado = true;
  texto = body.killed
    ? `🛑 <b>KILL SWITCH ACTIVO</b>\n${body.kill_reason || ''}\n\nNo opera hasta que borres <code>state/.KILLED_a</code>.`
    : `🔴 <b>Agente caído</b> — ${UMBRAL} chequeos seguidos sin respuesta (${r.statusCode || 'sin conexión'}).`;
} else if (vivo && s.avisado) {
  avisar = true; s.avisado = false;
  texto = `🟢 <b>Agente de vuelta</b> — uptime ${Math.round((body.uptime_s || 0) / 60)} min.`;
}

return [{ json: { vivo, fallos: s.fallos, avisar, texto,
                  ultimo_ciclo: body.last_cycle || null } }];
""".strip()

n = [
    sched("Cada 10 min", {"field": "minutes", "minutesInterval": 10}, -400, 300),
    http("Consultar salud", "GET", "/health", -180, 300, timeout=20000),
    code("Escalera de alertas", SALUD_JS, 40, 300),
    iff("¿Avisar?", "={{ $json.avisar }}", "boolean", "true", True, 260, 300),
    telegram("Avisar salud", "$json.texto", 480, 300),
]
n[1] |= {"onError": "continueRegularOutput", "alwaysOutputData": True}
save("2-vigilancia.json", "2 · Vigilancia (escalera de alertas)", n, wire(
    ("Cada 10 min", "Consultar salud"),
    ("Consultar salud", "Escalera de alertas"),
    ("Escalera de alertas", "¿Avisar?"),
    ("¿Avisar?", "Avisar salud", 0),
))

# ═══════════════════════════════════════════════════════════════════════
# 3 — APRENDIZAJE: resolver, medir, y decir cuándo APAGAR
# ═══════════════════════════════════════════════════════════════════════
VEREDICTO_JS = r"""
// El criterio para dejarlo prendido NO es el PnL: con 30 apuestas el PnL
// es ruido. Es `skill_vs_market` — ¿pronostica mejor que el precio? Si no,
// no hay negocio por mucho que haya ganado plata.
//
// Las claves son EXACTAMENTE las de learning_report(). Inventarlas hace
// que esto lea undefined y diga "todavía no se sabe" para siempre, callado.
const b = ($input.first().json.body) || {};
const n = b.n_resolved ?? 0;
const bss = b.skill_vs_market ?? null;
const listo = b.ready_to_judge === true;   // el umbral lo pone el agente
const pnl = b.pnl_usd ?? 0;
const tasa = b.beat_market_rate;

let estado, texto;
if (!listo) {
  estado = 'juntando';
  texto = `📊 <b>Todavía no se sabe</b>\n${n} resoluciones.\n`
        + `Hasta las 30 cualquier número es ruido. PnL de paper: $${pnl}.`;
} else if (bss === null) {
  estado = 'sin-dato';
  texto = `⚠️ ${n} resoluciones pero sin skill_vs_market. Revisa /report.`;
} else if (bss <= 0) {
  estado = 'apagar';
  texto = `🛑 <b>APAGAR</b>\nskill vs mercado ${bss.toFixed(3)} con ${n} resoluciones.\n`
        + `El precio pronostica igual o mejor que el agente. No hay edge: `
        + `lo que gane de aquí en adelante es suerte.\nPnL $${pnl}`
        + (tasa != null ? ` · le gana al mercado ${(tasa * 100).toFixed(0)}% de las veces` : '');
} else {
  estado = 'hay-edge';
  texto = `🟢 <b>Hay edge</b>\nskill vs mercado +${bss.toFixed(3)} con ${n} resoluciones.\n`
        + `PnL $${pnl}`
        + (tasa != null ? ` · le gana al mercado ${(tasa * 100).toFixed(0)}% de las veces` : '');
}

const cats = (b.by_category || [])
  .map(c => `| ${c.category || '?'} | ${c.n} | ${Number(c.brier).toFixed(3)} | `
          + `${Number(c.brier_market).toFixed(3)} | `
          + `${c.brier < c.brier_market ? '✅' : '❌'} |`);

const md = [
  '---', 'tipo: reporte', `fecha: ${$now.toFormat('yyyy-MM-dd')}`,
  `estado: ${estado}`, '---', '',
  `# Reporte ${$now.toFormat('yyyy-MM-dd')}`, '',
  texto.replace(/<[^>]+>/g, ''), '',
  ...(cats.length ? ['## Por categoría', '',
                     '| categoría | n | brier | mercado | ¿gana? |',
                     '|---|---|---|---|---|', ...cats, ''] : []),
  '## Crudo', '', '```json', JSON.stringify(b, null, 2), '```', '',
].join('\n');

return [{
  json: { estado, n, bss, pnl, texto, avisar: estado !== 'juntando' },
  binary: { data: { data: Buffer.from(md, 'utf8').toString('base64'),
                    mimeType: 'text/markdown', fileName: 'reporte.md' } },
}];
""".strip()

n = [
    sched("Cada 6 horas", {"field": "hours", "hoursInterval": 6}, -640, 200),
    http("Buscar resueltos", "POST", "/resolve", -420, 200, timeout=600000),
    code("Resumir resoluciones",
         "const b=$input.first().json.body||{};"
         "return [{json:{resueltos:b.resueltos??0, ...b}}];", -200, 200),
    sched("Reporte diario 8am",
          {"field": "days", "triggerAtHour": 8, "triggerAtMinute": 0}, -640, 420),
    http("Pedir reporte", "GET", "/report", -420, 420, timeout=120000),
    code("¿Hay edge o apagar?", VEREDICTO_JS, -200, 420),
    node("Guardar en el vault", "readWriteFile", 1, {
        "operation": "write",
        "fileName": "=/vault/contexto/_reporte-{{ $now.format('yyyy-MM-dd') }}.md",
        "dataPropertyName": "data", "options": {}}, 20, 520),
    iff("¿Vale la pena avisar?", "={{ $json.avisar }}", "boolean", "true", True, 20, 340),
    telegram("Avisar veredicto", "$json.texto", 240, 340),
]
save("3-aprendizaje.json", "3 · Aprendizaje y veredicto", n, wire(
    ("Cada 6 horas", "Buscar resueltos"),
    ("Buscar resueltos", "Resumir resoluciones"),
    ("Reporte diario 8am", "Pedir reporte"),
    ("Pedir reporte", "¿Hay edge o apagar?"),
    ("¿Hay edge o apagar?", "¿Vale la pena avisar?"),
    ("¿Hay edge o apagar?", "Guardar en el vault"),
    ("¿Vale la pena avisar?", "Avisar veredicto", 0),
))

# ═══════════════════════════════════════════════════════════════════════
# 4 — ERRORES: un solo sitio donde cae todo lo que revienta
# ═══════════════════════════════════════════════════════════════════════
ERROR_JS = r"""
// Se dispara cuando CUALQUIER workflow que lo tenga como errorWorkflow
// falla. Sin esto, un workflow roto se queda callado hasta que alguien
// entra a mirar — que es justo lo que no va a pasar.
const e = $input.first().json;
const w = e.workflow || {}, x = e.execution || {};
return [{ json: { texto:
  `💥 <b>Workflow falló</b>\n`
+ `<b>${w.name || '?'}</b>\n`
+ `nodo: <code>${x.lastNodeExecuted || '?'}</code>\n`
+ `${String(x.error?.message || '').slice(0, 300)}\n`
+ (x.url ? `\n${x.url}` : '') } }];
""".strip()

n = [
    node("Cuando algo falla", "errorTrigger", 1, {}, -300, 300),
    code("Formatear error", ERROR_JS, -80, 300),
    telegram("Avisar error", "$json.texto", 140, 300),
]
save("4-errores.json", "4 · Errores (errorWorkflow)", n, wire(
    ("Cuando algo falla", "Formatear error"),
    ("Formatear error", "Avisar error"),
))

# ═══════════════════════════════════════════════════════════════════════
# 5 — MANDO: disparar y parar desde el celular
# ═══════════════════════════════════════════════════════════════════════
n = [
    node("Webhook /mando", "webhook", 2, {
        "path": "mando", "httpMethod": "GET", "responseMode": "lastNode",
        "options": {}}, -420, 300),
    iff("¿Token correcto?",
        "={{ $json.query.token }}", "string", "equals",
        "={{ $env.AGENT_API_TOKEN }}", -200, 300),
    iff("¿Qué pide?", "={{ $json.query.accion }}", "string", "equals",
        "kill", 20, 220),
    http("Parar todo", "POST", "/kill", 240, 120, timeout=30000),
    http("Disparar ciclo", "POST", "/cycle", 240, 320, timeout=1800000),
    code("Rechazado", "return [{json:{error:'token inválido'}}];", 20, 440),
]
n[3]["parameters"] |= {"sendBody": True, "specifyBody": "json",
                       "jsonBody": '={{ JSON.stringify({reason: "parada desde el celular"}) }}'}
save("5-mando.json", "5 · Mando manual (webhook)", n, wire(
    ("Webhook /mando", "¿Token correcto?"),
    ("¿Token correcto?", "¿Qué pide?", 0),
    ("¿Token correcto?", "Rechazado", 1),
    ("¿Qué pide?", "Parar todo", 0),
    ("¿Qué pide?", "Disparar ciclo", 1),
))
