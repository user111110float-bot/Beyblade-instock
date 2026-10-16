"""每天收集台北 / 新北戰鬥陀螺 (BEYBLADE) 到貨相關資訊，輸出成靜態網頁。

只用 Python 標準函式庫，GitHub Actions 上不需要安裝套件。
每個來源各自 try/except，某個網站掛掉不會影響其他來源。
"""

from __future__ import annotations

import datetime as dt
import email.utils
import hashlib
import html
import json
import re
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_FILE = ROOT / "data" / "items.json"
DOCS = ROOT / "docs"

TW = dt.timezone(dt.timedelta(hours=8))
NOW = dt.datetime.now(TW)
KEEP_DAYS = 30  # 歷史資料保留天數
UA = (
    "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Mobile Safari/537.36"
)

# ---- 關鍵字 ---------------------------------------------------------------

BEY_WORDS = ["戰鬥陀螺", "陀螺", "beyblade", "bey blade", "爆旋", "BX-", "UX-", "CX-"]
ARRIVAL_WORDS = [
    "到貨", "補貨", "現貨", "開賣", "上市", "發售", "販售", "上架", "抽選", "抽籤",
    "排隊", "入荷", "有貨", "新貨", "進貨", "預購", "開搶", "搶購", "缺貨", "售完",
]
TAIPEI = [
    "台北", "臺北", "北市", "中正", "大同", "中山", "松山", "大安", "萬華", "信義",
    "士林", "北投", "內湖", "南港", "文山", "西門", "地下街", "京站", "微風", "南西",
    "東區", "公館",
]
NEW_TAIPEI = [
    "新北", "板橋", "三重", "中和", "永和", "新莊", "新店", "土城", "蘆洲", "樹林",
    "汐止", "鶯歌", "三峽", "淡水", "林口", "五股", "泰山", "深坑", "八里", "瑞芳",
    "環球", "宏匯",
]
# 全台連鎖通路，標示出來方便判斷
STORES = [
    "玩具反斗城", "Toysrus", "Funbox", "麗嬰", "家樂福", "全聯", "大買家", "好市多",
    "Costco", "誠品", "金石堂", "7-11", "全家", "萊爾富", "momo", "PChome", "蝦皮",
    "博客來", "TAKARA", "Animate", "安利美特", "湯姆熊", "大魯閣",
]


def has_any(text: str, words: list[str]) -> list[str]:
    low = text.lower()
    return [w for w in words if w.lower() in low]


# ---- HTTP ------------------------------------------------------------------


def fetch(url: str, *, headers: dict | None = None, timeout: int = 25) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        charset = resp.headers.get_content_charset() or "utf-8"
        return resp.read().decode(charset, errors="replace")


def strip_tags(s: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", " ", s or "")).strip()


def parse_date(s: str | None) -> dt.datetime | None:
    if not s:
        return None
    try:
        d = email.utils.parsedate_to_datetime(s)
    except (TypeError, ValueError):
        try:
            d = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return d.astimezone(TW)


# ---- 來源 ------------------------------------------------------------------


def rss_items(xml_text: str, source: str) -> list[dict]:
    out = []
    root = ET.fromstring(xml_text)
    for it in root.iter("item"):
        title = strip_tags(it.findtext("title") or "")
        link = (it.findtext("link") or "").strip()
        desc = strip_tags(it.findtext("description") or "")
        pub = parse_date(it.findtext("pubDate"))
        src = it.findtext("source") or source
        out.append(
            {"title": title, "url": link, "summary": desc[:200], "date": pub, "source": src}
        )
    return out


def google_news(query: str) -> list[dict]:
    q = urllib.parse.quote(f"{query} when:14d")
    url = f"https://news.google.com/rss/search?q={q}&hl=zh-TW&gl=TW&ceid=TW:zh-Hant"
    return rss_items(fetch(url), "Google 新聞")


def bing_news(query: str) -> list[dict]:
    q = urllib.parse.quote(query)
    url = f"https://www.bing.com/news/search?q={q}&format=rss&setlang=zh-TW&cc=TW"
    return rss_items(fetch(url), "Bing 新聞")


def wordpress_search(base: str, query: str, name: str) -> list[dict]:
    q = urllib.parse.quote(query)
    return rss_items(fetch(f"{base}/?s={q}&feed=rss2"), name)


PTT_ENTRY = re.compile(
    r'<div class="r-ent">.*?<div class="title">\s*(?:<a href="([^"]+)">(.*?)</a>)?.*?'
    r'<div class="date">\s*([\d/ ]+)</div>',
    re.S,
)


def ptt_search(board: str, query: str) -> list[dict]:
    q = urllib.parse.quote(query)
    url = f"https://www.ptt.cc/bbs/{board}/search?q={q}"
    page = fetch(url, headers={"Cookie": "over18=1"})
    out = []
    for href, title, mmdd in PTT_ENTRY.findall(page):
        if not href:
            continue  # 已刪除文章
        # 文章網址內含 unix timestamp：M.1700000000.A.XXX.html
        m = re.search(r"M\.(\d{9,10})\.", href)
        date = dt.datetime.fromtimestamp(int(m.group(1)), TW) if m else None
        out.append(
            {
                "title": strip_tags(title),
                "url": "https://www.ptt.cc" + href,
                "summary": "",
                "date": date,
                "source": f"PTT {board}",
            }
        )
    return out


def dcard_search(query: str) -> list[dict]:
    q = urllib.parse.quote(query)
    url = f"https://www.dcard.tw/service/api/v2/search/posts?query={q}&limit=30"
    data = json.loads(fetch(url, headers={"Accept": "application/json"}))
    out = []
    for p in data:
        out.append(
            {
                "title": p.get("title", ""),
                "url": f"https://www.dcard.tw/f/{p.get('forumAlias', 'all')}/p/{p.get('id')}",
                "summary": (p.get("excerpt") or "")[:200],
                "date": parse_date(p.get("createdAt")),
                "source": f"Dcard {p.get('forumName', '')}".strip(),
            }
        )
    return out


SOURCES = [
    ("Google 新聞：到貨", lambda: google_news("戰鬥陀螺 到貨")),
    ("Google 新聞：補貨", lambda: google_news("戰鬥陀螺 補貨 OR 開賣 OR 現貨")),
    ("Google 新聞：台北新北", lambda: google_news("戰鬥陀螺 台北 OR 新北")),
    ("Google 新聞：BEYBLADE X", lambda: google_news("BEYBLADE X 台灣")),
    ("Bing 新聞", lambda: bing_news("戰鬥陀螺 到貨")),
    ("玩具人 toy-people", lambda: wordpress_search("https://www.toy-people.com", "戰鬥陀螺", "玩具人")),
    ("PTT Toy", lambda: ptt_search("Toy", "陀螺")),
    ("PTT Beyblade", lambda: ptt_search("Beyblade", "到貨")),
    ("PTT BabyMother", lambda: ptt_search("BabyMother", "陀螺")),
    ("PTT Lifeismoney", lambda: ptt_search("Lifeismoney", "陀螺")),
    ("PTT Gossiping", lambda: ptt_search("Gossiping", "戰鬥陀螺")),
    ("Dcard", lambda: dcard_search("戰鬥陀螺 到貨")),
    ("Dcard 補貨", lambda: dcard_search("戰鬥陀螺 補貨")),
]


# ---- 整理 ------------------------------------------------------------------


def classify(item: dict) -> dict | None:
    text = f"{item['title']} {item['summary']}"
    if not has_any(text, BEY_WORDS):
        return None
    # 單獨的「陀螺」容易誤判，要再搭配其他線索
    if not has_any(text, [w for w in BEY_WORDS if w != "陀螺"]) and not has_any(
        text, ARRIVAL_WORDS
    ):
        return None
    tp, ntp = has_any(text, TAIPEI), has_any(text, NEW_TAIPEI)
    region = []
    if tp:
        region.append("台北")
    if ntp:
        region.append("新北")
    item["region"] = region
    item["arrival"] = has_any(text, ARRIVAL_WORDS)
    item["stores"] = has_any(text, STORES)
    item["score"] = 2 * bool(region) + 2 * bool(item["arrival"]) + bool(item["stores"])
    return item


def key_of(item: dict) -> str:
    norm = re.sub(r"\W+", "", item["title"].lower())[:60]
    return hashlib.sha1(norm.encode()).hexdigest()[:16]


def load_history() -> dict[str, dict]:
    if DATA_FILE.exists():
        return {i["id"]: i for i in json.loads(DATA_FILE.read_text("utf-8"))["items"]}
    return {}


def main() -> int:
    history = load_history()
    status = []
    new_count = 0
    for name, fn in SOURCES:
        try:
            raw = fn()
        except Exception as e:  # noqa: BLE001 - 任何來源失敗都只記錄
            status.append({"name": name, "ok": False, "count": 0, "error": str(e)[:120]})
            print(f"[x] {name}: {e}", file=sys.stderr)
            continue
        kept = 0
        for it in raw:
            it = classify(it)
            if not it:
                continue
            if it["date"] and it["date"] < NOW - dt.timedelta(days=KEEP_DAYS):
                continue
            kept += 1
            k = key_of(it)
            date = (it["date"] or NOW).isoformat(timespec="minutes")
            if k in history:
                continue
            new_count += 1
            history[k] = {
                "id": k,
                "title": it["title"],
                "url": it["url"],
                "summary": it["summary"],
                "source": it["source"],
                "date": date,
                "first_seen": NOW.isoformat(timespec="minutes"),
                "region": it["region"],
                "arrival": it["arrival"],
                "stores": it["stores"],
                "score": it["score"],
            }
        status.append({"name": name, "ok": True, "count": kept})
        print(f"[ok] {name}: {len(raw)} 筆，相關 {kept} 筆")

    cutoff = (NOW - dt.timedelta(days=KEEP_DAYS)).isoformat()
    items = [i for i in history.values() if i["date"] >= cutoff]
    items.sort(key=lambda i: i["date"], reverse=True)

    payload = {
        "updated": NOW.isoformat(timespec="minutes"),
        "new_today": new_count,
        "sources": status,
        "items": items,
    }
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    DATA_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=1), "utf-8")
    DOCS.mkdir(exist_ok=True)
    (DOCS / "data.json").write_text(json.dumps(payload, ensure_ascii=False), "utf-8")
    write_markdown(payload)
    print(f"共 {len(items)} 筆，今日新增 {new_count} 筆")
    return 0


def write_markdown(payload: dict) -> None:
    """備用：在 GitHub App 直接打開 LATEST.md 也能看。"""
    lines = [
        "# 戰鬥陀螺到貨情報（台北／新北）",
        "",
        f"更新時間：{payload['updated']}　今日新增 {payload['new_today']} 筆",
        "",
    ]
    local = [i for i in payload["items"] if i["region"]]
    other = [i for i in payload["items"] if not i["region"]]
    for heading, group in (("## 📍 台北／新北相關", local), ("## 🌀 其他到貨／販售消息", other)):
        lines += [heading, ""]
        if not group:
            lines += ["（目前沒有）", ""]
        for i in group[:40]:
            tags = " ".join(f"`{t}`" for t in i["region"] + i["stores"])
            lines.append(f"- {i['date'][:10]} [{i['title']}]({i['url']}) — {i['source']} {tags}")
        lines.append("")
    (ROOT / "LATEST.md").write_text("\n".join(lines), "utf-8")


if __name__ == "__main__":
    sys.exit(main())
