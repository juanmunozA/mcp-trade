//+------------------------------------------------------------------+
//| UT Bot Alerts (QuantNomad) traducido de Pine a MQL5, con el       |
//| filtro que gano en la medicion de XAUUSD 15M (materias/red/utbot) |
//|   a=2, ATR 10, Heikin Ashi, solo señales a favor de la EMA 200    |
//|   2019-2024: +3,07 pips/op, PF 1,11 · 2025-2026: +21,4, PF 1,23   |
//| La ventaja esta en salir en la señal contraria, no en un objetivo |
//| fijo. Las alertas saltan solo con la vela CERRADA: dentro de la   |
//| vela la señal puede aparecer y desaparecer.                       |
//+------------------------------------------------------------------+
#property copyright   "MCP Trade"
#property version     "1.00"
#property description "UT Bot con Heikin Ashi y filtro EMA 200 (medido en XAUUSD 15M)"
#property indicator_chart_window
#property indicator_buffers 6
#property indicator_plots   4

#property indicator_label1  "Linea UT"
#property indicator_type1   DRAW_COLOR_LINE
#property indicator_color1  clrLime,clrRed
#property indicator_width1  2

#property indicator_label2  "Buy"
#property indicator_type2   DRAW_ARROW
#property indicator_color2  clrLime
#property indicator_width2  3

#property indicator_label3  "Sell"
#property indicator_type3   DRAW_ARROW
#property indicator_color3  clrRed
#property indicator_width3  3

#property indicator_label4  "EMA 200"
#property indicator_type4   DRAW_LINE
#property indicator_color4  clrGold
#property indicator_style4  STYLE_DOT

input double ClaveA        = 2.0;   // Key value (sensibilidad). El original trae 1
input int    PeriodoATR    = 10;    // Periodo del ATR
input bool   HeikinAshi    = true;  // Señales con cierre Heikin Ashi
input int    PeriodoEMA    = 200;   // EMA de tendencia
input bool   SoloFiltradas = true;  // Mostrar solo señales a favor de la EMA
input bool   Alertas       = true;  // Ventana de alerta al cerrar la vela
input bool   AlMovil       = false; // Notificacion push al movil (MetaQuotes ID)

double Linea[], ColorLinea[], Compra[], Venta[], Ema[], Atr[];
datetime ultimaAlerta = 0;

int OnInit()
{
   SetIndexBuffer(0, Linea, INDICATOR_DATA);
   SetIndexBuffer(1, ColorLinea, INDICATOR_COLOR_INDEX);
   SetIndexBuffer(2, Compra, INDICATOR_DATA);
   SetIndexBuffer(3, Venta, INDICATOR_DATA);
   SetIndexBuffer(4, Ema, INDICATOR_DATA);
   SetIndexBuffer(5, Atr, INDICATOR_CALCULATIONS);
   PlotIndexSetInteger(1, PLOT_ARROW, 233);
   PlotIndexSetInteger(2, PLOT_ARROW, 234);
   PlotIndexSetDouble(1, PLOT_EMPTY_VALUE, EMPTY_VALUE);
   PlotIndexSetDouble(2, PLOT_EMPTY_VALUE, EMPTY_VALUE);
   IndicatorSetString(INDICATOR_SHORTNAME, StringFormat("UT Bot a=%.1f ATR%d%s EMA%d",
                      ClaveA, PeriodoATR, HeikinAshi ? " HA" : "", PeriodoEMA));
   return INIT_SUCCEEDED;
}

double Src(const int i, const double &o[], const double &h[], const double &l[], const double &c[])
{
   return HeikinAshi ? (o[i] + h[i] + l[i] + c[i]) / 4.0 : c[i];
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
      Compra[i] = EMPTY_VALUE;
      Venta[i]  = EMPTY_VALUE;
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
         ColorLinea[i] = 0;
         continue;
      }
      double p = Linea[i - 1], x1 = Src(i - 1, open, high, low, close), d = ClaveA * Atr[i];
      if(x > p && x1 > p)      Linea[i] = MathMax(p, x - d);
      else if(x < p && x1 < p) Linea[i] = MathMin(p, x + d);
      else if(x > p)           Linea[i] = x - d;
      else                     Linea[i] = x + d;
      ColorLinea[i] = (x > Linea[i]) ? 0 : 1;

      if(i < PeriodoATR + 2)
         continue;
      bool compra = x > Linea[i] && x1 <= Linea[i - 1];
      bool venta  = x < Linea[i] && x1 >= Linea[i - 1];
      if(compra && (!SoloFiltradas || close[i] > Ema[i])) Compra[i] = low[i] - 0.3 * Atr[i];
      if(venta  && (!SoloFiltradas || close[i] < Ema[i])) Venta[i]  = high[i] + 0.3 * Atr[i];
   }

   // Alerta solo con la ultima vela CERRADA y una vez por vela
   int j = rates_total - 2;
   if(prev_calculated == 0)
      ultimaAlerta = time[j];               // al cargar no avisa de señales viejas
   else if(time[j] != ultimaAlerta && (Compra[j] != EMPTY_VALUE || Venta[j] != EMPTY_VALUE))
   {
      ultimaAlerta = time[j];
      string msg = StringFormat("UT %s %s %s @ %s · linea %s", Compra[j] != EMPTY_VALUE ? "BUY" : "SELL",
                                _Symbol, EnumToString(_Period), DoubleToString(close[j], _Digits),
                                DoubleToString(Linea[j], _Digits));
      if(Alertas) Alert(msg);
      if(AlMovil) SendNotification(msg);
   }
   return rates_total;
}
