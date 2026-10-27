# 個股追蹤看板

網頁:https://city2243.github.io/stock-watch/

自己研究過的個股:最新看法、便宜價(E(TP)÷1.3)、可買價(E(TP)÷1.2)與到價提醒。

- `build_views.py`(本機):讀 `equity-research/cases/*/action.json`、`src/readme_views.json`(早期案由 README 萃取;只在本機,不進 repo,因為含未過濾原文)、石化追蹤估值 → `docs/data/views.json`。研究更新後重跑再推。
- `fetch_prices.py`(GitHub Actions 每個交易日兩次):抓收盤價、判定區間;新進可買區間就開 issue(`到價提醒` 標籤)通知。
- 公開頁會過濾非公開來源(錄音、專家會議、付費內容)的字句,見 `build_views.py` 的 `PRIVATE_SRC`。
