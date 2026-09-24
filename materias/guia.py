"""
La guia como conocimiento del agente, no como reglas que alguien tradujo.

LA DIFERENCIA QUE ESTE ARCHIVO MARCA
────────────────────────────────────
Hasta ahora el documento vivia traducido: yo lo lei y escribi funciones
de Python con la cita al lado. Eso tiene una ventaja real -- es
determinista y se puede hacer backtest, que es lo que exige el capitulo
18.2 -- y una limitacion que no se puede tapar: lo que quedo en el codigo
es MI lectura del documento, congelada. Lo que no entendi o no me parecio
importante no esta.

Aqui el documento entero entra al contexto del agente. Las 56 paginas,
sin resumir, en cada decision. El agente lee la guia y razona sobre ella
delante del mercado, igual que lo haria una persona con el PDF abierto.

POR QUE ENTERO Y NO POR TROZOS
Porque los capitulos se apoyan entre si y trocearlos rompe justo lo que
hace falta. Decidir si una barrida del minimo de Asia es trampa o ruptura
usa el capitulo 6.2 (barrida vs ruptura), el 5.3 (Power of 3), el 10
(killzones), el 15.5 (SMT) y el 2.3 (leer la vela). Un buscador que
devuelva «los tres trozos mas parecidos» le quita al agente justo las
conexiones que son el analisis.

Son 88.896 caracteres, unos 39.000 tokens. Entra de sobra en la ventana
de 1M y con cache de prompt cuesta centimos por llamada, porque el texto
no cambia nunca -- es el mismo documento en todas las decisiones, que es
exactamente el caso que el cache esta hecho para abaratar.

DE DONDE SALE
Se extrae una vez del PDF y queda en `guia.txt`, versionado junto al
codigo. Asi el agente lleva su conocimiento consigo: no depende de que
alguien tenga el PDF en Descargas, ni de una red, ni de un servicio de
embeddings que se pueda caer a mitad de sesion.
"""

from __future__ import annotations

import re
from pathlib import Path

RUTA = Path(__file__).with_name("guia.txt")
PDF = Path(r"C:\Users\Juan\Downloads\Guia_Integral_Trading_Materias_Primas_y_Metales.pdf")

_texto: str | None = None


def extraer(pdf: Path | str = PDF, destino: Path | str = RUTA) -> str:
    """
    Pasa el PDF a texto y lo guarda. Se corre una vez, a mano.

    El extractor mete un salto de linea por palabra, asi que se rearma el
    parrafo: un texto donde cada palabra es una linea gasta el triple de
    tokens y se lee peor, y el agente lo va a leer entero en cada
    decision.
    """
    from pypdf import PdfReader
    r = PdfReader(str(pdf))
    partes = []
    for i, p in enumerate(r.pages, 1):
        t = p.extract_text() or ""
        t = re.sub(r"\s*\n\s*", " ", t)
        t = re.sub(r"[ \t]{2,}", " ", t)
        # Un salto antes de cada numeral de capitulo, para que el agente
        # pueda citar «cap. 11.4» y sepa donde empieza y acaba.
        t = re.sub(r"(?<=[a-z\.\)»])\s(\d{1,2}\.\d{1,2}\s)", r"\n\n\1", t)
        t = re.sub(r"\s*Página\s+\d+\s*$", "", t.strip())
        if t:
            partes.append(f"[pagina {i}]\n{t}")
    texto = ("\n\n".join(partes)).strip()
    Path(destino).write_text(texto, encoding="utf-8")
    return texto


def texto() -> str:
    """La guia entera, en memoria. Se lee del disco una sola vez."""
    global _texto
    if _texto is None:
        if not RUTA.exists():
            raise FileNotFoundError(
                f"falta {RUTA.name}. Corre: python -m materias.guia")
        _texto = RUTA.read_text(encoding="utf-8")
    return _texto


def capitulos() -> dict[str, str]:
    """
    El indice, para poder comprobar que no falta nada.

    No se usa para trocear el documento -- el agente lo recibe entero --
    sino para verificar que la extraccion no se comio un capitulo, que es
    el fallo silencioso tipico de sacar texto de un PDF.
    """
    # Se busca en todo el texto, no solo a principio de linea. El salto
    # que separa capitulos lo pone la extraccion, y basta que el PDF meta
    # un caracter raro antes del numeral para que un capitulo real no
    # case. Paso con el 6.2, que SI estaba extraido: fallaba la
    # comprobacion, no la extraccion. Una verificacion que da falsas
    # alarmas deja de mirarse a la tercera vez.
    out: dict[str, str] = {}
    patron = r"(?:^|\s)(\d{1,2}\.\d{1,2})\s+([A-ZÁÉÍÓÚÑ][^\n]{3,88})"
    for m in re.finditer(patron, texto(), re.M):
        out.setdefault(m.group(1), m.group(2).strip())
    return out


def estado() -> dict:
    t = texto()
    return {"caracteres": len(t), "tokens_aprox": round(len(t) / 2.3),
            "capitulos": len(capitulos()), "ruta": str(RUTA)}


if __name__ == "__main__":
    if not RUTA.exists() or PDF.exists():
        print(f"extrayendo de {PDF.name}...")
        extraer()
    e = estado()
    print(f"  {e['caracteres']:,} caracteres  ~{e['tokens_aprox']:,} tokens")
    print(f"  {e['capitulos']} subcapitulos detectados")
    print(f"  guardado en {e['ruta']}")
    faltan = [c for c in ("2.3", "4.3", "5.3", "6.2", "7.1", "8.1", "9.3",
                          "10.2", "11.9", "13.2", "14.1", "15.5", "16.3",
                          "18.7", "19.1", "20.2")
              if c not in capitulos()]
    print("  faltan capitulos clave:", faltan or "ninguno")
