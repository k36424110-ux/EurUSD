from flask import Flask, jsonify, render_template, request
import os, time, threading, math
from datetime import datetime, timezone
import requests
import pandas as pd
import numpy as np

app = Flask(__name__)
APP_NAME = "EURUSD Engine Pro"
CACHE_TTL = int(os.getenv("CACHE_TTL_SECONDS", "45"))
_cache, _lock = {}, threading.Lock()
YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/{}"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; EURUSDEnginePro/1.0)"}

class MarketDataError(Exception): pass

def get_candles(symbol="EURUSD=X", interval="5m", period=None):
    allowed = {"1m": "1d", "5m": "5d", "15m": "5d", "30m": "1mo", "60m": "1mo", "1d": "1y"}
    if interval not in allowed: raise MarketDataError("Unité de temps non prise en charge")
    period = period or allowed[interval]
    key = f"{symbol}:{interval}:{period}"
    now = time.time()
    with _lock:
        item = _cache.get(key)
        if item and now - item[0] < CACHE_TTL: return item[1].copy()
    try:
        r = requests.get(YAHOO.format(symbol), params={"range": period, "interval": interval, "events": "history"}, headers=HEADERS, timeout=12)
        r.raise_for_status(); payload = r.json()
        result = payload.get("chart", {}).get("result")
        if not result: raise MarketDataError("La source ne renvoie pas de bougies pour le moment")
        obj = result[0]; q = obj["indicators"]["quote"][0]
        df = pd.DataFrame({k: q.get(k, []) for k in ["open", "high", "low", "close", "volume"]}, index=pd.to_datetime(obj.get("timestamp", []), unit="s", utc=True))
        df = df.dropna(subset=["open", "high", "low", "close"])
        if len(df) < 50: raise MarketDataError("Historique insuffisant pour analyser ce marché")
        df.index.name = "time"
        with _lock: _cache[key] = (now, df)
        return df.copy()
    except (requests.RequestException, ValueError, KeyError, TypeError) as e:
        raise MarketDataError(f"Données de marché temporairement indisponibles ({type(e).__name__}). Réessaie dans quelques instants.") from e

def indicators(df):
    x = df.copy()
    x["ema20"] = x.close.ewm(span=20, adjust=False).mean()
    x["ema50"] = x.close.ewm(span=50, adjust=False).mean()
    x["ema200"] = x.close.ewm(span=200, adjust=False).mean()
    prev = x.close.shift(1)
    tr = pd.concat([x.high-x.low, (x.high-prev).abs(), (x.low-prev).abs()], axis=1).max(axis=1)
    x["atr"] = tr.rolling(14).mean()
    x["vol_avg"] = x.volume.rolling(20).mean()
    return x

def swing_points(df, left=3, right=3):
    highs, lows = [], []
    if len(df) < left + right + 5: return highs, lows
    h, l = df.high.to_numpy(), df.low.to_numpy()
    for i in range(left, len(df)-right):
        if h[i] >= max(h[i-left:i+right+1]) and h[i] > max(h[i-left:i]): highs.append((i, float(h[i])))
        if l[i] <= min(l[i-left:i+right+1]) and l[i] < min(l[i-left:i]): lows.append((i, float(l[i])))
    return highs, lows

def detect_features(raw):
    df = indicators(raw)
    last, prev = df.iloc[-1], df.iloc[-2]
    atr = float(last.atr) if pd.notna(last.atr) and last.atr > 0 else float((df.high-df.low).tail(14).mean())
    highs, lows = swing_points(df.tail(120).reset_index(drop=True))
    # swing indices are local to the last 120 bars
    tail = df.tail(120).reset_index(drop=True)
    recent_highs = highs[-8:]; recent_lows = lows[-8:]
    recent_high = max((p for _,p in recent_highs), default=float(tail.high.iloc[-20:].max()))
    recent_low = min((p for _,p in recent_lows), default=float(tail.low.iloc[-20:].min()))
    close = float(last.close)
    trend = "HAUSSIÈRE" if last.ema20 > last.ema50 and last.close > last.ema50 else "BAISSIÈRE" if last.ema20 < last.ema50 and last.close < last.ema50 else "NEUTRE"
    # Sweep and reclaim: prior swing taken, candle closes back inside level.
    prior_high = float(df.high.iloc[-21:-1].max()); prior_low = float(df.low.iloc[-21:-1].min())
    sweep_high = float(last.high) > prior_high and close < prior_high
    sweep_low = float(last.low) < prior_low and close > prior_low
    # Three-candle FVG, only the most recent gap is surfaced.
    fvg = None
    for i in range(max(2, len(df)-30), len(df)):
        a, c = df.iloc[i-2], df.iloc[i]
        if float(c.low) > float(a.high): fvg = {"type":"haussière", "low":float(a.high), "high":float(c.low), "bar":i}
        elif float(c.high) < float(a.low): fvg = {"type":"baissière", "low":float(c.high), "high":float(a.low), "bar":i}
    # Heuristic OB: last opposite candle before a displacement >= 1.2 ATR, only recent 12 bars.
    ob = None
    for i in range(len(df)-2, max(2, len(df)-13), -1):
        base, move = df.iloc[i-1], df.iloc[i]
        body = abs(float(move.close)-float(move.open))
        if body >= max(atr*1.2, 1e-8):
            if move.close > move.open and base.close < base.open:
                ob = {"type":"haussier", "low":float(base.low), "high":float(max(base.open,base.close)), "bar":i-1}; break
            if move.close < move.open and base.close > base.open:
                ob = {"type":"baissier", "low":float(min(base.open,base.close)), "high":float(base.high), "bar":i-1}; break
    # Approximate rolling VWAP: Yahoo FX volume is often absent/zero, so never fabricate a volume VWAP.
    volume_ok = df.volume.notna().sum() > 30 and float(df.volume.fillna(0).tail(30).sum()) > 0
    vwap = None
    if volume_ok:
        sample = df.tail(100); typical = (sample.high+sample.low+sample.close)/3
        vwap = float((typical*sample.volume.fillna(0)).sum()/sample.volume.fillna(0).sum())
    return {"price":close,"time":df.index[-1].isoformat(),"trend":trend,"ema20":float(last.ema20),"ema50":float(last.ema50),"ema200":float(last.ema200) if pd.notna(last.ema200) else None,"atr":atr,"atr_pips":atr*10000,"recent_high":recent_high,"recent_low":recent_low,"sweep_high":bool(sweep_high),"sweep_low":bool(sweep_low),"fvg":fvg,"order_block":ob,"vwap":vwap,"volume_available":bool(volume_ok),"candles":len(df),"spread_note":"Le spread réel du courtier n'est pas fourni par cette source."}

def build_analysis():
    frames = {tf: get_candles(interval=tf) for tf in ["15m", "5m", "1m", "60m"]}
    feats = {tf: detect_features(frame) for tf,frame in frames.items()}
    h1, m15, m5, m1 = feats["60m"], feats["15m"], feats["5m"], feats["1m"]
    score = 0; reasons=[]
    for label, f, weight in [("H1",h1,2),("M15",m15,2),("M5",m5,1)]:
        if f["trend"] == "HAUSSIÈRE": score += weight; reasons.append(f"{label}: EMA20 au-dessus de l'EMA50 et prix cohérent avec une structure haussière")
        elif f["trend"] == "BAISSIÈRE": score -= weight; reasons.append(f"{label}: EMA20 sous l'EMA50 et prix cohérent avec une structure baissière")
    if m5["sweep_low"]: score += 2; reasons.append("M5: balayage du plus bas récent suivi d'une réintégration")
    if m5["sweep_high"]: score -= 2; reasons.append("M5: balayage du plus haut récent suivi d'une réintégration")
    if m5["fvg"] and m5["fvg"]["type"] == "haussière": score += 1; reasons.append("M5: FVG haussière récente détectée")
    elif m5["fvg"] and m5["fvg"]["type"] == "baissière": score -= 1; reasons.append("M5: FVG baissière récente détectée")
    if m5["order_block"]: reasons.append(f"M5: zone d'Order Block {m5['order_block']['type']} candidate détectée (heuristique, à valider)")
    # Require alignment and a real trigger; otherwise don't manufacture a trade.
    direction = "LONG" if score >= 4 else "SHORT" if score <= -4 else "WAIT"
    trigger = m1["sweep_low"] if direction == "LONG" else m1["sweep_high"] if direction == "SHORT" else False
    if direction in ("LONG","SHORT") and not trigger:
        reasons.append("Le biais est marqué, mais le déclencheur M1 n'est pas confirmé: pas d'entrée immédiate")
        direction = "WAIT"
    entry = m1["price"]; atr = max(m5["atr"], 0.0001)
    sl = tp = rr = None
    if direction in ("LONG","SHORT"):
        sl = entry - 1.2*atr if direction == "LONG" else entry + 1.2*atr
        tp = entry + 1.8*atr if direction == "LONG" else entry - 1.8*atr
        rr = round(abs(tp-entry)/abs(entry-sl),2)
    if not reasons: reasons.append("Aucun avantage directionnel clair détecté par les règles actuelles")
    return {"app":APP_NAME,"symbol":"EUR/USD","source":"Yahoo Finance chart endpoint (flux indicatif; pas un flux institutionnel centralisé)","generated_at":datetime.now(timezone.utc).isoformat(),"signal":direction,"score":score,"confidence_label":"Confluence de règles, pas une probabilité statistique","reasons":reasons,"entry_reference":entry if direction in ("LONG","SHORT") else None,"stop_loss":sl,"take_profit":tp,"rr":rr,"timeframes":feats,"warnings":["Signaux expérimentaux: aucune rentabilité n'est garantie.","Les Order Blocks et FVG sont des heuristiques, pas une vérité objective.","Pas d'exécution d'ordres ni de spread courtier dans cette version.","Les données FX publiques peuvent être retardées, incomplètes ou différentes du prix de ton courtier."]}

@app.route("/")
def home(): return render_template("index.html", app_name=APP_NAME)

@app.route("/api/health")
def health():
    return jsonify({"status":"ok","app":APP_NAME,"utc":datetime.now(timezone.utc).isoformat(),"cache_ttl_seconds":CACHE_TTL,"note":"La santé du serveur ne garantit pas la disponibilité de la source de marché."})

@app.route("/api/analysis")
def api_analysis():
    try: return jsonify(build_analysis())
    except MarketDataError as e: return jsonify({"error":str(e),"status":"data_unavailable","generated_at":datetime.now(timezone.utc).isoformat()}), 503
    except Exception as e:
        app.logger.exception("Analysis failed")
        return jsonify({"error":"Erreur interne pendant l'analyse. Consulte les logs Render.","status":"error"}), 500

@app.route("/api/backtest")
def backtest():
    """Small transparent baseline backtest: EMA20/EMA50 cross, next-bar execution, ATR exits.
    Not a proof of profitability; approximate costs are user supplied in pips."""
    try:
        days = min(max(int(request.args.get("days", "30")), 5), 365)
        cost_pips = min(max(float(request.args.get("cost_pips", "1.2")), 0), 10)
        df = get_candles(interval="30m", period="1y" if days > 30 else "1mo").tail(days*48).copy()
        df = indicators(df).dropna().reset_index(drop=True)
        df["signal"] = np.where((df.ema20 > df.ema50) & (df.ema20.shift(1) <= df.ema50.shift(1)), 1,
                          np.where((df.ema20 < df.ema50) & (df.ema20.shift(1) >= df.ema50.shift(1)), -1, 0))
        trades=[]; i=1
        while i < len(df)-1:
            side=int(df.signal.iloc[i])
            if side == 0: i+=1; continue
            entry=float(df.open.iloc[i+1]); atr=float(df.atr.iloc[i])
            if not math.isfinite(atr) or atr <= 0: i+=1; continue
            risk=1.2*atr; target=1.8*atr
            sl=entry-side*risk; tp=entry+side*target
            outcome=None; exit_price=float(df.close.iloc[min(i+12,len(df)-1)])
            for j in range(i+1,min(i+13,len(df))):
                hi,lo=float(df.high.iloc[j]),float(df.low.iloc[j])
                stop_hit=(lo <= sl) if side==1 else (hi >= sl)
                target_hit=(hi >= tp) if side==1 else (lo <= tp)
                if stop_hit and target_hit: outcome=-1.0; exit_price=sl; break # conservative if both in same candle
                if stop_hit: outcome=-1.0; exit_price=sl; break
                if target_hit: outcome=1.5; exit_price=tp; break
            if outcome is None: outcome=side*(exit_price-entry)/risk
            outcome -= cost_pips/ (risk*10000)
            trades.append({"side":"LONG" if side==1 else "SHORT","entry":entry,"exit":exit_price,"r_multiple":round(outcome,3)})
            i += 12
        rs=[t["r_multiple"] for t in trades]
        wins=[r for r in rs if r>0]; losses=[r for r in rs if r<0]
        gross_win=sum(wins); gross_loss=abs(sum(losses))
        return jsonify({"method":"EMA20/EMA50 cross baseline; next-bar open; ATR stop/target; conservative same-candle handling","period_days":days,"bars":len(df),"trades":len(rs),"win_rate_pct":round(100*len(wins)/len(rs),2) if rs else None,"expectancy_R":round(float(np.mean(rs)),3) if rs else None,"profit_factor":round(gross_win/gross_loss,3) if gross_loss else (None if not gross_win else "infinite_no_losses"),"net_R":round(sum(rs),3),"max_drawdown_R":round(float(max((max(np.cumsum(rs)[:i+1])-np.cumsum(rs)[i] for i in range(len(rs))), default=0)),3) if rs else None,"cost_assumption_pips":cost_pips,"trades_preview":trades[-20:],"limitations":["Yahoo candles are not a broker-grade tick feed.","Spread is modeled as a constant pip cost; slippage and commissions are not fully modeled.","This baseline does not backtest the Order Block/FVG setup.","A candle can touch both stop and target; the test assumes the stop was hit first.","Results are research only and can differ materially from live execution."]})
    except MarketDataError as e: return jsonify({"error":str(e)}),503
    except Exception:
        app.logger.exception("Backtest failed")
        return jsonify({"error":"Backtest impossible actuellement; consulte les logs Render."}),500

if __name__ == "__main__": app.run(host="0.0.0.0", port=int(os.getenv("PORT","5000")))
