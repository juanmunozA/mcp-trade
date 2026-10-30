//+------------------------------------------------------------------+
//| UT Bot Alerts (QuantNomad) traducido de Pine a MQL5, con el       |
//| aspecto del original: velas verdes/rojas y etiquetas Buy / Sell.  |
//| Por defecto, como el de TradingView del usuario (a=1, ATR 14):    |
//| todas las señales; las que van a favor de la EMA 200 salen        |
//| grandes y las que van en contra, apagadas.                        |
//| Medido (materias/red/utbot.py): a=1 ATR14 en M5 pierde -1,09      |
//| pips/op en 2019-2024. Lo que gano: a=2, ATR 10, Heikin Ashi y     |
//| SoloFiltradas=true en M15: +3,07 pips/op, PF 1,11.                |
//| Las señales y alertas son de velas CERRADAS: dentro de la vela la |
//| señal puede aparecer y desaparecer.                               |
//| v3: niveles del rebote del ultimo impulso con la probabilidad de  |
//| llegar a cada uno (materias/red/largo_rebote.py, 5M/15M 2022-26). |
//+------------------------------------------------------------------+
#property copyright   "MCP Trade"
#property version     "3.00"
#property description "UT Bot con Heikin Ashi y filtro EMA 200 (medido en XAUUSD 15M)"
#property indicator_chart_window
#property indicator_buffers 12
#property indicator_plots   5

#property indicator_label1  "Velas UT"
#property indicator_type1   DRAW_COLOR_CANDLES
#property indicator_color1  clrLime,clrRed

#property indicator_label2  "Linea UT"
#property indicator_type2   DRAW_COLOR_LINE
#property indicator_color2  clrLime,clrRed
#property indicator_width2  1

#property indicator_label3  "Buy"
#property indicator_type3   DRAW_ARROW
#property indicator_color3  clrLime
#property indicator_width3  2

#property indicator_label4  "Sell"
#property indicator_type4   DRAW_ARROW
#property indicator_color4  clrRed
#property indicator_width4  2

#property indicator_label5  "EMA 200"
#property indicator_type5   DRAW_LINE
#property indicator_color5  clrGold
#property indicator_style5  STYLE_DOT

input double ClaveA        = 1.0;   // Key value (sensibilidad). Medido: 2 da menos señales y mejores
input int    PeriodoATR    = 14;    // Periodo del ATR
input bool   HeikinAshi    = false; // Señales con cierre Heikin Ashi
input int    PeriodoEMA    = 200;   // EMA de tendencia
input bool   SoloFiltradas = false; // Ocultar las que van contra la EMA (si no, salen apagadas)
input bool   ColorearVelas = true;  // Velas verdes/rojas como el original
input bool   MostrarLinea  = false; // Dibujar la linea del trailing stop
input bool   MostrarEMA    = false; // Dibujar la EMA 200
input bool   Alertas       = true;  // Ventana de alerta al cerrar la vela
input bool   AlertarTodas  = false; // Alertar tambien las que van contra la EMA
input bool   AlMovil       = false; // Notificacion push al movil (MetaQuotes ID)
input bool   MostrarRebote = true;  // Niveles del rebote con su probabilidad (medido desde 2022)
input int    VelasRebote   = 3000;  // Velas para el zigzag del rebote

#include "Rebote_tabla.mqh"

#define PREFIJO "UTB_"
#define PREF_REB "UTB_R_"

double VO[], VH[], VL[], VC[], VColor[];
double Linea[], ColorLinea[], Compra[], Venta[], Ema[], Atr[], Fuerte[];
datetime ultimaAlerta = 0;
bool claro = false;
color cVerde = clrLime, cRojo = clrRed, cVerdeD = clrSeaGreen, cRojoD = clrIndianRed;

// Colores segun el fondo: en blanco, la paleta de TradingView; en negro, vivos
void Paleta()
{
   color bg = (color)ChartGetInteger(0, CHART_COLOR_BACKGROUND);
   claro = ((bg & 0xFF) + ((bg >> 8) & 0xFF) + ((bg >> 16) & 0xFF)) / 3 > 128;
   cVerde  = claro ? C'38,166,154'  : clrLime;
   cRojo   = claro ? C'239,83,80'   : clrRed;
   cVerdeD = claro ? C'150,205,198' : clrSeaGreen;
   cRojoD  = claro ? C'244,170,168' : clrIndianRed;
   PlotIndexSetInteger(0, PLOT_LINE_COLOR, 0, cVerde);
   PlotIndexSetInteger(0, PLOT_LINE_COLOR, 1, cRojo);
   PlotIndexSetInteger(1, PLOT_LINE_COLOR, 0, cVerde);
   PlotIndexSetInteger(1, PLOT_LINE_COLOR, 1, cRojo);
   PlotIndexSetInteger(2, PLOT_LINE_COLOR, cVerde);
   PlotIndexSetInteger(3, PLOT_LINE_COLOR, cRojo);
   PlotIndexSetInteger(4, PLOT_LINE_COLOR, claro ? C'200,150,0' : clrGold);
}

int OnInit()
{
   SetIndexBuffer(0, VO, INDICATOR_DATA);
   SetIndexBuffer(1, VH, INDICATOR_DATA);
   SetIndexBuffer(2, VL, INDICATOR_DATA);
   SetIndexBuffer(3, VC, INDICATOR_DATA);
   SetIndexBuffer(4, VColor, INDICATOR_COLOR_INDEX);
   SetIndexBuffer(5, Linea, INDICATOR_DATA);
   SetIndexBuffer(6, ColorLinea, INDICATOR_COLOR_INDEX);
   SetIndexBuffer(7, Compra, INDICATOR_DATA);
   SetIndexBuffer(8, Venta, INDICATOR_DATA);
   SetIndexBuffer(9, Ema, INDICATOR_DATA);
   SetIndexBuffer(10, Atr, INDICATOR_CALCULATIONS);
   SetIndexBuffer(11, Fuerte, INDICATOR_CALCULATIONS);
   PlotIndexSetInteger(0, PLOT_DRAW_TYPE, ColorearVelas ? DRAW_COLOR_CANDLES : DRAW_NONE);
   PlotIndexSetInteger(1, PLOT_DRAW_TYPE, MostrarLinea ? DRAW_COLOR_LINE : DRAW_NONE);
   PlotIndexSetInteger(4, PLOT_DRAW_TYPE, MostrarEMA ? DRAW_LINE : DRAW_NONE);
   PlotIndexSetInteger(2, PLOT_ARROW, 233);
   PlotIndexSetInteger(3, PLOT_ARROW, 234);
   for(int p = 0; p < 5; p++)
      PlotIndexSetDouble(p, PLOT_EMPTY_VALUE, EMPTY_VALUE);
   IndicatorSetString(INDICATOR_SHORTNAME, StringFormat("UT Bot a=%.1f ATR%d%s%s",
                      ClaveA, PeriodoATR, HeikinAshi ? " HA" : "", SoloFiltradas ? " EMA200" : ""));
   Paleta();
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   ObjectsDeleteAll(0, PREFIJO);
}

double Src(const int i, const double &o[], const double &h[], const double &l[], const double &c[])
{
   return HeikinAshi ? (o[i] + h[i] + l[i] + c[i]) / 4.0 : c[i];
}

// Etiqueta "Buy" / "Sell" junto a la vela, como el labelup/labeldown de Pine
void Etiqueta(const datetime t, const double precio, const bool compra, const bool fuerte)
{
   string nombre = PREFIJO + (compra ? "B" : "S") + IntegerToString((long)t);
   if(ObjectFind(0, nombre) >= 0)
      return;
   ObjectCreate(0, nombre, OBJ_TEXT, 0, t, precio);
   ObjectSetString(0, nombre, OBJPROP_TEXT, compra ? "Buy" : "Sell");
   // A favor de la EMA 200: grande y vivo. En contra: pequeña y apagada
   ObjectSetString(0, nombre, OBJPROP_FONT, fuerte ? "Arial Black" : "Arial");
   ObjectSetInteger(0, nombre, OBJPROP_FONTSIZE, fuerte ? 10 : 7);
   ObjectSetInteger(0, nombre, OBJPROP_COLOR, compra ? (fuerte ? cVerde : cVerdeD) : (fuerte ? cRojo : cRojoD));
   ObjectSetInteger(0, nombre, OBJPROP_ANCHOR, compra ? ANCHOR_UPPER : ANCHOR_LOWER);
   ObjectSetInteger(0, nombre, OBJPROP_SELECTABLE, false);
   ObjectSetInteger(0, nombre, OBJPROP_HIDDEN, true);
}

//--- Rebote: el mismo zigzag que materias/red/largo_rebote.py (giro de REB_K ATR) ------------
// Tabla de 5M para M1-M5; de 15M para el resto (en H1 o mas no esta medida: orientativa)
double Superv(const int g, const double r)
{
   double x = r / REB_PASO;
   if(x <= 0) return 1.0;
   if(x >= REB_PASOS - 1) return (_Period <= PERIOD_M5) ? REB_M5[g][REB_PASOS - 1] : REB_M15[g][REB_PASOS - 1];
   int k = (int)MathFloor(x);
   double f = x - k;
   double a = (_Period <= PERIOD_M5) ? REB_M5[g][k] : REB_M15[g][k];
   double b = (_Period <= PERIOD_M5) ? REB_M5[g][k + 1] : REB_M15[g][k + 1];
   return a + f * (b - a);
}

double SiguePorProf(const double r)
{
   int k = r < 0.382 ? 0 : (r < 0.618 ? 1 : (r < 0.786 ? 2 : (r < 1.0 ? 3 : 4)));
   return (_Period <= PERIOD_M5) ? REB_SIGUE_PROF_M5[k] : REB_SIGUE_PROF_M15[k];
}

void NivelReb(const string nombre, const datetime t1, const datetime t2, const double precio, const color c,
           const ENUM_LINE_STYLE estilo, const string texto)
{
   string n = PREF_REB + nombre;
   if(ObjectFind(0, n) < 0)
   {
      ObjectCreate(0, n, OBJ_TREND, 0, t1, precio, t2, precio);
      ObjectSetInteger(0, n, OBJPROP_SELECTABLE, false);
      ObjectSetInteger(0, n, OBJPROP_HIDDEN, true);
      ObjectSetInteger(0, n, OBJPROP_RAY_RIGHT, false);
   }
   ObjectMove(0, n, 0, t1, precio);
   ObjectMove(0, n, 1, t2, precio);
   ObjectSetInteger(0, n, OBJPROP_COLOR, c);
   ObjectSetInteger(0, n, OBJPROP_STYLE, estilo);
   string e = n + "_t";
   if(ObjectFind(0, e) < 0)
   {
      ObjectCreate(0, e, OBJ_TEXT, 0, t2, precio);
      ObjectSetInteger(0, e, OBJPROP_ANCHOR, ANCHOR_LEFT);
      ObjectSetInteger(0, e, OBJPROP_FONTSIZE, 7);
      ObjectSetInteger(0, e, OBJPROP_SELECTABLE, false);
      ObjectSetInteger(0, e, OBJPROP_HIDDEN, true);
   }
   ObjectMove(0, e, 0, t2, precio);
   ObjectSetString(0, e, OBJPROP_TEXT, texto);
   ObjectSetInteger(0, e, OBJPROP_COLOR, c);
}

void Renglon(const int fila, const string texto, const color c)
{
   string n = PREF_REB + "panel" + IntegerToString(fila);
   if(ObjectFind(0, n) < 0)
   {
      ObjectCreate(0, n, OBJ_LABEL, 0, 0, 0);
      ObjectSetInteger(0, n, OBJPROP_CORNER, CORNER_LEFT_UPPER);
      ObjectSetInteger(0, n, OBJPROP_XDISTANCE, 8);
      ObjectSetInteger(0, n, OBJPROP_YDISTANCE, 22 + 14 * fila);
      ObjectSetInteger(0, n, OBJPROP_FONTSIZE, 8);
      ObjectSetString(0, n, OBJPROP_FONT, "Consolas");
      ObjectSetInteger(0, n, OBJPROP_SELECTABLE, false);
      ObjectSetInteger(0, n, OBJPROP_HIDDEN, true);
   }
   ObjectSetString(0, n, OBJPROP_TEXT, texto);
   ObjectSetInteger(0, n, OBJPROP_COLOR, c);
}

// El impulso mas reciente (>= REB_MIN_PIERNA ATR) y hasta donde suele llegar su rebote.
// Si el tramo desde el ultimo giro ya mide >= REB_MIN_PIERNA ATR, ese es el impulso y su
// rebote aun no esta confirmado. Si no, es el impulso anterior y el rebote ya se confirmo.
// Probabilidad de llegar a Y sabiendo que va por X: S(Y) / S(X).
void Rebote(const int rates_total, const datetime &time[], const double &high[], const double &low[],
            const double &close[])
{
   int u = rates_total - 1, s = MathMax(PeriodoATR + 1, rates_total - VelasRebote);
   int lado = 0, ie = s, pc = 0;
   double ext = high[s];
   int pIe[], pIc[], pT[]; double pP[];
   for(int i = s + 1; i <= u; i++)
   {
      double d = REB_K * Atr[i];
      int giro = 0;
      if(lado >= 0)
      {
         if(high[i] > ext || (lado == 0 && high[i] >= ext)) { ie = i; ext = high[i]; }
         if(low[i] < ext - d) giro = 1;
      }
      else
      {
         if(low[i] < ext) { ie = i; ext = low[i]; }
         if(high[i] > ext + d) giro = -1;
      }
      if(giro == 0)
         continue;
      ArrayResize(pIe, pc + 1, 512); ArrayResize(pIc, pc + 1, 512);
      ArrayResize(pT, pc + 1, 512); ArrayResize(pP, pc + 1, 512);
      pIe[pc] = ie; pIc[pc] = i; pP[pc] = ext; pT[pc] = giro; pc++;
      lado = -giro; ie = i; ext = giro > 0 ? low[i] : high[i];
   }
   if(pc < 2)
   {
      ObjectsDeleteAll(0, PREF_REB);
      return;
   }
   // ext / ie: el extremo del tramo que va desde el ultimo giro
   double P = pP[pc - 1], X = ext, A = 0, B = 0, prof = 0;
   int ladoImp = 0, iConf = 0, iB = 0;
   bool confirmado;
   if(MathAbs(P - X) >= REB_MIN_PIERNA * Atr[u])
   {
      A = P; B = X; ladoImp = -pT[pc - 1]; iConf = u; iB = ie; confirmado = false;
      for(int j = ie + 1; j <= u; j++)
         prof = MathMax(prof, ladoImp < 0 ? high[j] - B : B - low[j]);
   }
   else
   {
      // el ultimo impulso >= REB_MIN_PIERNA ATR cuyo extremo B no se ha vuelto a pasar: su rebote sigue vivo
      confirmado = true;
      bool hay = false;
      for(int k = pc - 1; k >= MathMax(1, pc - 8) && !hay; k--)
      {
         if(MathAbs(pP[k] - pP[k - 1]) < REB_MIN_PIERNA * Atr[pIc[k]])
            continue;
         double m = 0;
         bool roto = false;
         for(int j = pIe[k] + 1; j <= u && !roto; j++)
         {
            roto = pT[k] > 0 ? high[j] > pP[k] : low[j] < pP[k];
            m = MathMax(m, pT[k] > 0 ? pP[k] - low[j] : high[j] - pP[k]);
         }
         if(roto)
            continue;
         A = pP[k - 1]; B = pP[k]; ladoImp = pT[k]; iConf = pIc[k]; iB = pIe[k]; prof = m; hay = true;
      }
      if(!hay)
      {
         ObjectsDeleteAll(0, PREF_REB);
         return;
      }
   }
   double pierna = MathAbs(B - A), pa = pierna / Atr[iConf];
   if(pa < REB_MIN_PIERNA)
   {
      ObjectsDeleteAll(0, PREF_REB);
      return;
   }
   bool aFavor = ladoImp > 0 ? close[iConf] > Ema[iConf] : close[iConf] < Ema[iConf];
   int g = (aFavor ? 0 : 3) + (pa < 6 ? 0 : (pa < 10 ? 1 : 2));
   double x = prof / pierna, base = Superv(g, x);
   if(base <= 0) base = 1e-9;

   color cTxt = claro ? C'90,90,90' : clrSilver;
   color cNiv = ladoImp < 0 ? (claro ? C'200,90,90' : C'255,120,120') : (claro ? C'40,140,120' : C'120,220,180');
   datetime t2 = time[u] + PeriodSeconds() * 6;
   double niveles[5] = {0.382, 0.5, 0.618, 0.786, 1.0};
   for(int k = 0; k < 5; k++)
   {
      double r = niveles[k], precio = B - ladoImp * r * pierna;
      double p = r <= x ? 100.0 : 100.0 * MathMin(1.0, Superv(g, r) / base);
      NivelReb("n" + IntegerToString(k), time[iB], t2, precio, cNiv, k == 4 ? STYLE_SOLID : STYLE_DOT,
            StringFormat("%.1f%%  %s  %s", 100 * r, DoubleToString(precio, _Digits),
                         r <= x ? "ya llego" : StringFormat("%.0f%%", p)));
   }
   // mediana y 3 de cada 4, condicionadas a lo que ya reboto
   double med = 2.0, p75 = 2.0;
   for(double r = x + 0.01; r <= 2.0; r += 0.01)
   {
      double q = Superv(g, r) / base;
      if(med == 2.0 && q <= 0.5) med = r;
      if(q <= 0.25) { p75 = r; break; }
   }
   string tf = _Period <= PERIOD_M5 ? "5M" : "15M";
   Renglon(0, StringFormat("Rebote (tabla %s): impulso %s %s -> %s, %.1f ATR, %s EMA200", tf,
                           ladoImp > 0 ? "alcista" : "bajista", DoubleToString(A, _Digits), DoubleToString(B, _Digits),
                           pa, aFavor ? "a favor" : "contra"), cTxt);
   Renglon(1, confirmado ? StringFormat("Rebote confirmado: va por %.0f%% (%s)", 100 * x,
                                        DoubleToString(B - ladoImp * prof, _Digits))
                         : StringFormat("Rebote sin confirmar (%.0f%%): las probabilidades son si rebota %.0f ATR",
                                        100 * x, REB_K), cTxt);
   Renglon(2, StringFormat("La mitad para antes de %s (%.0f%%) · 3 de 4 antes de %s (%.0f%%)",
                           DoubleToString(B - ladoImp * med * pierna, _Digits), 100 * med,
                           DoubleToString(B - ladoImp * p75 * pierna, _Digits), 100 * p75), cTxt);
   Renglon(3, StringFormat("Hasta 78%% la tendencia sigue %.0f%% · si pasa 100%% (%s) solo %.0f%%: es giro",
                           SiguePorProf(0.7), DoubleToString(A, _Digits), SiguePorProf(1.5)), cTxt);
}

int OnCalculate(const int rates_total, const int prev_calculated, const datetime &time[],
                const double &open[], const double &high[], const double &low[], const double &close[],
                const long &tick_volume[], const long &volume[], const int &spread[])
{
   if(rates_total < PeriodoATR + 3)
      return 0;
   int inicio = prev_calculated > 1 ? prev_calculated - 1 : 0;
   double k = 2.0 / (PeriodoEMA + 1);
   for(int i = inicio; i < rates_total; i++)
   {
      Compra[i] = Venta[i] = EMPTY_VALUE;
      Fuerte[i] = 0;
      VO[i] = open[i];
      VH[i] = high[i];
      VL[i] = low[i];
      VC[i] = close[i];
      // ATR de Wilder, como ta.atr de Pine: media simple de arranque y luego rma
      double tr = (i == 0) ? high[i] - low[i]
                 : MathMax(high[i] - low[i], MathMax(MathAbs(high[i] - close[i - 1]), MathAbs(low[i] - close[i - 1])));
      if(i < PeriodoATR)
         Atr[i] = (i == 0) ? tr : (Atr[i - 1] * i + tr) / (i + 1);
      else
         Atr[i] = Atr[i - 1] + (tr - Atr[i - 1]) / PeriodoATR;
      Ema[i] = (i == 0) ? close[i] : Ema[i - 1] + k * (close[i] - Ema[i - 1]);

      double x = Src(i, open, high, low, close);
      if(i < PeriodoATR)
      {
         Linea[i] = x;
         ColorLinea[i] = VColor[i] = 0;
         continue;
      }
      double p = Linea[i - 1], x1 = Src(i - 1, open, high, low, close), d = ClaveA * Atr[i];
      if(x > p && x1 > p)      Linea[i] = MathMax(p, x - d);
      else if(x < p && x1 < p) Linea[i] = MathMin(p, x + d);
      else if(x > p)           Linea[i] = x - d;
      else                     Linea[i] = x + d;
      ColorLinea[i] = VColor[i] = (x > Linea[i]) ? 0 : 1;

      if(i < PeriodoATR + 2 || i == rates_total - 1)   // la vela en curso no da señal
         continue;
      bool compra = x > Linea[i] && x1 <= Linea[i - 1];
      bool venta  = x < Linea[i] && x1 >= Linea[i - 1];
      bool aFavor = compra ? close[i] > Ema[i] : close[i] < Ema[i];
      Fuerte[i] = aFavor ? 1 : 0;
      if(compra && (!SoloFiltradas || aFavor))
      {
         Compra[i] = low[i] - 0.2 * Atr[i];
         Etiqueta(time[i], low[i] - 0.6 * Atr[i], true, aFavor);
      }
      if(venta && (!SoloFiltradas || aFavor))
      {
         Venta[i] = high[i] + 0.2 * Atr[i];
         Etiqueta(time[i], high[i] + 0.6 * Atr[i], false, aFavor);
      }
   }

   // Alerta solo con la ultima vela CERRADA y una vez por vela
   int j = rates_total - 2;
   if(prev_calculated == 0)
      ultimaAlerta = time[j];               // al cargar no avisa de señales viejas
   else if(time[j] != ultimaAlerta && (Compra[j] != EMPTY_VALUE || Venta[j] != EMPTY_VALUE)
           && (AlertarTodas || Fuerte[j] > 0))
   {
      ultimaAlerta = time[j];
      string msg = StringFormat("UT %s %s %s @ %s", Compra[j] != EMPTY_VALUE ? "BUY" : "SELL",
                                _Symbol, EnumToString(_Period), DoubleToString(close[j], _Digits));
      if(Alertas) Alert(msg);
      if(AlMovil) SendNotification(msg);
   }
   if(MostrarRebote && rates_total > PeriodoATR + 50)
      Rebote(rates_total, time, high, low, close);
   return rates_total;
}
