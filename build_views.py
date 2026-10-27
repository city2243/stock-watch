"""彙整所有自己研究過的個股看法 → docs/data/views.json(本機跑,研究更新後重跑再推)。

來源優先序:
1. equity-research/cases/<案>/action.json(有 etp 的;各案簽核後的操作卡)
2. src/readme_views.json(較早的案只有 README,由 Haiku 逐案萃取、附行號,見 src/README)
3. equity-research/studies/petchem/data/valuation_summary.csv(石化追蹤系統的估值,無評等)
看法的版本紀錄在 src/history.json。
"""
import csv, json, re, datetime as dt
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ER = ROOT.parent / "equity-research"
CASES = ER / "cases"
PETCHEM = ER / "studies" / "petchem" / "data" / "valuation_summary.csv"
SRC = ROOT / "src"
OUT = ROOT / "docs" / "data" / "views.json"
STALE_DAYS = 45          # 操作卡規則:模型 45 天要 refresh
YF = {"4004": "4004.T", "VST": "VST"}
MARKET = {"4004": "JP", "VST": "US"}
CCY = {"JP": "JPY", "US": "USD", "TW": "TWD"}
# 只公開自己的分析;私人來源(錄音、受訪者)的內容不上公開頁
PRIVATE_THESIS = {"5284"}
PRIVATE_SRC = re.compile("錄音|專家會議|專家|總編|萬寶|訪談|論壇|私訪|座談|LINE|群組|老師")
HIDDEN = "(依據非公開來源,內容不公開)"


def scrub(x):
    """公開頁不放私人或付費來源:命中關鍵字的整句換掉"""
    if isinstance(x, str):
        return HIDDEN if PRIVATE_SRC.search(x) else x
    if isinstance(x, list):
        return [scrub(i) for i in x]
    if isinstance(x, dict):
        return {k: scrub(v) for k, v in x.items()}
    return x


def view_of(rating):
    r = (rating or "").upper()
    if "REDUCE" in r or "減碼" in r or "SELL" in r:
        return "減碼"
    if "偏空" in r:
        return "中立偏空"
    if any(k in r for k in ("ACCUMULATE", "BUY", "偏多", "買進")):
        return "偏多"
    if r.strip():
        return "中立"
    return None


def load(p, default):
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default


def from_action(d, a):
    sc = a.get("scenarios") or {}
    trig = a.get("triggers") or []
    return {
        "src": "個案模型", "model_date": a.get("date"), "close_at_model": a.get("close_at_model"),
        "rating": a.get("rating_zh") or a.get("rating"), "etp": a.get("etp"),
        "scenarios": {k: (sc.get(k) or {}).get("tp") for k in ("bear", "base", "bull")},
        "probs": {k: (sc.get(k) or {}).get("p") for k in ("bear", "base", "bull")},
        "thesis": a.get("thesis"),
        "up": [_trig(t) for t in trig if t.get("direction") == "正"][:4],
        "down": [_trig(t) for t in trig if t.get("direction") == "負"][:4],
        "next_checks": [{"d": c.get("d"), "what": c.get("what")} for c in (a.get("next_checks") or [])][:4],
    }


def _trig(t):
    when = f"({t['when']})" if t.get("when") else ""
    return f"{t.get('what', '')}:{t.get('threshold', '')}{when}"


def from_readme(r):
    sc = r.get("scenarios") or {}
    g = lambda k, f: (sc.get(k) or {}).get(f) if isinstance(sc.get(k), dict) else None
    return {
        "src": "README", "model_date": r.get("model_date"), "close_at_model": r.get("close_at_model"),
        "rating": r.get("rating"), "etp": r.get("etp"),
        "scenarios": {k: g(k, "tp") for k in ("bear", "base", "bull")},
        "probs": {k: g(k, "p") for k in ("bear", "base", "bull")},
        "thesis": r.get("thesis"),
        "up": [r["watch_up"]] if r.get("watch_up") else [],
        "down": [r["watch_down"]] if r.get("watch_down") else [],
        "next_checks": (r.get("next_checks") or [])[:4],
    }


def from_petchem(row, asof):
    f = lambda k: float(row[k]) if row.get(k) not in (None, "") else None
    return {
        "src": "石化追蹤", "model_date": asof, "close_at_model": f("price"), "rating": None,
        "etp": f("etp"), "scenarios": {"bear": f("tp_s3"), "base": f("tp_s2"), "bull": f("tp_s1")},
        "probs": {"bear": 0.25, "base": 0.5, "bull": 0.25},
        "thesis": f"石化追蹤系統估值({row.get('method')});2026E EPS {f('eps26e') or 0:.2f}、2027E {f('eps27e') or 0:.2f}(共識 {row.get('cons26')} / {row.get('cons27')})。",
        "up": [], "down": [], "next_checks": [],
    }


def main():
    readme = {r["case"]: r for r in load(SRC / "readme_views.json", [])}
    hist = {h["case"]: h for h in load(SRC / "history.json", [])}
    pet = {}
    if PETCHEM.exists():
        asof = dt.date.fromtimestamp(PETCHEM.stat().st_mtime).isoformat()
        for row in csv.DictReader(PETCHEM.open(encoding="utf-8-sig")):
            if row.get("has_tp") == "True" and row.get("etp"):
                pet[row["sid"]] = (row, asof)

    stocks, seen = [], set()
    for d in sorted(CASES.iterdir()):
        if not d.is_dir() or d.name.startswith("_"):
            continue
        tk, _, nm = d.name.partition("_")
        tk = "VST" if tk == "VST" else tk
        a = load(d / "action.json", {})
        if a.get("etp"):
            v = from_action(d, a)
        elif d.name in readme and readme[d.name].get("etp"):
            v = from_readme(readme[d.name])
            nm = nm or readme[d.name].get("nm") or ""
        elif tk in pet:
            v = from_petchem(*pet[tk])
        else:
            continue
        if not nm:
            nm = (readme.get(d.name) or {}).get("nm") or a.get("nm") or tk
        rv = readme.get(d.name) or {}
        if rv.get("market") in ("JP", "US"):
            MARKET[tk] = rv["market"]
            YF.setdefault(tk, tk + ".T" if rv["market"] == "JP" else tk)
        if tk == "VST":
            nm = "Vistra"
        elif tk == "6506":
            nm = "安川電機"
        stocks.append(_finish(tk, nm, d.name, v, hist.get(d.name) or readme.get(d.name)))
        seen.add(tk)
    for tk, (row, asof) in pet.items():   # 石化系統有、但沒有案資料夾的
        if tk not in seen:
            stocks.append(_finish(tk, row["name"], None, from_petchem(row, asof), None))

    stocks.sort(key=lambda s: s["tk"])
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"built": dt.datetime.now().strftime("%Y-%m-%d %H:%M"), "stocks": stocks},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    by = {}
    for s in stocks:
        by[s["src"]] = by.get(s["src"], 0) + 1
    print(f"{len(stocks)} 檔 → {OUT}  來源 {by}")


def _finish(tk, nm, case, v, h):
    mk = MARKET.get(tk, "TW")
    etp = v["etp"]
    md = v.get("model_date")
    try:
        age = (dt.date.today() - dt.date.fromisoformat(md)).days
    except (TypeError, ValueError):
        age = None
    history = [x for x in ((h or {}).get("history") or []) if x.get("etp") or x.get("rating")]
    up, down = v["up"], v["down"]
    if h and not up and h.get("watch_up"):
        up = [h["watch_up"]]
    if h and not down and h.get("watch_down"):
        down = [h["watch_down"]]
    thesis = v.get("thesis")
    if tk in PRIVATE_THESIS:
        thesis = "(本案部分依據為私人訪談,論點不公開;價位與評等照常追蹤)"
        history = [{k: x.get(k) for k in ("date", "ver", "etp", "rating")} for x in history]
        up, down = [], []
    return {
        "tk": tk, "nm": nm, "case": case, "market": mk, "yf": YF.get(tk), "ccy": CCY[mk],
        "src": v["src"], "model_date": md, "age_days": age, "stale": age is not None and age > STALE_DAYS,
        "close_at_model": v.get("close_at_model"), "rating": v.get("rating"), "view": view_of(v.get("rating")),
        "etp": etp, "scenarios": v["scenarios"], "probs": v["probs"],
        "buy_px": round(etp / 1.3, 2), "half_px": round(etp / 1.2, 2), "trim_px": round(etp * 0.9, 2),
        "thesis": scrub(thesis), "up": scrub(up), "down": scrub(down), "next_checks": scrub(v.get("next_checks") or []),
        "history": [{k: x.get(k) for k in ("date", "ver", "etp", "rating", "note")} for x in scrub(history)],
    }


if __name__ == "__main__":
    main()
