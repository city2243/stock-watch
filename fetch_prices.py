"""抓每檔收盤價、判定價位區間、新進買點就開 GitHub issue 提醒。

GitHub Actions 每個交易日跑兩次(台股/日股收盤後、美股收盤後);本機也能跑。
讀 docs/data/views.json(build_views.py 產生),寫 docs/data/prices.json 與 docs/data/alerts.json。
區間規則跟操作卡 meeting_signal/limitup/studies/calendar/action_plan.py 的 zones() 一致。
"""
import json, os, sys, datetime as dt
from pathlib import Path
import requests

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "docs" / "data"
BUY_ZONES = ("買進區", "加碼區", "風險區")
HIST_DAYS = 120


def _ca_bundle():
    # 本機 Norton 會攔 HTTPS:把 Windows 信任存放區併進 certifi
    if os.name != "nt":
        return
    import ssl, certifi
    p = ROOT / ".ca_bundle.pem"
    pems = [open(certifi.where(), encoding="utf-8").read()]
    for store in ("ROOT", "CA"):
        for der, _, _ in ssl.enum_certificates(store):
            pems.append(ssl.DER_cert_to_PEM_cert(der))
    p.write_text("\n".join(pems), encoding="utf-8")
    for k in ("REQUESTS_CA_BUNDLE", "SSL_CERT_FILE", "CURL_CA_BUNDLE"):
        os.environ[k] = str(p)


def _num(x):
    try:
        v = float(str(x).replace(",", ""))
        return v if v > 0 else None
    except (TypeError, ValueError):
        return None


def tw_quotes():
    """上市+上櫃+興櫃當日收盤:{代號: (價, 日期, 市場)}"""
    out = {}
    H = {"User-Agent": "Mozilla/5.0"}
    try:
        r = requests.get("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL", headers=H, timeout=30).json()
        for x in r:
            v = _num(x.get("ClosingPrice"))
            if v:
                out[x["Code"]] = (v, _roc(x.get("Date")), "TWSE")
    except Exception as e:
        print("TWSE 失敗:", e)
    try:
        r = requests.get("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes", headers=H, timeout=30).json()
        for x in r:
            v = _num(x.get("Close"))
            if v:
                out.setdefault(x["SecuritiesCompanyCode"], (v, _roc(x.get("Date")), "TPEx"))
    except Exception as e:
        print("TPEx 失敗:", e)
    try:
        r = requests.get("https://www.tpex.org.tw/openapi/v1/tpex_esb_latest_statistics", headers=H, timeout=30).json()
        for x in r:
            v = _num(x.get("LatestPrice") or x.get("Average"))
            if v:
                out.setdefault(x["SecuritiesCompanyCode"], (v, _roc(x.get("Date")), "興櫃"))
    except Exception as e:
        print("興櫃 失敗:", e)
    return out


def _roc(s):
    # 民國日期 1151027 → 2026-10-27
    s = str(s or "").strip()
    if len(s) == 7 and s.isdigit():
        return f"{int(s[:3]) + 1911}-{s[3:5]}-{s[5:]}"
    return s or None


def yf_quote(sym):
    import yfinance as yf
    h = yf.Ticker(sym).history(period="10d", auto_adjust=False)
    h = h.dropna(subset=["Close"])
    if h.empty:
        return None
    return float(h["Close"].iloc[-1]), h.index[-1].strftime("%Y-%m-%d"), "Yahoo"


def yf_batch(syms):
    """{代號: Yahoo 代碼} → {代號: (價, 日期, 'Yahoo')};失敗就回空"""
    if not syms:
        return {}
    import yfinance as yf
    try:
        df = yf.download(list(syms.values()), period="10d", group_by="ticker", auto_adjust=False, progress=False, threads=True)
    except Exception as e:
        print("Yahoo 批次失敗:", e)
        return {}
    out = {}
    for tk, s in syms.items():
        try:
            c = df[s]["Close"].dropna()
            if len(c):
                out[tk] = (float(c.iloc[-1]), c.index[-1].strftime("%Y-%m-%d"), "Yahoo")
        except KeyError:
            pass
    return out


def yf_hist(sym):
    import yfinance as yf
    try:
        c = yf.Ticker(sym).history(period="6mo", auto_adjust=False)["Close"].dropna()
        return [[i.strftime("%Y-%m-%d"), round(float(x), 2)] for i, x in c.items()]
    except Exception as e:
        print(sym, "補歷史失敗:", e)
        return []


def zone(px, v):
    """回傳 (區間名, 距買進價%)。和操作卡同一套邊界。"""
    etp = v.get("etp")
    if not (px and etp):
        return None
    bear = (v.get("scenarios") or {}).get("bear")
    bull = (v.get("scenarios") or {}).get("bull")
    if bear and px < bear:
        return "風險區"
    if px <= etp / 1.3:
        return "買進區"
    if px <= etp / 1.2:
        return "加碼區"
    if px <= etp * 0.9:
        return "觀望區"
    if px <= etp:
        return "減碼區"
    if not bull or px <= bull:
        return "超過目標區"
    return "出清區"


def main():
    _ca_bundle()
    views = json.loads((DATA / "views.json").read_text(encoding="utf-8"))["stocks"]
    old = {}
    pf = DATA / "prices.json"
    if pf.exists():
        old = json.loads(pf.read_text(encoding="utf-8")).get("stocks", {})

    tw = tw_quotes()
    print(f"台股報價 {len(tw)} 檔")
    # 證交所 openapi 有時落後一天:同時抓 Yahoo,取日期較新的那個
    exch = {tk: q[2] for tk, q in tw.items()}
    ysyms = {tk: tk + (".TWO" if tw[tk][2] == "TPEx" else ".TW")
             for tk in (v["tk"] for v in views if v["market"] == "TW") if tk in tw and tw[tk][2] != "興櫃"}
    for tk, q in yf_batch(ysyms).items():
        if q[1] > (tw[tk][1] or ""):
            tw[tk] = q
    new, alerts = {}, []
    for v in views:
        tk, mk = v["tk"], v["market"]
        q = None
        if mk == "TW":
            q = tw.get(tk)
            if not q:
                for suf in (".TW", ".TWO"):
                    try:
                        q = yf_quote(tk + suf)
                    except Exception:
                        q = None
                    if q:
                        break
        else:
            try:
                q = yf_quote(v["yf"])
            except Exception as e:
                print(tk, "Yahoo 失敗:", e)
        prev = old.get(tk, {})
        if not q:
            print(tk, v["nm"], "抓不到價,沿用上次")
            new[tk] = prev
            continue
        px, d, src = q
        z = zone(px, v)
        prev_hist = prev.get("hist", [])
        if len(prev_hist) < 20:   # 第一次:從 Yahoo 補半年收盤,走勢圖才有東西看
            sym = v.get("yf") or tk + (".TWO" if exch.get(tk) == "TPEx" else ".TW")
            prev_hist = yf_hist(sym) or prev_hist
        hist = [h for h in prev_hist if h[0] != d] + [[d, px]]
        hist = sorted(hist)[-HIST_DAYS:]
        rec = {"px": px, "d": d, "src": src, "zone": z, "hist": hist,
               "zone_since": prev.get("zone_since") if prev.get("zone") == z else d}
        new[tk] = rec
        # 新進可買的區間(前一次不在任何可買區)才提醒
        if z in BUY_ZONES and prev.get("zone") not in BUY_ZONES:
            alerts.append({"tk": tk, "nm": v["nm"], "zone": z, "px": px, "d": d,
                           "etp": v["etp"], "buy": v["buy_px"], "half": v["half_px"],
                           "rating": v.get("rating"), "reduce": v.get("view") == "減碼",
                           "model_date": v.get("model_date")})

    now = dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).strftime("%Y-%m-%d %H:%M")
    pf.write_text(json.dumps({"updated": now, "stocks": new}, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    af = DATA / "alerts.json"
    log = json.loads(af.read_text(encoding="utf-8")) if af.exists() else []
    log = (alerts + log)[:200]
    af.write_text(json.dumps(log, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"更新 {len(new)} 檔;新到價 {len(alerts)} 檔")

    missing = [v["tk"] for v in views if v.get("etp") and not new.get(v["tk"], {}).get("px")]
    if missing:
        print("沒有價格:", missing)
    if alerts:
        open_issue(alerts)
    # 健檢:有估值的股票有一半以上抓不到價就算失敗,讓 Actions 變紅
    priced = [v for v in views if v.get("etp")]
    if len(missing) > len(priced) / 2:
        sys.exit(1)


def open_issue(alerts):
    tok, repo = os.environ.get("GITHUB_TOKEN"), os.environ.get("GITHUB_REPOSITORY")
    lines = []
    for a in alerts:
        warn = "　⚠ 評等是減碼:到價也不買,先 refresh 模型" if a["reduce"] else ""
        lines.append(f"- **{a['tk']} {a['nm']}** 收 {a['px']:g}({a['d']})進 **{a['zone']}**;"
                     f"便宜價 ≤{a['buy']:g}、可買價 ≤{a['half']:g}、E(TP) {a['etp']:g};模型日 {a['model_date']}{warn}")
    body = ("以下股票新進入可買區間(規則同操作卡:買進區 ≤E(TP)÷1.3、加碼區 ≤E(TP)÷1.2、風險區=跌破悲觀價先重核):\n\n"
            + "\n".join(lines) + "\n\n買之前:確認翻轉條件沒有成立、模型沒有超過 45 天沒 refresh。")
    title = "到價提醒 " + alerts[0]["d"] + ":" + "、".join(f"{a['tk']} {a['nm']}" for a in alerts[:6])
    if not (tok and repo):
        print("[本機,不開 issue]", title)
        print(body)
        return
    r = requests.post(f"https://api.github.com/repos/{repo}/issues",
                      headers={"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json"},
                      json={"title": title, "body": body, "labels": ["到價提醒"]}, timeout=30)
    print("issue:", r.status_code, r.json().get("html_url"))


if __name__ == "__main__":
    main()
