//+------------------------------------------------------------------+
//| UT Bot Alerts (QuantNomad) traducido de Pine a MQL5, con el       |
//| aspecto del original: velas verdes/rojas y etiquetas Buy / Sell.  |
//| Por defecto usa lo que gano en la medicion de XAUUSD 15M          |
//| (materias/red/utbot.py):                                          |
//|   a=2, ATR 10, Heikin Ashi, solo señales a favor de la EMA 200    |
//|   2019-2024: +3,07 pips/op, PF 1,11 · 2025-2026: +21,4, PF 1,23   |
//| Para ver exactamente el de TradingView: ClaveA=1, HeikinAshi=     |
//| false, SoloFiltradas=false.                                       |
//| Las señales y alertas son de velas CERRADAS: dentro de la vela la |
//| señal puede aparecer y desaparecer.                               |
//+------------------------------------------------------------------+
#property copyright   "MCP Trade"
#property version     "2.00"
#property description "UT Bot con Heikin Ashi y filtro EMA 200 (medido en XAUUSD 15M)"
#property indicator_chart_window
#property indicator_buffers 11
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

input double ClaveA        = 2.0;   // Key value (sensibilidad). El original trae 1
input int    PeriodoATR    = 10;    // Periodo del ATR
input bool   HeikinAshi    = true;  // Señales con cierre Heikin Ashi
input int    PeriodoEMA    = 200;   // EMA de tendencia
input bool   SoloFiltradas = true;  // Solo señales a favor de la EMA
input bool   ColorearVelas = true;  // Velas verdes/rojas como el original
input bool   MostrarLinea  = false; // Dibujar la linea del trailing stop
input bool   MostrarEMA    = false; // Dibujar la EMA 200
input bool   Alertas       = true;  // Ventana de alerta al cerrar la vela
input bool   AlMovil       = false; // Notificacion push al movil (MetaQuotes ID)

#define PREFIJO "UTB_"

double VO[], VH[], VL[], VC[], VColor[];
double Linea[], ColorLinea[], Compra[], Venta[], Ema[], Atr[];
datetime ultimaAlerta = 0;

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
   PlotIndexSetInteger(0, PLOT_DRAW_TYPE, ColorearVelas ? DRAW_COLOR_CANDLES : DRAW_NONE);
   PlotIndexSetInteger(1, PLOT_DRAW_TYPE, MostrarLinea ? DRAW_COLOR_LINE : DRAW_NONE);
   PlotIndexSetInteger(4, PLOT_DRAW_TYPE, MostrarEMA ? DRAW_LINE : DRAW_NONE);
   PlotIndexSetInteger(2, PLOT_ARROW, 233);
   PlotIndexSetInteger(3, PLOT_ARROW, 234);
   for(int p = 0; p < 5; p++)
      PlotIndexSetDouble(p, PLOT_EMPTY_VALUE, EMPTY_VALUE);
   IndicatorSetString(INDICATOR_SHORTNAME, StringFormat("UT Bot a=%.1f ATR%d%s%s",
                      ClaveA, PeriodoATR, HeikinAshi ? " HA" : "", SoloFiltradas ? " EMA200" : ""));
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
void Etiqueta(const datetime t, const double precio, const bool compra)
{
   string nombre = PREFIJO + (compra ? "B" : "S") + IntegerToString((long)t);
   if(ObjectFind(0, nombre) >= 0)
      return;
   ObjectCreate(0, nombre, OBJ_TEXT, 0, t, precio);
   ObjectSetString(0, nombre, OBJPROP_TEXT, compra ? "Buy" : "Sell");
   ObjectSetString(0, nombre, OBJPROP_FONT, "Arial Bold");
   ObjectSetInteger(0, nombre, OBJPROP_FONTSIZE, 9);
   ObjectSetInteger(0, nombre, OBJPROP_COLOR, compra ? clrLime : clrRed);
   ObjectSetInteger(0, nombre, OBJPROP_ANCHOR, compra ? ANCHOR_UPPER : ANCHOR_LOWER);
   ObjectSetInteger(0, nombre, OBJPROP_SELECTABLE, false);
   ObjectSetInteger(0, nombre, OBJPROP_HIDDEN, true);
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
      if(compra && (!SoloFiltradas || close[i] > Ema[i]))
      {
         Compra[i] = low[i] - 0.2 * Atr[i];
         Etiqueta(time[i], low[i] - 0.6 * Atr[i], true);
      }
      if(venta && (!SoloFiltradas || close[i] < Ema[i]))
      {
         Venta[i] = high[i] + 0.2 * Atr[i];
         Etiqueta(time[i], high[i] + 0.6 * Atr[i], false);
      }
   }

   // Alerta solo con la ultima vela CERRADA y una vez por vela
   int j = rates_total - 2;
   if(prev_calculated == 0)
      ultimaAlerta = time[j];               // al cargar no avisa de señales viejas
   else if(time[j] != ultimaAlerta && (Compra[j] != EMPTY_VALUE || Venta[j] != EMPTY_VALUE))
   {
      ultimaAlerta = time[j];
      string msg = StringFormat("UT %s %s %s @ %s", Compra[j] != EMPTY_VALUE ? "BUY" : "SELL",
                                _Symbol, EnumToString(_Period), DoubleToString(close[j], _Digits));
      if(Alertas) Alert(msg);
      if(AlMovil) SendNotification(msg);
   }
   return rates_total;
}
