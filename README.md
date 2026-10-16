# 🌀 陀螺到貨雷達（全台）

每天自動收集全台近期**戰鬥陀螺 BEYBLADE** 到貨、補貨、開賣消息，整理成手機好讀的網頁。
網頁上方可以自己選縣市（可複選，或選「全台」），選擇會記在手機上，下次打開不用重選。

- 📱 網頁：`https://<你的帳號>.github.io/Beyblade-instock/`（開啟 GitHub Pages 後可用，建議加到手機主畫面）
- 📄 備用：直接在 GitHub App 打開 [`LATEST.md`](LATEST.md)

## 運作方式

GitHub Actions 每天台灣時間 **08:00、18:00** 執行 `scraper/collect.py`：

| 來源 | 內容 |
|---|---|
| Google 新聞 / Bing 新聞 | 「戰鬥陀螺 到貨／補貨／開賣」等新聞 |
| 玩具人 toy-people | 新品與上市資訊 |
| PTT（Toy、Beyblade、BabyMother、Lifeismoney、Gossiping） | 網友回報現貨、補貨 |
| Dcard | 網友回報（Dcard 常擋機器人，失敗屬正常） |

每則消息會自動標記：📍 縣市（依縣市名、行政區、商圈判斷）、📦 到貨關鍵字、🏬 通路（玩具反斗城、Funbox…）。
資料保留 30 天，存在 `data/items.json`。

## 第一次設定（只要做一次）

1. Repo → **Settings → Pages → Build and deployment → Source** 選 **GitHub Actions**
   （免費帳號需要 repo 是 Public）
2. Repo → **Actions** → 「每日收集陀螺到貨資訊」→ **Run workflow** 手動跑第一次
3. 跑完後打開上面的網頁網址

## 自訂

關鍵字、縣市對應的行政區（`REGIONS`）、通路清單都在 `scraper/collect.py` 最上方，直接改就好。
