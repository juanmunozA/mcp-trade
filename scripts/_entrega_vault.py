"""
El vault de Obsidian que viaja en la entrega.

Aparte del que siembra `pilot_a_llm/vault.py`, y por una razón concreta:
el del repo describe el montaje de nube (Gemini investigando, llama y
DeepSeek en Cloudflare). La entrega corre Ollama y SearXNG. Quien la
reciba leería una descripción de algo que no tiene.

`Vault._seed_flow` y `_seed_decisions` saltan lo que ya existe, así que
estas notas ganan y el agente no las pisa.

Se abre `./vault` en Obsidian y `Ctrl+G` para el grafo.
"""

from __future__ import annotations

import json
from pathlib import Path

COLORES = {
    "arranque": 0x5BA8A0,
    "flujo": 0xD97757,
    "orden": 0x6FA37D,
    "veto": 0xC8615B,
    "servicios": 0x7C8AA0,
    "orquestacion": 0xC8A85B,
    "contexto": 0x8B6FA8,
}

# (slug, título, cuerpo, enlaces sueltos además de la cadena)
ARRANQUE = [
    ("1-requisitos", "Qué necesita la máquina",
     "**Docker** es lo único obligatorio.\n\n"
     "**RAM libre** — libre, no total. El modelo tiene que caber entero:\n\n"
     "| RAM libre | modelo |\n|---|---|\n"
     "| 4 GB | `deepseek-r1:1.5b` — solo para ver que arranca |\n"
     "| 16 GB | `deepseek-r1:8b` — mínimo razonable |\n"
     "| 32 GB | `deepseek-r1:14b` — recomendado |\n"
     "| 64 GB | `deepseek-r1:32b` — el mejor que cabe |\n\n"
     "**Disco:** ~25 GB.\n\n"
     "GPU NVIDIA de 8 GB o más: opcional, pasa de minutos a segundos. Las "
     "integradas de Intel y AMD no sirven — Ollama no las usa.",
     ["ollama"]),
    ("2-configurar", "Configurar el .env",
     "`cp .env.example .env` y tres cosas:\n\n"
     "- `OLLAMA_MODEL` — el que quepa en tu RAM\n"
     "- `AGENT_API_TOKEN` — algo largo y aleatorio\n"
     "- `SEARXNG_SECRET` — otro distinto\n\n"
     "El token protege la API: sin él, cualquiera en tu red puede disparar "
     "ciclos o parar el agente.\n\n"
     "Opcional pero recomendado: `GROQ_API_KEY`. Ver [[3-ensemble]].",
     ["3-ensemble"]),
    ("3-arrancar", "Arrancar",
     "```bash\n./arrancar.sh       # Linux / Mac\n.\\arrancar.ps1      # Windows\n```\n\n"
     "Es una compuerta, no un atajo. Comprueba Docker, **la RAM contra el "
     "modelo elegido**, disco y `.env` antes de levantar nada. Si elegiste un "
     "modelo que no cabe te lo dice ahí, en vez de dejarlo morir por falta de "
     "memoria a los veinte minutos.\n\n"
     "Después levanta los cinco servicios, espera la descarga del modelo "
     "(5-20 GB, puede tardar una hora) y prepara la base.",
     ["agent", "postgres"]),
    ("4-verificar", "Verificar que quedó bien",
     "```bash\ndocker compose logs -f agent\n```\n\n"
     "Cada 4 horas elige mercados y pronostica:\n\n"
     "```\nORDEN YES ganara-x | p=0.720 vs 0.550 | edge +0.170 | $5.00\n"
     "VETO  pasara-y — desacuerdo del ensemble 0.31\n"
     "INVESTIGAR cierre-frontera — evidencia insuficiente\n```\n\n"
     "`VETO` e `INVESTIGAR` **no son fallos**: es el agente decidiendo no "
     "apostar. La mayoría de los mercados terminan así, y está bien.\n\n"
     "Salud: `curl http://localhost:10000/health`",
     ["1-seleccion", "4-vetos"]),
    ("5-que-esperar", "Qué esperar",
     "**Las primeras semanas no vas a saber si funciona.** Los mercados "
     "resuelven en días o semanas y hacen falta ~30 resoluciones para que el "
     "número que mide si sirve signifique algo.\n\n"
     "```\nsemana 1-2   pronostica; nada ha resuelto\n"
     "semana 3-4   primeras resoluciones\n"
     "semana 5     30 resoluciones → la calibración se activa sola\n"
     "semana 6     el reporte dice si hay ventaja o hay que apagarlo\n```\n\n"
     "**El criterio no es cuánta plata ganó.** Con 30 apuestas la plata es "
     "ruido: puede ir ganando sin tener ninguna ventaja real. El criterio es "
     "si le gana al precio pronosticando — ver [[6-aprendizaje]].",
     ["6-aprendizaje", "3-aprendizaje"]),
]

FLUJO = [
    ("1-seleccion", "Selección de mercados",
     "De ~800 activos a los 8 del ciclo. **Sin IA y sin costo**: precio entre "
     "0.12 y 0.88, liquidez media, resuelve en 2-150 días.\n\n"
     "La liquidez puntúa como una campana con pico en ~$80k, no como una "
     "rampa. Los mercados gigantes ya los miraron todos: ahí no hay ventaja.\n\n"
     "`GET /candidates` hace solo esto.",
     ["agent"]),
    ("2-investigacion", "Investigación",
     "**SearXNG** busca, el modelo local destila. Dos consultas por mercado, "
     "se extrae el texto de las páginas y sale una lista de hechos con fecha "
     "y fuente.\n\n"
     "Ollama no navega: sin este paso el agente pronosticaría eventos actuales "
     "con lo que recuerda del entrenamiento, que para esto es inútil.\n\n"
     "Tiene prohibido opinar sobre probabilidades: si sugiere un número, el "
     "ensemble se ancla y deja de ser independiente.",
     ["searxng"]),
    ("3-ensemble", "Ensemble en cascada",
     "**DeepSeek local** pronostica todos los mercados del ciclo. Los que "
     "marca como interesantes escalan a una **segunda opinión**.\n\n"
     "Si pusiste `GROQ_API_KEY`, esa segunda opinión es Llama en Groq. "
     "Importa más de lo que parece: con un solo modelo se pierde el veto por "
     "desacuerdo, y es el que frena las apuestas donde dos modelos no "
     "coinciden. Sirve porque es **otra familia** — sus errores no están "
     "correlacionados con los de DeepSeek, que es justo lo que hace "
     "informativo el desacuerdo.\n\n"
     "Sin Groq corre igual, con un modelo y sin ese veto.",
     ["ollama"]),
    ("4-vetos", "Los tres vetos",
     "Cualquiera fuerza PASS, por atractivo que parezca el edge. Aquí se "
     "descarta la mayoría de lo analizado, y eso es lo correcto.\n\n"
     "- [[veto-ambiguedad]] — el modelo vio dos lecturas del reglamento\n"
     "- [[veto-desacuerdo]] — los modelos no convergieron\n"
     "- [[veto-divergencia]] — discrepó del mercado más de 35 puntos\n"
     "- [[veto-evidencia]] — no había hechos con qué pronosticar",
     []),
    ("5-decision", "Decisión y riesgo",
     "Calibración isotónica → Kelly fraccional sobre el saldo real → **risk "
     "kernel**.\n\n"
     "Nada de esto usa IA: el modelo aporta un número y la aritmética decide "
     "qué hacer con él.\n\n"
     "Los límites viven en variables de entorno a propósito — el modelo nunca "
     "los ve. Si estuvieran donde él puede leerlos, empezaría a razonar sobre "
     "cómo llegar hasta ellos.\n\n"
     "El kernel tiene la última palabra y no negocia con la oportunidad.\n\n"
     "Sale una de tres: [[orden-yes]], [[orden-no]] o [[pass-sin-edge]].",
     []),
    ("6-aprendizaje", "Bucle de aprendizaje",
     "Cada 6 h busca mercados resueltos. Brier propio contra Brier del "
     "mercado.\n\n"
     "Es lo único que convierte esto en algo que aprende — y lo único que "
     "puede decirte que **no sirve**:\n\n"
     "```bash\ncurl http://localhost:10000/report -H \"Authorization: Bearer <token>\"\n```\n\n"
     "`skill_vs_market > 0` → pronostica mejor que el mercado.\n"
     "`skill_vs_market <= 0` → **apágalo**, por bien que se vea el PnL.\n\n"
     "A las 30 resoluciones la calibración se activa sola.",
     ["postgres", "3-aprendizaje"]),
]

DECISIONES = {
    "orden-yes": ("Órdenes YES", "5-decision",
                  "El agente apostó a que el evento SÍ ocurre."),
    "orden-no": ("Órdenes NO", "5-decision",
                 "El agente apostó a que NO ocurre."),
    "pass-sin-edge": ("Pass · sin edge suficiente", "5-decision",
                      "El desajuste no supera el umbral. Coincidir con el "
                      "mercado es lo normal y lo correcto: un agente que "
                      "encuentra oportunidad en todo no está evaluando."),
    "veto-divergencia": ("Veto · divergencia extrema", "4-vetos",
                         "Discrepó del mercado más de 35 puntos. Un edge real "
                         "son 5-15; si sale 70, lo probable no es haber "
                         "encontrado oro sino haber entendido otra pregunta."),
    "veto-desacuerdo": ("Veto · desacuerdo del ensemble", "4-vetos",
                        "Los modelos no convergieron. La pregunta es ambigua "
                        "aunque ninguno lo haya declarado. Este veto solo "
                        "existe si hay un segundo modelo — ver [[3-ensemble]]."),
    "veto-ambiguedad": ("Veto · ambigüedad declarada", "4-vetos",
                        "El modelo leyó el reglamento, vio dos lecturas "
                        "posibles y se detuvo solo."),
    "veto-evidencia": ("Veto · evidencia insuficiente", "2-investigacion",
                       "Sin hechos el modelo rellena con su prior, y ahí "
                       "aparecen los pronósticos sobreconfiados."),
}

SERVICIOS = [
    ("agent", "agent · el agente", "http://localhost:10000/health",
     "La lógica. Expone los verbos que n8n orquesta:\n\n"
     "| | |\n|---|---|\n"
     "| `GET /health` | estado y kill switch |\n"
     "| `GET /report` | ¿hay ventaja o hay que apagarlo? |\n"
     "| `GET /candidates` | el explorador, sin IA |\n"
     "| `POST /market` | un mercado por el pipeline |\n"
     "| `POST /cycle` | el ciclo completo |\n"
     "| `POST /resolve` | busca resueltos y aprende |\n"
     "| `POST /kill` | botón de pánico |\n",
     ["1-seleccion", "5-decision"]),
    ("ollama", "ollama · los modelos", "http://localhost:11434",
     "Corre DeepSeek en tu máquina. Sin cuota de tokens ni clave que rotar.\n\n"
     "**El límite existe igual: es el tiempo de CPU.** DeepSeek razona mucho "
     "antes de responder y eso se paga en segundos. Ilimitado en cuota, no en "
     "reloj.\n\n"
     "El modelo queda residente (`OLLAMA_KEEP_ALIVE=-1`): sin eso se descarga "
     "a los 5 minutos y la siguiente llamada vuelve a pagar la carga entera, "
     "que en CPU son minutos.",
     ["3-ensemble"]),
    ("searxng", "searxng · búsqueda", "http://localhost:8888",
     "Metabuscador propio. Ollama no navega; sin esto el agente pronostica a "
     "ciegas.\n\n"
     "Si devuelve 403 le falta `formats: [json]` en `searxng/settings.yml`. Es "
     "el fallo más común al montarlo para uso programático.",
     ["2-investigacion"]),
    ("postgres", "postgres · la base", "localhost:5432",
     "Pronósticos, resoluciones, calibración.\n\n"
     "**Puede no existir.** `DB_MODE` en el `.env` decide:\n\n"
     "| | |\n|---|---|\n"
     "| `local` | un contenedor más. Autocontenido, nada sale. |\n"
     "| `neon` | postgres gestionado. **Un contenedor menos, ~250 MB de RAM** |\n\n"
     "Con Ollama comiéndose casi toda la memoria, esos 250 MB y la carpeta "
     "`pgdata/` —que crece sin parar— pueden decidir si el modelo cabe o se "
     "va a swap.\n\n"
     "A cambio, cada consulta sale a internet y si Neon no responde el ciclo "
     "falla. Da igual aquí: son unas pocas escrituras por mercado.\n\n"
     "Con Neon hay que usar el endpoint **DIRECTO**, no el `-pooler`: "
     "PgBouncer baraja conexiones y el driver revienta con *prepared "
     "statement already exists*. El arrancador avisa si te equivocas.",
     ["6-aprendizaje", "2-configurar"]),
    ("n8n", "n8n · orquestación", "http://localhost:5678",
     "Decide **cuándo** pasa cada cosa. El agente decide **qué** pasa.\n\n"
     "Es opcional: si no lo tocas el agente corre igual, tiene sus propios "
     "bucles. Lo que te da es verlo y meterle mano.\n\n"
     "Al importar, dos cosas a mano:\n\n"
     "1. *Credentials → New → Header Auth*. Name `Authorization`, Value "
     "`Bearer <tu AGENT_API_TOKEN>`.\n"
     "2. En cada workflow: *Settings → Error workflow → `4 · Errores`*. Sin "
     "eso [[4-errores]] nunca dispara.",
     ["1-ciclo", "2-vigilancia", "3-aprendizaje", "4-errores", "5-mando"]),
]

ORQUESTACION = [
    ("1-ciclo", "1 · Ciclo de pronóstico", "cada 4 horas",
     "**Un mercado = una ejecución.** Pide `/candidates` y reparte: cada "
     "mercado es su propia fila que puedes ver, reintentar sola y ramificar "
     "según el resultado.\n\n"
     "Antes esto era un `POST /cycle` de diez minutos a ciegas: si un mercado "
     "moría, eso solo existía en los logs.",
     ["1-seleccion", "agent"]),
    ("2-vigilancia", "2 · Vigilancia", "cada 10 min",
     "Consulta `/health` y **recuerda la ejecución anterior**.\n\n"
     "Avisa al **tercer** fallo seguido, una sola vez, y otra al recuperarse. "
     "Sin memoria solo hay dos opciones malas: avisar en cada parpadeo de red "
     "hasta que dejas de leer las alertas, o no avisar nunca.",
     ["agent"]),
    ("3-aprendizaje", "3 · Aprendizaje y veredicto", "cada 6 h + diario",
     "Registra resultados y dicta el veredicto.\n\n"
     "Dice **APAGAR** cuando `skill_vs_market <= 0` aunque el PnL esté en "
     "verde, porque con 30 apuestas el PnL es ruido.\n\n"
     "Escribe el reporte a `vault/contexto/`.",
     ["6-aprendizaje"]),
    ("4-errores", "4 · Errores", "cuando algo revienta",
     "Atrapa cualquier workflow que falle y avisa con el nodo y el mensaje.\n\n"
     "Hay que asignarlo a mano en *Settings → Error workflow* de cada uno. Sin "
     "ese paso nunca dispara y un workflow roto se queda callado.",
     ["n8n"]),
    ("5-mando", "5 · Mando manual", "webhook",
     "Disparar un ciclo o parar todo desde el celular, sin entrar a la "
     "máquina. Pide el token en el query.",
     ["agent"]),
]

INICIO = """---
tags: [inicio]
---

# Agente de pronóstico

Lee mercados de eventos de Polymarket, busca información, estima una
probabilidad y apuesta **solo cuando difiere del precio lo suficiente para
pagar el spread**. Arranca simulando.

---

## Qué le hace a cada mercado

Esto es el flujo. Todo lo demás son detalles de esto.

```mermaid
flowchart TD
    P["Polymarket<br/>~800 mercados activos"] --> S["1 · SELECCIÓN<br/>filtro + puntaje · sin IA"]
    S -->|"8 candidatos"| I["2 · INVESTIGACIÓN<br/>SearXNG busca, el modelo destila"]

    I -->|"sin hechos"| VE(["PASS · veto evidencia"])
    I --> E["3 · ENSEMBLE<br/>DeepSeek pronostica"]
    E -->|"parece interesante"| G["segunda opinión<br/>Groq"]
    G --> V
    E --> V{"4 · LOS TRES VETOS"}

    V -->|"pregunta ambigua"| VA(["PASS · veto ambigüedad"])
    V -->|"los modelos no coinciden"| VD(["PASS · veto desacuerdo"])
    V -->|"discrepa +35 puntos"| VX(["PASS · veto divergencia"])

    V -->|"pasa"| Q["COTIZAR<br/>book real · se paga el ask"]
    Q --> D["5 · DECISIÓN<br/>Kelly fraccional × 0.20"]
    D -->|"edge insuficiente"| PS(["PASS · sin edge"])
    D --> K{"RISK KERNEL<br/>límites duros"}
    K -->|"rechaza"| PS
    K -->|"aprueba"| O(["ORDEN"])

    O --> R["6 · APRENDIZAJE<br/>al resolver: Brier propio vs del mercado"]
    R -.->|"corrige el sesgo"| E

    style O fill:#6FA37D,color:#fff
    style VE fill:#C8615B,color:#fff
    style VA fill:#C8615B,color:#fff
    style VD fill:#C8615B,color:#fff
    style VX fill:#C8615B,color:#fff
    style PS fill:#8a8a8a,color:#fff
    style K fill:#3d3d3d,color:#fff
```

**La mayoría de los mercados terminan en PASS, y eso es correcto.** Un
agente que encuentra oportunidad en todo no está evaluando.

Detalle de cada paso: [[1-seleccion]] · [[2-investigacion]] ·
[[3-ensemble]] · [[4-vetos]] · [[5-decision]] · [[6-aprendizaje]]

---

## Cómo se monta

```mermaid
flowchart LR
    Z["descomprimir"] --> C["editar .env<br/>modelo + 2 claves"]
    C --> A["./arrancar.sh"]
    A --> Q{"¿la máquina da?<br/>docker · RAM · disco"}
    Q -->|"no"| X(["te dice qué falta<br/>y no arranca"])
    Q -->|"sí"| U["docker compose up"]
    U --> M["baja el modelo<br/>5-20 GB · tarda"]
    M --> B["prepara la base"]
    B --> OK(["corriendo"])

    style OK fill:#6FA37D,color:#fff
    style X fill:#C8615B,color:#fff
```

[[1-requisitos]] → [[2-configurar]] → [[3-arrancar]] → [[4-verificar]] →
[[5-que-esperar]]

---

## Quién habla con quién

```mermaid
flowchart LR
    subgraph TU["tu máquina"]
        N["n8n<br/>:5678"] -->|"cada 4 h"| A["agent<br/>:10000"]
        A --> O["ollama<br/>:11434"]
        A --> S["searxng<br/>:8888"]
        A -.->|"DB_MODE=local"| P["postgres<br/>:5432"]
    end
    A -->|"mercados y órdenes"| PM[("Polymarket")]
    S -->|"consultas"| W[("buscadores")]
    A -.->|"DB_MODE=neon"| NE[("Neon<br/>postgres gestionado")]

    style TU fill:#1e1e1e,stroke:#555
```

Los modelos y el contexto **nunca** salen de tu máquina. Sale Polymarket
(es el mercado, inevitable) y las consultas de búsqueda.

La base es la única pieza que elegís: en un contenedor al lado
(`DB_MODE=local`) o en Neon (`DB_MODE=neon`), que quita ~250 MB de RAM.
Ver [[postgres]].

[[agent]] · [[ollama]] · [[searxng]] · [[postgres]] · [[n8n]]

---

## Lo que escribes tú

Lo de `contexto/` entra al prompt del agente en su próximo ciclo. Ahí se le
dan lecciones. Lee [[_LEEME]] antes de escribir, y [[reglas-de-resolucion]]
como ejemplo de nota que sí sirve.

Sus pronósticos van a `3-mercados/` a medida que corre — ahora está vacío.

---

## Sobre el grafo (`Ctrl+G`)

Sirve para **navegar**, no para entender el flujo — un grafo de fuerzas no
tiene noción de orden y apelotona todo. Para el orden están los diagramas
de arriba.

Viene filtrado con `-path:3-mercados`: si no, los cientos de notas de
mercados tapan la estructura. Borra el filtro cuando quieras ver de qué
mercados salió cada decisión.
"""


def _nota(titulo: str, tags: str, cuerpo: str, pie: str = "") -> str:
    return f"---\ntags: [{tags}]\n---\n\n# {titulo}\n\n{cuerpo}\n" + (
        f"\n---\n{pie}\n" if pie else ""
    )


def _cadena(i: int, items: list) -> str:
    """Anterior y siguiente: es lo que dibuja la espina del grafo."""
    ant = items[i - 1][0] if i > 0 else items[-1][0]
    sig = items[i + 1][0] if i + 1 < len(items) else items[0][0]
    return f"← [[{ant}]] · [[{sig}]] →"


def construir(raiz: Path) -> int:
    """Escribe el vault completo. Devuelve cuántas notas."""
    carpetas = {
        "arranque": raiz / "0-arranque",
        "flujo": raiz / "1-flujo",
        "decisiones": raiz / "2-decisiones",
        "servicios": raiz / "6-servicios",
        "orquestacion": raiz / "7-orquestacion",
    }
    for d in carpetas.values():
        d.mkdir(parents=True, exist_ok=True)

    n = 0
    (raiz / "_INICIO.md").write_text(INICIO, encoding="utf-8")
    n += 1

    for i, (slug, titulo, cuerpo, extra) in enumerate(ARRANQUE):
        pie = _cadena(i, ARRANQUE) + (
            " · " + " · ".join(f"[[{e}]]" for e in extra) if extra else ""
        )
        (carpetas["arranque"] / f"{slug}.md").write_text(
            _nota(f"{i + 1}. {titulo}", f"arranque, orden-{i + 1}", cuerpo, pie),
            encoding="utf-8")
        n += 1

    for i, (slug, titulo, cuerpo, extra) in enumerate(FLUJO):
        pie = _cadena(i, FLUJO) + (
            " · " + " · ".join(f"[[{e}]]" for e in extra) if extra else ""
        )
        (carpetas["flujo"] / f"{slug}.md").write_text(
            _nota(f"{i + 1}. {titulo}", "flujo", cuerpo, pie), encoding="utf-8")
        n += 1

    for slug, (titulo, paso, cuerpo) in DECISIONES.items():
        tipo = "orden" if slug.startswith("orden") else "veto"
        (carpetas["decisiones"] / f"{slug}.md").write_text(
            _nota(titulo, f"decision, {tipo}", cuerpo, f"[[{paso}]]"),
            encoding="utf-8")
        n += 1

    for slug, titulo, donde, cuerpo, extra in SERVICIOS:
        pie = " · ".join(f"[[{e}]]" for e in extra)
        (carpetas["servicios"] / f"{slug}.md").write_text(
            _nota(titulo, "servicio", f"`{donde}`\n\n{cuerpo}", pie),
            encoding="utf-8")
        n += 1

    for slug, titulo, cuando, cuerpo, extra in ORQUESTACION:
        pie = " · ".join(f"[[{e}]]" for e in extra)
        (carpetas["orquestacion"] / f"{slug}.md").write_text(
            _nota(titulo, "orquestacion", f"*{cuando}*\n\n{cuerpo}", pie),
            encoding="utf-8")
        n += 1

    _graph_config(raiz)
    return n


def _graph_config(raiz: Path) -> None:
    cfg = raiz / ".obsidian"
    cfg.mkdir(exist_ok=True)

    def grupo(query: str, color: int) -> dict:
        return {"query": query, "color": {"a": 1, "rgb": color}}

    (cfg / "graph.json").write_text(json.dumps({
        "collapse-filter": False,
        # Sin esto, los cientos de notas de mercados que el agente va
        # escribiendo tapan la estructura y el grafo es una bola ilegible.
        # Se quita desde la UI cuando se quiera ver el detalle.
        "search": "-path:3-mercados",
        "showTags": False,
        "showAttachments": False,
        "hideUnresolved": True,
        "showOrphans": False,
        "collapse-color-groups": False,
        "colorGroups": [
            grupo("path:0-arranque", COLORES["arranque"]),
            grupo("path:1-flujo", COLORES["flujo"]),
            grupo("tag:#orden path:2-decisiones", COLORES["orden"]),
            grupo("tag:#veto path:2-decisiones", COLORES["veto"]),
            grupo("path:6-servicios", COLORES["servicios"]),
            grupo("path:7-orquestacion", COLORES["orquestacion"]),
            grupo("path:contexto", COLORES["contexto"]),
        ],
        "collapse-display": False,
        "showArrow": True,
        "textFadeMultiplier": -1.2,
        "nodeSizeMultiplier": 1.15,
        "lineSizeMultiplier": 0.55,
        "collapse-forces": False,
        # Repulsión alta y enlaces largos: separa los racimos en vez de dejar
        # una bola apretada donde no se lee nada.
        "centerStrength": 0.28,
        "repelStrength": 16,
        "linkStrength": 0.55,
        "linkDistance": 260,
        "scale": 0.42,
        "close": True,
    }, indent=2), encoding="utf-8")

    (cfg / "appearance.json").write_text(
        json.dumps({"theme": "obsidian", "accentColor": "#D97757"}, indent=2),
        encoding="utf-8")

    (cfg / "app.json").write_text(
        json.dumps({"attachmentFolderPath": "adjuntos",
                    "alwaysUpdateLinks": True}, indent=2),
        encoding="utf-8")

    # Abre en el grafo con _INICIO al lado. Sin esto, la primera vez se ve
    # un panel vacío y hay que saber que el grafo es Ctrl+G.
    (cfg / "workspace.json").write_text(json.dumps({
        "main": {
            "id": "raiz", "type": "split", "direction": "vertical",
            "children": [{
                "id": "tabs", "type": "tabs",
                "children": [
                    {"id": "hoja-grafo", "type": "leaf",
                     "state": {"type": "graph", "state": {}}},
                    {"id": "hoja-inicio", "type": "leaf",
                     "state": {"type": "markdown",
                               "state": {"file": "_INICIO.md", "mode": "preview"}}},
                ],
                "currentTab": 0,
            }],
        },
        "left": {
            "id": "izq", "type": "split", "direction": "horizontal", "width": 260,
            "children": [{
                "id": "izq-tabs", "type": "tabs",
                "children": [{"id": "hoja-archivos", "type": "leaf",
                              "state": {"type": "file-explorer", "state": {}}}],
            }],
        },
        "right": {"id": "der", "type": "split", "direction": "horizontal",
                  "width": 300, "collapsed": True, "children": []},
        "active": "hoja-grafo",
        "lastOpenFiles": ["_INICIO.md"],
    }, indent=2), encoding="utf-8")
