//+------------------------------------------------------------------+
//| Estructura y vacios, la misma logica que materias/red/zonas.py:   |
//|  - maximos/minimos de SERIE: el extremo de cada tramo (zigzag que |
//|    se da la vuelta 3 ATR), con HH / LH / HL / LL                  |
//|  - maximos/minimos relativos (fractal 2 velas): puntos pequeños   |
//|  - vacios (FVG) de 3 velas, con su mitad (CE). Se cortan cuando   |
//|    una vela CIERRA al otro lado; entonces pasan a inversos (IFVG) |
//| Medido en XAUUSD 15M desde 2022 (cierres contra el azar):         |
//|  primer toque que se queda en el borde: reacciona 54 % (azar 38)  |
//|  primer toque que llega a la mitad:     reacciona 27 % (azar 30)  |
//|  extremos de serie: se rompen 74 %; la ruptura sigue 47 %         |
//|  (relativos: 77 % y 41 %)                                         |
//+------------------------------------------------------------------+
#property copyright   "MCP Trade"
#property version     "1.00"
#property description "Maximos/minimos de serie, relativos y vacios (FVG/IFVG) con su estado"
#property indicator_chart_window
#property indicator_buffers 4
#property indicator_plots   3

#property indicator_label1  "Serie"
#property indicator_type1   DRAW_SECTION
#property indicator_color1  clrDodgerBlue
#property indicator_width1  1

#property indicator_label2  "Max relativo"
#property indicator_type2   DRAW_ARROW
#property indicator_color2  clrSilver

#property indicator_label3  "Min relativo"
#property indicator_type3   DRAW_ARROW
#property indicator_color3  clrSilver

input int    Velas           = 600;   // Velas hacia atras que se analizan
input double GiroSerieATR    = 3.0;   // Giro en ATR que cierra un tramo (maximo/minimo de serie)
input double MinVacioATR     = 0.2;   // Vacios mas chicos que esto (en ATR) se ignoran
input bool   MostrarRelativos = true; // Puntos en los maximos/minimos relativos
input bool   MostrarRotos    = false; // Dibujar tambien los vacios ya rotos
input bool   MostrarIFVG     = true;  // Vacios rotos que pasan a inversos
input color  ColorAlcista    = C'0,90,40';
input color  ColorBajista    = C'110,20,20';
input color  ColorDefendido  = C'0,150,70';   // primer toque en el borde (el que se defiende)
input color  ColorDefendidoB = C'190,40,40';
input color  ColorInverso    = C'70,70,90';

#define P "EZ_"

double Zig[], FracH[], FracL[], Atr[];
datetime ultimaVela = 0;

int OnInit()
{
   SetIndexBuffer(0, Zig, INDICATOR_DATA);
   SetIndexBuffer(1, FracH, INDICATOR_DATA);
   SetIndexBuffer(2, FracL, INDICATOR_DATA);
   SetIndexBuffer(3, Atr, INDICATOR_CALCULATIONS);
   PlotIndexSetInteger(1, PLOT_ARROW, 159);
   PlotIndexSetInteger(2, PLOT_ARROW, 159);
   for(int p = 0; p < 3; p++)
      PlotIndexSetDouble(p, PLOT_EMPTY_VALUE, EMPTY_VALUE);
   if(!MostrarRelativos)
   {
      PlotIndexSetInteger(1, PLOT_DRAW_TYPE, DRAW_NONE);
      PlotIndexSetInteger(2, PLOT_DRAW_TYPE, DRAW_NONE);
   }
   IndicatorSetString(INDICATOR_SHORTNAME, "Estructura y vacios");
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason) { ObjectsDeleteAll(0, P); }

void Texto(const string nombre, const datetime t, const double precio, const string txt,
           const color c, const int tam, const ENUM_ANCHOR_POINT anc)
{
   ObjectCreate(0, nombre, OBJ_TEXT, 0, t, precio);
   ObjectSetString(0, nombre, OBJPROP_TEXT, txt);
   ObjectSetString(0, nombre, OBJPROP_FONT, "Arial Bold");
   ObjectSetInteger(0, nombre, OBJPROP_FONTSIZE, tam);
   ObjectSetInteger(0, nombre, OBJPROP_COLOR, c);
   ObjectSetInteger(0, nombre, OBJPROP_ANCHOR, anc);
   ObjectSetInteger(0, nombre, OBJPROP_SELECTABLE, false);
   ObjectSetInteger(0, nombre, OBJPROP_HIDDEN, true);
}

void Caja(const string nombre, const datetime t1, const double p1, const datetime t2, const double p2,
          const color c, const bool relleno, const ENUM_LINE_STYLE estilo)
{
   ObjectCreate(0, nombre, OBJ_RECTANGLE, 0, t1, p1, t2, p2);
   ObjectSetInteger(0, nombre, OBJPROP_COLOR, c);
   ObjectSetInteger(0, nombre, OBJPROP_FILL, relleno);
   ObjectSetInteger(0, nombre, OBJPROP_BACK, true);
   ObjectSetInteger(0, nombre, OBJPROP_STYLE, estilo);
   ObjectSetInteger(0, nombre, OBJPROP_SELECTABLE, false);
   ObjectSetInteger(0, nombre, OBJPROP_HIDDEN, true);
}

void Mitad(const string nombre, const datetime t1, const datetime t2, const double p, const color c)
{
   ObjectCreate(0, nombre, OBJ_TREND, 0, t1, p, t2, p);
   ObjectSetInteger(0, nombre, OBJPROP_COLOR, c);
   ObjectSetInteger(0, nombre, OBJPROP_STYLE, STYLE_DOT);
   ObjectSetInteger(0, nombre, OBJPROP_RAY_RIGHT, false);
   ObjectSetInteger(0, nombre, OBJPROP_BACK, true);
   ObjectSetInteger(0, nombre, OBJPROP_SELECTABLE, false);
   ObjectSetInteger(0, nombre, OBJPROP_HIDDEN, true);
}

void Serie(const int ini, const int ult, const datetime &time[], const double &high[], const double &low[])
{
   int lado = 0, ie = ini;
   double ext = high[ini];
   double prevMax = 0, prevMin = 0;
   bool hayMax = false, hayMin = false;
   for(int i = ini + 1; i <= ult; i++)
   {
      if(lado >= 0)
      {
         if(high[i] > ext) { ie = i; ext = high[i]; }
         if(low[i] < ext - GiroSerieATR * Atr[i])
         {
            Zig[ie] = ext;
            string et = hayMax ? (ext > prevMax ? "HH" : "LH") : "max";
            Texto(P + "S" + IntegerToString(ie), time[ie], ext + 0.3 * Atr[ie], et,
                  et == "HH" ? clrLime : (et == "LH" ? clrOrange : clrWhite), 8, ANCHOR_LOWER);
            prevMax = ext; hayMax = true;
            lado = -1; ie = i; ext = low[i];
         }
      }
      else
      {
         if(low[i] < ext) { ie = i; ext = low[i]; }
         if(high[i] > ext + GiroSerieATR * Atr[i])
         {
            Zig[ie] = ext;
            string et = hayMin ? (ext > prevMin ? "HL" : "LL") : "min";
            Texto(P + "S" + IntegerToString(ie), time[ie], ext - 0.3 * Atr[ie], et,
                  et == "HL" ? clrLime : (et == "LL" ? clrOrange : clrWhite), 8, ANCHOR_UPPER);
            prevMin = ext; hayMin = true;
            lado = 1; ie = i; ext = high[i];
         }
      }
   }
}

void Vacios(const int ini, const int ult, const datetime &time[], const double &high[],
            const double &low[], const double &close[])
{
   datetime futuro = time[ult] + PeriodSeconds() * 8;
   for(int i = ini + 2; i <= ult; i++)
   {
      int lado = 0;
      double bajo = 0, alto = 0;
      if(low[i] > high[i - 2] && low[i] - high[i - 2] >= MinVacioATR * Atr[i])
      { lado = 1; bajo = high[i - 2]; alto = low[i]; }
      else if(high[i] < low[i - 2] && low[i - 2] - high[i] >= MinVacioATR * Atr[i])
      { lado = -1; bajo = high[i]; alto = low[i - 2]; }
      if(lado == 0)
         continue;
      double ce = (alto + bajo) / 2;
      int roto = -1, toque = -1;
      bool llegoMitad = false;
      for(int k = i + 1; k <= ult; k++)
      {
         if(toque < 0 && ((lado > 0 && low[k] <= alto) || (lado < 0 && high[k] >= bajo)))
         {
            toque = k;
            llegoMitad = lado > 0 ? low[k] <= ce : high[k] >= ce;
         }
         if((lado > 0 && close[k] < bajo) || (lado < 0 && close[k] > alto)) { roto = k; break; }
      }
      string id = IntegerToString(i);
      if(roto < 0)
      {
         bool defendido = toque >= 0 && !llegoMitad;
         color c = defendido ? (lado > 0 ? ColorDefendido : ColorDefendidoB) : (lado > 0 ? ColorAlcista : ColorBajista);
         Caja(P + "F" + id, time[i - 1], bajo, futuro, alto, c, true, STYLE_SOLID);
         Mitad(P + "M" + id, time[i - 1], futuro, ce, clrGray);
         string estado = toque < 0 ? "sin tocar" : (llegoMitad ? "llego a la mitad" : "defendido 54%");
         Texto(P + "T" + id, futuro, lado > 0 ? alto : bajo, estado, clrSilver, 7,
               lado > 0 ? ANCHOR_LEFT_LOWER : ANCHOR_LEFT_UPPER);
         continue;
      }
      if(MostrarRotos)
         Caja(P + "F" + id, time[i - 1], bajo, time[roto], alto, lado > 0 ? ColorAlcista : ColorBajista, false, STYLE_DOT);
      if(!MostrarIFVG)
         continue;
      // Inverso: vive hasta que una vela cierra al otro lado otra vez
      int fin = -1;
      for(int k = roto + 1; k <= ult; k++)
         if((lado > 0 && close[k] > alto) || (lado < 0 && close[k] < bajo)) { fin = k; break; }
      if(fin < 0)
      {
         Caja(P + "I" + id, time[roto], bajo, futuro, alto, ColorInverso, true, STYLE_SOLID);
         Texto(P + "IT" + id, futuro, lado > 0 ? bajo : alto, "IFVG", clrSilver, 7,
               lado > 0 ? ANCHOR_LEFT_UPPER : ANCHOR_LEFT_LOWER);
      }
   }
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
      Zig[i] = FracH[i] = FracL[i] = EMPTY_VALUE;
   }
   // Todo se redibuja solo al cerrar cada vela: la vela en curso no cuenta
   if(time[rates_total - 1] == ultimaVela && prev_calculated > 0)
      return rates_total;
   ultimaVela = time[rates_total - 1];
   int ult = rates_total - 2;
   int ini = MathMax(20, rates_total - 1 - Velas);
   ObjectsDeleteAll(0, P);
   for(int i = ini; i <= ult; i++)
      Zig[i] = FracH[i] = FracL[i] = EMPTY_VALUE;
   for(int i = ini + 2; i <= ult - 2; i++)
   {
      if(high[i] >= high[i - 1] && high[i] >= high[i - 2] && high[i] >= high[i + 1] && high[i] >= high[i + 2])
         FracH[i] = high[i] + 0.15 * Atr[i];
      if(low[i] <= low[i - 1] && low[i] <= low[i - 2] && low[i] <= low[i + 1] && low[i] <= low[i + 2])
         FracL[i] = low[i] - 0.15 * Atr[i];
   }
   Serie(ini, ult, time, high, low);
   Vacios(ini, ult, time, high, low, close);
   ChartRedraw();
   return rates_total;
}
