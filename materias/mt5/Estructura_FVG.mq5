//+------------------------------------------------------------------+
//| Estructura, vacios y order blocks: la misma logica que            |
//| materias/red/zonas.py, dibujando SOLO lo que sirve cerca del      |
//| precio para que se lea de un vistazo.                             |
//|  - maximos/minimos de SERIE (zigzag de 3 ATR): HH / LH / HL / LL  |
//|  - vacios (FVG) de 3 velas y order blocks abiertos: los mas       |
//|    cercanos por encima y por debajo                               |
//| Medido en XAUUSD 15M desde 2022 (cierres contra el azar):         |
//|  FVG, primer toque en el borde:  reacciona 54 % (azar 38)         |
//|  OB,  primer toque en el borde:  reacciona 56 % (azar 47)         |
//|  si el primer toque llega a la mitad, lo normal es que se llene   |
//|  (27 % y 32 %): por eso esas zonas no se dibujan por defecto.     |
//|  extremos de serie: se rompen 74 %; la ruptura sigue 47 %         |
//+------------------------------------------------------------------+
#property copyright   "MCP Trade"
#property version     "2.00"
#property description "Extremos de serie, vacios (FVG) y order blocks cercanos, con su estado medido"
#property indicator_chart_window
#property indicator_buffers 2
#property indicator_plots   1

#property indicator_label1  "Serie"
#property indicator_type1   DRAW_SECTION
#property indicator_color1  clrDodgerBlue
#property indicator_width1  1

input int    Velas           = 600;   // Velas hacia atras que se analizan
input int    ZonasPorLado    = 2;     // Zonas mas cercanas por encima y por debajo del precio
input double GiroSerieATR    = 3.0;   // Giro en ATR que cierra un tramo (maximo/minimo de serie)
input double MinVacioATR     = 0.2;   // Vacios mas chicos que esto (en ATR) se ignoran
input bool   MostrarFVG      = true;  // Vacios
input bool   MostrarOB       = true;  // Order blocks
input bool   MostrarIFVG     = false; // Vacios rotos que pasan a inversos
input bool   MostrarMitad    = false; // Zonas cuyo primer toque llego a la mitad (suelen llenarse)
input bool   MostrarSerie    = true;  // HH / LH / HL / LL y la linea del zigzag
input color  ColorVacio      = C'232,232,232'; // Relleno de los vacios (fondo claro)
input color  ColorOB         = C'253,226,226'; // Relleno de los order blocks (fondo claro)
input bool   MostrarH1       = true;  // Traer tambien los vacios y order blocks de H1 vigentes
input int    ZonasH1PorLado  = 2;     // Zonas de H1 por encima y por debajo del precio
input int    VelasH1         = 500;   // Velas de H1 hacia atras (~3 semanas)
input color  ColorVacioH1    = C'212,212,212'; // Relleno de los vacios de H1 (fondo claro)
input color  ColorOBH1       = C'247,196,196'; // Relleno de los order blocks de H1 (fondo claro)

#define P "EZ_"

double Zig[], Atr[];
datetime ultimaVela = 0;
uint ultimoDibujo = 0;
int enCurso = -1;   // indice de la vela que se esta formando
bool claro = false;
int marcoActual = 0;   // 0 el del grafico, 1 H1

struct Zona
{
   int      tipo;     // 0 FVG, 1 OB, 2 IFVG
   int      lado;     // +1 alcista, -1 bajista
   double   bajo, alto;
   datetime t0;
   int      estado;   // 0 sin tocar, 1 defendido en el borde, 2 llego a la mitad, 3 tocando ahora
   bool     conFVG;   // OB cuyo impulso dejo un vacio
   int      marco;    // 0 el del grafico, 1 H1
};
Zona zonas[];

int OnInit()
{
   SetIndexBuffer(0, Zig, INDICATOR_DATA);
   SetIndexBuffer(1, Atr, INDICATOR_CALCULATIONS);
   PlotIndexSetDouble(0, PLOT_EMPTY_VALUE, EMPTY_VALUE);
   if(!MostrarSerie)
      PlotIndexSetInteger(0, PLOT_DRAW_TYPE, DRAW_NONE);
   IndicatorSetString(INDICATOR_SHORTNAME, "Estructura, vacios y order blocks");
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason) { ObjectsDeleteAll(0, P); }

bool FondoClaro()
{
   color bg = (color)ChartGetInteger(0, CHART_COLOR_BACKGROUND);
   int r = bg & 0xFF, g = (bg >> 8) & 0xFF, b = (bg >> 16) & 0xFF;
   return (r + g + b) / 3 > 128;
}

// Paleta: vacios gris claro y order blocks rojo muy suave; en fondo negro, sus equivalentes oscuros
color Relleno(const int tipo, const int marco)
{
   if(marco == 1) return tipo == 1 ? (claro ? ColorOBH1 : C'95,35,35') : (claro ? ColorVacioH1 : C'65,65,72');
   return tipo == 1 ? (claro ? ColorOB : C'70,30,30') : (claro ? ColorVacio : C'50,50,56');
}
color Borde(const int tipo, const int lado)
{
   if(tipo == 1) return lado > 0 ? clrDodgerBlue : clrDarkOrange;
   if(tipo == 2) return clrSlateGray;
   return claro ? (lado > 0 ? C'30,140,60' : C'200,40,40') : (lado > 0 ? clrLime : clrRed);
}
color Tinta() { return claro ? C'40,40,40' : C'220,220,220'; }

void Texto(const string nombre, const datetime t, const double precio, const string txt,
           const color c, const int tam, const ENUM_ANCHOR_POINT anc, const bool negrita = true)
{
   ObjectCreate(0, nombre, OBJ_TEXT, 0, t, precio);
   ObjectSetString(0, nombre, OBJPROP_TEXT, txt);
   ObjectSetString(0, nombre, OBJPROP_FONT, negrita ? "Arial Bold" : "Arial");
   ObjectSetInteger(0, nombre, OBJPROP_FONTSIZE, tam);
   ObjectSetInteger(0, nombre, OBJPROP_COLOR, c);
   ObjectSetInteger(0, nombre, OBJPROP_ANCHOR, anc);
   ObjectSetInteger(0, nombre, OBJPROP_SELECTABLE, false);
   ObjectSetInteger(0, nombre, OBJPROP_HIDDEN, true);
}

void Caja(const string nombre, const datetime t1, const double p1, const datetime t2, const double p2,
          const color c, const bool relleno, const ENUM_LINE_STYLE estilo, const int ancho)
{
   ObjectCreate(0, nombre, OBJ_RECTANGLE, 0, t1, p1, t2, p2);
   ObjectSetInteger(0, nombre, OBJPROP_COLOR, c);
   ObjectSetInteger(0, nombre, OBJPROP_FILL, relleno);
   ObjectSetInteger(0, nombre, OBJPROP_BACK, true);
   ObjectSetInteger(0, nombre, OBJPROP_STYLE, estilo);
   ObjectSetInteger(0, nombre, OBJPROP_WIDTH, ancho);
   ObjectSetInteger(0, nombre, OBJPROP_SELECTABLE, false);
   ObjectSetInteger(0, nombre, OBJPROP_HIDDEN, true);
}

void Agregar(const int tipo, const int lado, const double bajo, const double alto, const datetime t0,
             const int estado, const bool conFVG)
{
   int n = ArraySize(zonas);
   ArrayResize(zonas, n + 1);
   zonas[n].tipo = tipo; zonas[n].lado = lado; zonas[n].bajo = bajo; zonas[n].alto = alto;
   zonas[n].t0 = t0; zonas[n].estado = estado; zonas[n].conFVG = conFVG; zonas[n].marco = marcoActual;
}

// Primer toque y si la zona sigue viva (no hay cierre al otro lado)
bool Seguir(const int desde, const int ult, const int lado, const double bajo, const double alto,
            const double &high[], const double &low[], const double &close[], int &estado)
{
   double mitad = (alto + bajo) / 2;
   estado = 0;
   bool tocado = false;
   for(int x = desde; x <= ult; x++)
   {
      if(!tocado && ((lado > 0 && low[x] <= alto) || (lado < 0 && high[x] >= bajo)))
      {
         tocado = true;
         estado = ((lado > 0 && low[x] <= mitad) || (lado < 0 && high[x] >= mitad)) ? 2 : 1;
      }
      if((lado > 0 && close[x] < bajo) || (lado < 0 && close[x] > alto))
         return false;
   }
   // la vela en curso: si toca por primera vez, se marca, pero no rompe hasta cerrar
   if(!tocado && enCurso > ult && ((lado > 0 && low[enCurso] <= alto) || (lado < 0 && high[enCurso] >= bajo)))
      estado = 3;
   return true;
}

void Serie(const int ini, const int ult, const datetime &time[], const double &high[], const double &low[])
{
   int lado = 0, ie = ini;
   double ext = high[ini], prevMax = 0, prevMin = 0;
   bool hayMax = false, hayMin = false;
   color sube = claro ? C'30,140,60' : clrLime, baja = claro ? C'200,90,0' : clrOrange;
   for(int i = ini + 1; i <= ult; i++)
   {
      if(lado >= 0)
      {
         if(high[i] > ext) { ie = i; ext = high[i]; }
         if(low[i] < ext - GiroSerieATR * Atr[i])
         {
            Zig[ie] = ext;
            string et = hayMax ? (ext > prevMax ? "HH" : "LH") : "max";
            Texto(P + "S" + IntegerToString(ie), time[ie], ext + 0.25 * Atr[ie], et,
                  et == "HH" ? sube : (et == "LH" ? baja : Tinta()), 9, ANCHOR_LOWER);
            prevMax = ext; hayMax = true; lado = -1; ie = i; ext = low[i];
         }
      }
      else
      {
         if(low[i] < ext) { ie = i; ext = low[i]; }
         if(high[i] > ext + GiroSerieATR * Atr[i])
         {
            Zig[ie] = ext;
            string et = hayMin ? (ext > prevMin ? "HL" : "LL") : "min";
            Texto(P + "S" + IntegerToString(ie), time[ie], ext - 0.25 * Atr[ie], et,
                  et == "HL" ? sube : (et == "LL" ? baja : Tinta()), 9, ANCHOR_UPPER);
            prevMin = ext; hayMin = true; lado = 1; ie = i; ext = high[i];
         }
      }
   }
}

void BuscarVacios(const int ini, const int ult, const datetime &time[], const double &high[],
                  const double &low[], const double &close[], const double &atr[], const int seg)
{
   for(int i = ini + 2; i <= ult; i++)
   {
      int lado = 0;
      double bajo = 0, alto = 0;
      if(low[i] > high[i - 2] && low[i] - high[i - 2] >= MinVacioATR * atr[i]) { lado = 1; bajo = high[i - 2]; alto = low[i]; }
      else if(high[i] < low[i - 2] && low[i - 2] - high[i] >= MinVacioATR * atr[i]) { lado = -1; bajo = high[i]; alto = low[i - 2]; }
      // El hueco de la reapertura diaria o del lunes no es un vacio: velas no seguidas
      if(lado == 0 || time[i] - time[i - 2] > 3 * seg)
         continue;
      int estado;
      if(Seguir(i + 1, ult, lado, bajo, alto, high, low, close, estado))
      {
         if(MostrarFVG)
            Agregar(0, lado, bajo, alto, time[i - 1], estado, false);
         continue;
      }
      if(!MostrarIFVG)
         continue;
      int roto = -1;
      for(int k = i + 1; k <= ult; k++)
         if((lado > 0 && close[k] < bajo) || (lado < 0 && close[k] > alto)) { roto = k; break; }
      bool vive = true;
      for(int k = roto + 1; k <= ult && roto >= 0; k++)
         if((lado > 0 && close[k] > alto) || (lado < 0 && close[k] < bajo)) { vive = false; break; }
      if(roto >= 0 && vive)
         Agregar(2, -lado, bajo, alto, time[roto], 0, false);
   }
}

void BuscarOB(const int ini, const int ult, const datetime &time[], const double &open[],
              const double &high[], const double &low[], const double &close[])
{
   double ultMax = 0, ultMin = 0;
   bool hayMax = false, hayMin = false;
   for(int k = ini + 4; k <= ult; k++)
   {
      int f = k - 2;   // fractal de 2 velas que se confirma en k
      if(high[f] >= high[f - 1] && high[f] >= high[f - 2] && high[f] >= high[f + 1] && high[f] >= high[f + 2]) { ultMax = high[f]; hayMax = true; }
      if(low[f] <= low[f - 1] && low[f] <= low[f - 2] && low[f] <= low[f + 1] && low[f] <= low[f + 2]) { ultMin = low[f]; hayMin = true; }
      for(int lado = 1; lado >= -1; lado -= 2)
      {
         if(!(lado > 0 ? (hayMax && close[k] > ultMax) : (hayMin && close[k] < ultMin)))
            continue;
         if(lado > 0) hayMax = false; else hayMin = false;
         int j = -1;
         for(int x = k - 1; x >= MathMax(ini, k - 10); x--)
            if((lado > 0 && close[x] < open[x]) || (lado < 0 && close[x] > open[x])) { j = x; break; }
         if(j < 0)
            continue;
         bool conFVG = false;
         for(int x = j + 2; x <= k; x++)
            if((lado > 0 && low[x] > high[x - 2]) || (lado < 0 && high[x] < low[x - 2])) { conFVG = true; break; }
         int estado;
         if(Seguir(k + 1, ult, lado, low[j], high[j], high, low, close, estado))
            Agregar(1, lado, low[j], high[j], time[j], estado, conFVG);
      }
   }
}

string Nombre(const Zona &z)
{
   string t = z.tipo == 0 ? "FVG" : (z.tipo == 1 ? "OB" : "IFVG");
   string l = z.lado > 0 ? "alcista" : "bajista";
   string e = z.estado == 0 ? "sin tocar" : (z.estado == 1 ? (z.tipo == 1 ? "defendido 56%" : "defendido 54%")
            : (z.estado == 3 ? "TOCANDO AHORA" : "llego a la mitad"));
   if(z.tipo == 2) e = "roto, ahora " + (z.lado > 0 ? "soporte" : "resistencia");
   return (z.marco == 1 ? "H1 " : "") + t + " " + l + " · " + e + (z.conFVG ? " · con FVG" : "");
}

void Dibujar(const double precio, const datetime fin)
{
   int n = ArraySize(zonas);
   // las mas cercanas por encima y por debajo, y las que tienen el precio dentro
   bool elegida[];
   ArrayResize(elegida, n);
   ArrayInitialize(elegida, false);
   for(int marco = 0; marco <= 1; marco++)
   for(int lado = 1; lado >= -1; lado -= 2)
      for(int c = 0; c < (marco == 0 ? ZonasPorLado : ZonasH1PorLado); c++)
      {
         int mejor = -1;
         double dist = DBL_MAX;
         for(int i = 0; i < n; i++)
         {
            if(elegida[i] || zonas[i].marco != marco || (zonas[i].estado == 2 && !MostrarMitad))
               continue;
            double d = lado > 0 ? zonas[i].bajo - precio : precio - zonas[i].alto;
            if(d >= 0 && d < dist) { dist = d; mejor = i; }
         }
         if(mejor >= 0) elegida[mejor] = true;
      }
   for(int i = 0; i < n; i++)
      if(zonas[i].bajo <= precio && precio <= zonas[i].alto && (zonas[i].estado != 2 || MostrarMitad))
         elegida[i] = true;
   for(int i = 0; i < n; i++)
   {
      if(!elegida[i])
         continue;
      string id = IntegerToString(i);
      Zona z = zonas[i];
      bool inverso = z.tipo == 2;
      Caja(P + "Z" + id, z.t0, z.bajo, fin, z.alto, inverso ? clrSlateGray : Relleno(z.tipo, z.marco),
           !inverso, inverso ? STYLE_DASH : STYLE_SOLID, 1);
      // la etiqueta al final de la caja, junto al precio actual, para que no quede cortada
      Texto(P + "N" + id, fin, z.alto, Nombre(z) + " ", z.estado == 3 ? clrDodgerBlue : Tinta(), 8,
            ANCHOR_RIGHT_UPPER, z.estado == 1 || z.estado == 3);
   }
}

// Vacios y order blocks de H1 que siguen vivos, para verlos en marcos menores
void ZonasH1()
{
   MqlRates rh[];
   int nh = CopyRates(_Symbol, PERIOD_H1, 0, VelasH1, rh);
   if(nh < 60)
      return;
   datetime tH[]; double oH[], hH[], lH[], cH[], aH[];
   ArrayResize(tH, nh); ArrayResize(oH, nh); ArrayResize(hH, nh); ArrayResize(lH, nh);
   ArrayResize(cH, nh); ArrayResize(aH, nh);
   for(int i = 0; i < nh; i++)
   {
      tH[i] = rh[i].time; oH[i] = rh[i].open; hH[i] = rh[i].high; lH[i] = rh[i].low; cH[i] = rh[i].close;
      double tr = (i == 0) ? hH[i] - lH[i]
                 : MathMax(hH[i] - lH[i], MathMax(MathAbs(hH[i] - cH[i - 1]), MathAbs(lH[i] - cH[i - 1])));
      aH[i] = (i < 14) ? ((i == 0) ? tr : (aH[i - 1] * i + tr) / (i + 1)) : aH[i - 1] + (tr - aH[i - 1]) / 14;
   }
   marcoActual = 1;
   enCurso = nh - 1;
   BuscarVacios(20, nh - 2, tH, hH, lH, cH, aH, 3600);
   if(MostrarOB)
      BuscarOB(20, nh - 2, tH, oH, hH, lH, cH);
   marcoActual = 0;
}

int OnCalculate(const int rates_total, const int prev_calculated, const datetime &time[],
                const double &open[], const double &high[], const double &low[], const double &close[],
                const long &tick_volume[], const long &volume[], const int &spread[])
{
   if(rates_total < 50)
      return 0;
   int inicio = prev_calculated > 1 ? prev_calculated - 1 : 0;
   for(int i = inicio; i < rates_total; i++)
   {
      double tr = (i == 0) ? high[i] - low[i]
                 : MathMax(high[i] - low[i], MathMax(MathAbs(high[i] - close[i - 1]), MathAbs(low[i] - close[i - 1])));
      Atr[i] = (i < 14) ? ((i == 0) ? tr : (Atr[i - 1] * i + tr) / (i + 1)) : Atr[i - 1] + (tr - Atr[i - 1]) / 14;
      Zig[i] = EMPTY_VALUE;
   }
   // Se redibuja al cerrar cada vela, si cambia el fondo, y cada 5 s para los toques en vivo
   bool c = FondoClaro();
   if(time[rates_total - 1] == ultimaVela && prev_calculated > 0 && c == claro
      && GetTickCount() - ultimoDibujo < 5000)
      return rates_total;
   ultimoDibujo = GetTickCount();
   enCurso = rates_total - 1;
   claro = c;
   ultimaVela = time[rates_total - 1];
   int ult = rates_total - 2;
   int ini = MathMax(20, rates_total - 1 - Velas);
   ObjectsDeleteAll(0, P);
   for(int i = ini; i <= ult; i++)
      Zig[i] = EMPTY_VALUE;
   if(MostrarSerie)
      Serie(ini, ult, time, high, low);
   ArrayResize(zonas, 0);
   BuscarVacios(ini, ult, time, high, low, close, Atr, PeriodSeconds());
   if(MostrarOB)
      BuscarOB(ini, ult, time, open, high, low, close);
   if(MostrarH1 && PeriodSeconds() < 3600)
      ZonasH1();
   enCurso = rates_total - 1;
   Dibujar(close[ult], time[ult] + PeriodSeconds() * 3);
   ChartRedraw();
   return rates_total;
}
