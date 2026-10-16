# 🌀 陀螺到貨雷達（全台）

每天自動收集全台近期**戰鬥陀螺 BEYBLADE** 到貨、補貨、開賣消息，整理成手機好讀的網頁。
網頁上方可以自己選縣市（可複選，或選「全台」），選擇會記在手機上，下次打開不用重選。

- 📱 網頁：`https://<你的帳號>.github.io/Beyblade-instock/`（開啟 GitHub Pages 後可用，建議加到手機主畫面）
- 📄 備用：直接在 GitHub App 打開 [`LATEST.md`](LATEST.md)

## 運作方式

GitHub Actions 每天台灣時間 **08:00、18:00** 執行 `scraper/collect.py`：

| 來源 | 內容 |
|---|---|
| Google 新聞 | 「戰鬥陀螺 到貨／補貨／開賣」、超商、門市等新聞 |
| PTT（Toy、BabyMother、Lifeismoney、Gossiping） | 網友回報現貨、補貨 |
| Facebook 公開貼文 | 透過 Google 新聞收錄的公開粉專／社團貼文（店家到貨公告、網友轉售）。只收得到 Google 有收錄的部分 |
| YouTube（選填） | 近 14 天「到貨／補貨／開箱」影片，需要免費 API 金鑰 |
| Threads（選填） | 官方 API 關鍵字搜尋，需要 Meta App 權杖（見下方限制） |

每則消息會自動標記：📍 縣市（依縣市名、行政區、商圈判斷）、📦 到貨關鍵字、🏬 通路（玩具反斗城、Funbox…）。
只提到陀螺、但沒有到貨字眼、通路或型號的新聞（例如政治人物、明星玩陀螺）會被濾掉。
同一則消息被多家媒體轉載會合併成一張卡片（「另有 N 則相關報導」）。
沒寫縣市的連鎖通路消息（例如超商全台開賣）標成「全台通路」，選任何縣市都看得到。
資料保留 30 天，存在 `data/items.json`。

> Dcard 會擋 GitHub 的伺服器，Bing 新聞不提供 RSS，玩具人沒有開放搜尋介面，所以都沒有收（玩具人的報導 Google 新聞也收得到）。

## 第一次設定（只要做一次）

1. Repo → **Settings → Pages → Build and deployment → Source** 選 **GitHub Actions**
   （免費帳號需要 repo 是 Public）
2. Repo → **Actions** → 「每日收集陀螺到貨資訊」→ **Run workflow** 手動跑第一次
3. 跑完後打開上面的網頁網址

## 追蹤常去的玩具店

小玩具店的到貨消息多半只發在 Facebook 粉專。在 [`shops.json`](shops.json) 的 `shops` 加上店名，
每天就會專門搜這幾家的貼文，網頁上會標「🏪 店名」和店家所在縣市：

```json
"shops": [
  {"name": "某某玩具店", "city": "新北", "keywords": ["某某玩具", "某某中和店"]},
  {"name": "玩具e哥", "keywords": ["TOYeGO"]}
]
```

連鎖店（例如玩具e哥）不用填 `city`，會從貼文裡的分店名（「新莊昌隆店」「台北松山店」）判斷縣市，卡片上也會顯示分店。

手機上可以在 GitHub 打開 `shops.json` → 右上角 ✏️ 編輯 → Commit changes；或直接跟 Claude 說店名。
另外每個縣市都會各搜一次「陀螺 到貨／現貨／補貨」的 Facebook 貼文。

## 開啟社群來源（選填）

到 Repo → **Settings → Secrets and variables → Actions → New repository secret** 新增：

| Secret 名稱 | 怎麼取得 |
|---|---|
| `YOUTUBE_API_KEY` | [Google Cloud Console](https://console.cloud.google.com/apis/library/youtube.googleapis.com) 啟用 YouTube Data API v3 → 憑證 → 建立 API 金鑰（免費，本專案每天約用 400 / 10,000 額度） |
| `THREADS_ACCESS_TOKEN` | 在 [Meta for Developers](https://developers.facebook.com/) 建立 App 並加入 Threads 使用案例，取得 `threads_basic`、`threads_keyword_search` 權限的長期權杖 |

沒設定的來源會在網頁「資料來源狀態」顯示「未設定」，不影響其他來源。

**Threads 的限制**：Meta 規定 App 沒通過 `threads_keyword_search` 審核前，只搜得到**自己帳號**的貼文；要通過 App Review 才搜得到公開貼文。長期權杖 60 天到期，到期要重新產生並更新 Secret。

**Facebook／Instagram**：不直接爬（要登入、沒有搜尋 API，登入去爬違反使用條款、帳號可能被停權）。
Facebook 改從 Google 新聞收錄的公開貼文間接取得；個人二手／轉售貼文會標「二手／轉售」，不會出現在「到貨／補貨」分頁。
Instagram 目前沒有可行的管道。

## 自訂

關鍵字、縣市對應的行政區（`REGIONS`）、通路清單都在 `scraper/collect.py` 最上方，直接改就好。
