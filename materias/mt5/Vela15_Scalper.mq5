//+------------------------------------------------------------------+
//| Vela 15M en curso, para operar rebotes cortos en 1M / 5M.         |
//| NO da entradas: medido con 1M desde 2022, entrar en contra del    |
//| extremo de la vela de 15M pierde -0,01 a -0,03 ATR por operacion  |
//| (gana lo que el azar menos el spread). Sirve para poner TP y      |
//| stop con probabilidades medidas y para no pasarse de riesgo:      |
//|  - apertura, maximo y minimo de la vela de 15M en curso           |
//|  - desde el extremo mas cercano: cuanto suele rebotar antes de    |
//|    seguir 0,15 ATR mas alla, y cuanto dinero es con tu lote       |
//|  - perdida diaria usada / permitida, Quick Strike (< 30 s) y      |
//|    posiciones sin stop                                            |
//| Tablas: materias/red/vela15.py -> Vela15_tabla.mqh (2025-26).     |
//+------------------------------------------------------------------+
#property copyright   "MCP Trade"
#property version     "1.00"
#property description "Vela de 15M en curso: rebotes medidos, dinero por nivel y control de riesgo del fondeo"
#property indicator_chart_window
#property indicator_plots 0

input double Lote             = 3.0;  // Lote con el que operas
input double SaldoInicial     = 0;    // Tamano de la cuenta de fondeo (0 = balance al empezar el dia)
input double PerdidaDiariaPct = 5.0;  // Perdida diaria maxima de la cuenta (%)
input int    PeriodoATR       = 14;   // ATR de 15M (Wilder, como en la medicion)
input int    SegundosRapida   = 30;   // Quick Strike: operaciones mas cortas que esto
input bool   DibujarNiveles   = true; // Lineas de rebote y de «sigue» en el grafico

#include "Vela15_tabla.mqh"

#define PREF "V15_"

int OnInit()
{
   EventSetTimer(1);
   return(INIT_SUCCEEDED);
}

void OnDeinit(const int reason)
{
   EventKillTimer();
   ObjectsDeleteAll(0, PREF);
}

// ATR de Wilder de 15M en la vela ANTERIOR a la que esta en curso
double Atr15()
{
   MqlRates r[];
   ArraySetAsSeries(r, false);
   int n = CopyRates(_Symbol, PERIOD_M15, 1, PeriodoATR * 10, r);
   if(n < PeriodoATR + 1) return(0);
   double a = 0;
   for(int i = 1; i <= PeriodoATR; i++)
      a += MathMax(r[i].high - r[i].low, MathMax(MathAbs(r[i].high - r[i-1].close), MathAbs(r[i].low - r[i-1].close)));
   a /= PeriodoATR;
   for(int i = PeriodoATR + 1; i < n; i++)
   {
      double tr = MathMax(r[i].high - r[i].low, MathMax(MathAbs(r[i].high - r[i-1].close), MathAbs(r[i].low - r[i-1].close)));
      a = (a * (PeriodoATR - 1) + tr) / PeriodoATR;
   }
   return(a);
}

int Cubo(double x, const double &lim[], int n)
{
   int k = 0;
   for(int i = 0; i < n; i++) if(x >= lim[i]) k = i;
   return(k);
}

double Tabla(const double &t[], int cm, int ce, int k) { return(t[(cm * V15_NE + ce) * V15_NR + k]); }

double Dinero(double distancia) { return(distancia * Lote * SymbolInfoDouble(_Symbol, SYMBOL_TRADE_CONTRACT_SIZE)); }

string Usd(double x) { return((x >= 0 ? "+" : "-") + "$" + DoubleToString(MathAbs(x), 0)); }

string Pct(double p) { return(DoubleToString(100 * p, 0) + " %"); }

void Renglon(int fila, string texto, color c)
{
   string n = PREF + "f" + IntegerToString(fila);
   if(ObjectFind(0, n) < 0)
   {
      ObjectCreate(0, n, OBJ_LABEL, 0, 0, 0);
      ObjectSetInteger(0, n, OBJPROP_CORNER, CORNER_LEFT_LOWER);
      ObjectSetInteger(0, n, OBJPROP_ANCHOR, ANCHOR_LEFT_LOWER);
      ObjectSetInteger(0, n, OBJPROP_XDISTANCE, 8);
      ObjectSetString(0, n, OBJPROP_FONT, "Consolas");
      ObjectSetInteger(0, n, OBJPROP_FONTSIZE, 9);
      ObjectSetInteger(0, n, OBJPROP_SELECTABLE, false);
   }
   ObjectSetInteger(0, n, OBJPROP_YDISTANCE, 10 + 16 * fila);
   ObjectSetString(0, n, OBJPROP_TEXT, texto);
   ObjectSetInteger(0, n, OBJPROP_COLOR, c);
}

void Nivel(string nombre, datetime t1, datetime t2, double precio, color c, int estilo, string texto)
{
   string n = PREF + nombre, e = n + "_t";
   if(ObjectFind(0, n) < 0)
   {
      ObjectCreate(0, n, OBJ_TREND, 0, t1, precio, t2, precio);
      ObjectSetInteger(0, n, OBJPROP_SELECTABLE, false);
      ObjectCreate(0, e, OBJ_TEXT, 0, t2, precio);
      ObjectSetInteger(0, e, OBJPROP_ANCHOR, ANCHOR_LEFT);
      ObjectSetInteger(0, e, OBJPROP_FONTSIZE, 8);
      ObjectSetInteger(0, e, OBJPROP_SELECTABLE, false);
   }
   ObjectSetInteger(0, n, OBJPROP_TIME, 0, t1);
   ObjectSetInteger(0, n, OBJPROP_TIME, 1, t2);
   ObjectSetDouble(0, n, OBJPROP_PRICE, 0, precio);
   ObjectSetDouble(0, n, OBJPROP_PRICE, 1, precio);
   ObjectSetInteger(0, n, OBJPROP_COLOR, c);
   ObjectSetInteger(0, n, OBJPROP_STYLE, estilo);
   ObjectSetInteger(0, n, OBJPROP_RAY_RIGHT, false);
   ObjectSetInteger(0, e, OBJPROP_TIME, 0, t2);
   ObjectSetDouble(0, e, OBJPROP_PRICE, 0, precio);
   ObjectSetString(0, e, OBJPROP_TEXT, " " + texto);
   ObjectSetInteger(0, e, OBJPROP_COLOR, c);
}

void Pintar()
{
   double a = Atr15();
   datetime t0 = iTime(_Symbol, PERIOD_M15, 0);
   if(a <= 0 || t0 == 0) return;
   double o = iOpen(_Symbol, PERIOD_M15, 0), hi = iHigh(_Symbol, PERIOD_M15, 0), lo = iLow(_Symbol, PERIOD_M15, 0);
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID), ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   datetime ahora = TimeCurrent();
   int seg = (int)(ahora - t0), j = MathMin(14, seg / 60), falta = MathMax(0, 900 - seg);
   int cm = Cubo(j, V15_MINUTO, V15_NM);
   double rango = (hi - lo) / a;
   int fila = 0;

   // --- riesgo de la cuenta (lo mas importante va abajo del todo, fila 0) ---
   datetime dia = ahora - ahora % 86400;
   HistorySelect(dia, ahora + 60);
   double cerrado = 0;
   for(int i = HistoryDealsTotal() - 1; i >= 0; i--)
   {
      ulong d = HistoryDealGetTicket(i);
      long en = HistoryDealGetInteger(d, DEAL_ENTRY);
      if(en == DEAL_ENTRY_OUT || en == DEAL_ENTRY_INOUT || en == DEAL_ENTRY_OUT_BY)
         cerrado += HistoryDealGetDouble(d, DEAL_PROFIT) + HistoryDealGetDouble(d, DEAL_COMMISSION) + HistoryDealGetDouble(d, DEAL_SWAP);
   }
   double bal = AccountInfoDouble(ACCOUNT_BALANCE), eq = AccountInfoDouble(ACCOUNT_EQUITY);
   double base = SaldoInicial > 0 ? SaldoInicial : bal - cerrado;
   double tope = base * PerdidaDiariaPct / 100, hoy = cerrado + (eq - bal);
   double queda = tope + MathMin(0.0, hoy);
   double usd1 = Dinero(1.0);
   Renglon(fila++, StringFormat("Hoy %s · perdida diaria permitida $%.0f · te quedan $%.0f = %.2f $ de precio con %.2f lotes",
           Usd(hoy), tope, queda, usd1 > 0 ? queda / usd1 : 0, Lote), queda < tope * 0.3 ? clrRed : clrSilver);
   Renglon(fila++, StringFormat("%.2f lotes = $%.0f por cada $1 de precio · spread %.2f = -$%.0f al entrar",
           Lote, usd1, ask - bid, Dinero(ask - bid)), clrSilver);

   // --- posiciones abiertas en este simbolo ---
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong tk = PositionGetTicket(i);
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      bool compra = PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY;
      double sl = PositionGetDouble(POSITION_SL), pe = PositionGetDouble(POSITION_PRICE_OPEN);
      int vida = (int)(ahora - (datetime)PositionGetInteger(POSITION_TIME));
      string txt = StringFormat("#%I64u %s %.2f en %.2f · %s · abierta hace %d:%02d", tk, compra ? "BUY" : "SELL",
                   PositionGetDouble(POSITION_VOLUME), pe, Usd(PositionGetDouble(POSITION_PROFIT)), vida / 60, vida % 60);
      color c = clrSilver;
      if(vida < SegundosRapida) { txt += StringFormat(" · QUICK STRIKE: espera %d s antes de cerrar con ganancia", SegundosRapida - vida); c = clrOrange; }
      if(sl == 0) { txt += " · SIN STOP"; c = clrRed; }
      else txt += StringFormat(" · stop %.2f = %s", sl, Usd(Dinero((sl - pe) * (compra ? 1 : -1)) * PositionGetDouble(POSITION_VOLUME) / Lote));
      Renglon(fila++, txt, c);
   }

   Renglon(fila++, "Medido (1M, 2022-26): entrar en contra del extremo NO gana: -0,01 a -0,03 ATR por operacion. Esto es para TP y stop, no una señal.", clrGray);

   // --- el extremo mas cercano al precio: donde se piensa el rebote ---
   bool arriba = (hi - bid) <= (bid - lo);
   double ext = arriba ? hi : lo;
   int d = arriba ? 1 : -1;
   double estiron = (ext - o) * d / a;
   int ce = Cubo(estiron, V15_ESTIRON, V15_NE);
   double psig = Tabla(V15_PS, cm, ce, 1);
   string lin = StringFormat("Desde el %s %.2f (estirado %.2f ATR desde la apertura, precio a %.2f $):",
                arriba ? "MAXIMO" : "MINIMO", ext, estiron, MathAbs(bid - ext));
   for(int k = 0; k < V15_NR; k++)
   {
      double r = V15_RETRO[k], dist = r * a;
      lin += StringFormat(" %.2f$ %s", dist, Pct(Tabla(V15_PR, cm, ce, k)));
   }
   Renglon(fila++, lin, clrWhite);
   Renglon(fila++, StringFormat("  ...antes de que siga %.2f $ mas alla (%.2f): %s · rebote 0,25 ATR = %.2f $ = %s con tu lote · si sigue = %s",
           V15_SIGUE * a, ext + d * V15_SIGUE * a, Pct(Tabla(V15_PS, cm, ce, 1)), 0.25 * a, Usd(Dinero(0.25 * a)), Usd(-Dinero(V15_SIGUE * a))), clrWhite);
   Renglon(fila++, StringFormat("Vela 15M %s · minuto %d · faltan %d:%02d · ATR15 %.2f · rango ya %.2f ATR (normal %.2f, grande %.2f, muy grande %.2f)",
           TimeToString(t0, TIME_MINUTES), j, falta / 60, falta % 60, a, rango, V15_RANGO[1], V15_RANGO[2], V15_RANGO[3]),
           rango >= V15_RANGO[2] ? clrOrange : clrDodgerBlue);
   for(int f = fila; f < 12; f++) ObjectDelete(0, PREF + "f" + IntegerToString(f));

   if(!DibujarNiveles) return;
   datetime t2 = t0 + 900;
   Nivel("ap", t0, t2, o, clrGray, STYLE_DOT, "apertura 15M");
   Nivel("hi", t0, t2, hi, clrSilver, STYLE_SOLID, "max 15M");
   Nivel("lo", t0, t2, lo, clrSilver, STYLE_SOLID, "min 15M");
   for(int k = 0; k < V15_NR; k++)
   {
      double r = V15_RETRO[k];
      Nivel("r" + IntegerToString(k), t0, t2, ext - d * r * a, clrMediumSeaGreen, k == 1 ? STYLE_SOLID : STYLE_DASH,
            StringFormat("rebota %.2f ATR: %s · %s", r, Pct(Tabla(V15_PR, cm, ce, k)), Usd(Dinero(r * a))));
   }
   Nivel("sig", t0, t2, ext + d * V15_SIGUE * a, clrTomato, STYLE_DASH,
         StringFormat("sigue: %s · %s", Pct(psig), Usd(-Dinero(V15_SIGUE * a))));
}

void OnTimer() { Pintar(); ChartRedraw(); }

int OnCalculate(const int rates_total, const int prev_calculated, const datetime &time[], const double &open[],
                const double &high[], const double &low[], const double &close[], const long &tick_volume[],
                const long &volume[], const int &spread[])
{
   Pintar();
   return(rates_total);
}
