"""每天收集全台戰鬥陀螺 (BEYBLADE) 到貨相關資訊，輸出成靜態網頁。

只用 Python 標準函式庫，GitHub Actions 上不需要安裝套件。
每個來源各自 try/except，某個網站掛掉不會影響其他來源。
"""

from __future__ import annotations

import datetime as dt
import difflib
import email.utils
import hashlib
import html
import json
import os
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
# 縣市 → 判斷用關鍵字（縣市名＋較不會跟其他縣市撞名的行政區／商圈）。
# 中山、中正、大同、信義、大安、東區、北區…這類多個縣市都有的名稱刻意不列。
REGIONS: dict[str, list[str]] = {
    "基隆": ["基隆", "七堵", "暖暖"],
    "台北": ["台北", "臺北", "北市", "松山", "萬華", "士林", "北投", "內湖", "南港", "文山",
             "西門", "地下街", "京站", "南西", "公館", "天母"],
    "新北": ["新北", "板橋", "三重", "中和", "永和", "新莊", "新店", "土城", "蘆洲", "樹林",
             "汐止", "鶯歌", "三峽", "淡水", "林口", "五股", "泰山", "深坑", "八里", "瑞芳",
             "宏匯"],
    "桃園": ["桃園", "中壢", "平鎮", "八德", "楊梅", "蘆竹", "龜山", "龍潭", "大園", "青埔", "華泰名品"],
    "新竹": ["新竹", "竹北", "竹東", "湖口", "巨城"],
    "苗栗": ["苗栗", "頭份", "竹南"],
    "台中": ["台中", "臺中", "豐原", "大里", "太平", "沙鹿", "清水", "烏日", "逢甲", "一中街", "大甲"],
    "彰化": ["彰化", "員林", "鹿港", "和美"],
    "南投": ["南投", "草屯", "埔里"],
    "雲林": ["雲林", "斗六", "虎尾"],
    "嘉義": ["嘉義", "民雄"],
    "台南": ["台南", "臺南", "永康", "新營", "安平", "歸仁", "仁德", "南紡"],
    "高雄": ["高雄", "鳳山", "左營", "楠梓", "三民", "苓雅", "前鎮", "岡山", "夢時代", "巨蛋"],
    "屏東": ["屏東", "潮州", "東港"],
    "宜蘭": ["宜蘭", "羅東", "礁溪"],
    "花蓮": ["花蓮", "吉安"],
    "台東": ["台東", "臺東"],
    "澎湖": ["澎湖", "馬公"],
    "金門": ["金門"],
    "連江": ["馬祖", "連江"],
}
# 全台連鎖通路，標示出來方便判斷
STORES = [
    "玩具反斗城", "Toysrus", "Funbox", "麗嬰", "家樂福", "全聯", "大買家", "好市多",
    "Costco", "誠品", "金石堂", "7-11", "7-ELEVEN", "統一超商", "全家", "萊爾富", "Hi-Life",
    "OK超商", "小北百貨", "momo", "PChome", "蝦皮",
    "博客來", "TAKARA", "Animate", "安利美特", "湯姆熊", "大魯閣",
]


# 出現這些字就不收：香港／外地貼文、不是戰鬥陀螺的商品、「本店沒有陀螺」公告
EXCLUDE_WORDS = [
    "香港", "銅鑼灣", "旺角", "尖沙咀", "啟德", "嚟", "冇", "馬來西亞",
    "指尖陀螺", "奶粉", "沒有戰鬥陀螺", "無戰鬥陀螺",
]
# 個人二手／轉售（不是店家到貨）：保留但另外標記
RESALE = re.compile(r"出售|【售】|^售|售售|出清|面交|賣貨便|myship|二手|轉售|徵收|收購")
PRODUCT_CODE = re.compile(r"\b(?:BX|UX|CX)-\d+", re.I)


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
        if title.endswith(f" - {src}"):
            title = title[: -len(src) - 3]
        if desc.startswith(title):  # Google 新聞的摘要只是標題＋媒體名
            desc = ""
        out.append(
            {"title": title, "url": link, "summary": desc[:200], "date": pub, "source": src}
        )
    return out


def google_news(query: str) -> list[dict]:
    q = urllib.parse.quote(f"{query} when:14d")
    url = f"https://news.google.com/rss/search?q={q}&hl=zh-TW&gl=TW&ceid=TW:zh-Hant"
    return rss_items(fetch(url), "Google 新聞")


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


class NotConfigured(Exception):
    """需要金鑰的來源還沒設定：顯示「未設定」，不算失敗。"""


def youtube_search(query: str) -> list[dict]:
    """YouTube Data API v3（免費金鑰，每次搜尋耗 100 單位，每天額度約 10,000）。"""
    key = os.environ.get("YOUTUBE_API_KEY")
    if not key:
        raise NotConfigured("未設定 YOUTUBE_API_KEY")
    after = (NOW - dt.timedelta(days=14)).astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    params = urllib.parse.urlencode({
        "part": "snippet", "type": "video", "order": "date", "maxResults": 25,
        "q": query, "regionCode": "TW", "relevanceLanguage": "zh-Hant",
        "publishedAfter": after, "key": key,
    })
    data = json.loads(fetch(f"https://www.googleapis.com/youtube/v3/search?{params}"))
    out = []
    for v in data.get("items", []):
        sn = v["snippet"]
        out.append({
            "title": html.unescape(sn["title"]),
            "url": f"https://www.youtube.com/watch?v={v['id']['videoId']}",
            "summary": html.unescape(sn.get("description", ""))[:200],
            "date": parse_date(sn.get("publishedAt")),
            "source": f"YouTube {sn.get('channelTitle', '')}".strip(),
        })
    return out


def threads_search(query: str) -> list[dict]:
    """Threads 官方 API 關鍵字搜尋。

    注意：Meta 規定 App 未通過 threads_keyword_search 審核前，只會搜到「自己帳號」的貼文；
    通過審核後才搜得到公開貼文。長期權杖 60 天到期，需要重新產生後更新 Secret。
    """
    token = os.environ.get("THREADS_ACCESS_TOKEN")
    if not token:
        raise NotConfigured("未設定 THREADS_ACCESS_TOKEN")
    params = urllib.parse.urlencode({
        "q": query, "search_type": "RECENT",
        "fields": "id,text,permalink,timestamp,username", "access_token": token,
    })
    data = json.loads(fetch(f"https://graph.threads.net/v1.0/keyword_search?{params}"))
    out = []
    for p in data.get("data", []):
        text = p.get("text") or ""
        out.append({
            "title": text.split("\n")[0][:80] or "(Threads 貼文)",
            "url": p.get("permalink", ""),
            "summary": text[:200],
            "date": parse_date(p.get("timestamp")),
            "source": f"Threads @{p.get('username', '')}",
        })
    return out


SOCIAL_PREFIXES = ("PTT", "YouTube", "Threads", "Facebook")

SOURCES = [
    ("Google 新聞：到貨", lambda: google_news("戰鬥陀螺 到貨")),
    ("Google 新聞：補貨", lambda: google_news("戰鬥陀螺 補貨 OR 開賣 OR 現貨")),
    ("Google 新聞：台北新北", lambda: google_news("戰鬥陀螺 台北 OR 新北")),
    ("Google 新聞：中南部", lambda: google_news("戰鬥陀螺 台中 OR 台南 OR 高雄")),
    ("Google 新聞：門市", lambda: google_news("戰鬥陀螺 門市 OR 玩具反斗城")),
    ("Google 新聞：超商", lambda: google_news("戰鬥陀螺 7-ELEVEN OR 全家 OR 萊爾富")),
    ("Google 新聞：BEYBLADE X", lambda: google_news("BEYBLADE X 台灣")),
    # Facebook 不能直接爬；Google 新聞有收錄部分公開粉專貼文，從這裡間接拿
    ("Facebook 公開貼文：戰鬥陀螺", lambda: google_news("戰鬥陀螺 site:facebook.com")),
    ("Facebook 公開貼文：到貨", lambda: google_news("陀螺 到貨 OR 補貨 OR 現貨 site:facebook.com")),
    ("PTT Toy", lambda: ptt_search("Toy", "陀螺")),
    ("PTT BabyMother", lambda: ptt_search("BabyMother", "陀螺")),
    ("PTT Lifeismoney", lambda: ptt_search("Lifeismoney", "陀螺")),
    ("PTT Gossiping", lambda: ptt_search("Gossiping", "戰鬥陀螺")),
    ("YouTube：到貨", lambda: youtube_search("戰鬥陀螺 到貨|補貨|開箱")),
    ("YouTube：BEYBLADE X", lambda: youtube_search("BEYBLADE X 台灣")),
    ("Threads：戰鬥陀螺", lambda: threads_search("戰鬥陀螺")),
    ("Threads：到貨", lambda: threads_search("陀螺 到貨")),
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
    if has_any(text, EXCLUDE_WORDS):
        return None
    arrival, stores = has_any(text, ARRIVAL_WORDS), has_any(text, STORES)
    # 只提到陀螺的新聞（政治人物、明星比賽…）不算；要有到貨字眼、通路或型號
    if not arrival and not stores and not PRODUCT_CODE.search(text):
        return None
    region = [city for city, words in REGIONS.items() if has_any(text, words)]
    item["region"] = region
    item["arrival"] = arrival
    item["stores"] = stores
    item["resale"] = bool(RESALE.search(item["title"]))
    item["score"] = 2 * bool(region) + 2 * bool(item["arrival"]) + bool(item["stores"])
    return item


def key_of(item: dict) -> str:
    norm = re.sub(r"\W+", "", item["title"].lower())[:60]
    return hashlib.sha1(norm.encode()).hexdigest()[:16]


def load_history() -> dict[str, dict]:
    if DATA_FILE.exists():
        return {i["id"]: i for i in json.loads(DATA_FILE.read_text("utf-8"))["items"]}
    return {}


def reclassify(items) -> list[dict]:
    """關鍵字清單改過後，舊資料也照新規則重算（不符合的直接剔除）。"""
    out = []
    for i in items:
        if i["title"].endswith(f" - {i['source']}"):
            i["title"] = i["title"][: -len(i["source"]) - 3]
        if i["summary"].startswith(i["title"]):
            i["summary"] = ""
        c = classify({"title": i["title"], "summary": i["summary"]})
        if c:
            i.update(region=c["region"], arrival=c["arrival"], stores=c["stores"], score=c["score"],
                     resale=c["resale"],
                     social=i["source"].startswith(SOCIAL_PREFIXES))
            out.append(i)
    return out


def _norm(title: str) -> str:
    return re.sub(r"\W+", "", title.lower())


def _codes(i: dict) -> set[str]:
    return {c.upper() for c in PRODUCT_CODE.findall(i["title"] + " " + i["summary"])}


def same_story(a: dict, b: dict) -> bool:
    """標題很像；或同一通路、型號不衝突（各家媒體下標差很多，只比文字抓不到）。"""
    if difflib.SequenceMatcher(None, _norm(a["title"]), _norm(b["title"])).ratio() >= 0.5:
        return True
    if a["stores"] and set(a["stores"]) == set(b["stores"]):
        ca, cb = _codes(a), _codes(b)
        return not (ca and cb) or bool(ca & cb)
    return False


def merge_similar(items: list[dict]) -> list[dict]:
    """同一則消息被多家媒體轉載時只留一則（最早看到的），其餘記在 also。"""
    items = sorted(items, key=lambda i: (i["first_seen"], i["date"]))
    kept: list[dict] = []
    for i in items:
        day = dt.datetime.fromisoformat(i["date"])
        for k in kept:
            if abs(dt.datetime.fromisoformat(k["date"]) - day) > dt.timedelta(days=3):
                continue
            if same_story(k, i):
                also = k.setdefault("also", [])
                for o in [{"source": i["source"], "url": i["url"]}, *i.get("also", [])]:
                    if o["url"] not in {a["url"] for a in also} and o["url"] != k["url"]:
                        also.append(o)
                break
        else:
            kept.append(i)
    return kept


def redact(msg: str) -> str:
    """錯誤訊息會公開在網頁上，先把金鑰遮掉。"""
    for var in ("YOUTUBE_API_KEY", "THREADS_ACCESS_TOKEN"):
        if os.environ.get(var):
            msg = msg.replace(os.environ[var], "***")
    return msg


def main() -> int:
    history = load_history()
    status = []
    new_count = 0
    for name, fn in SOURCES:
        try:
            raw = fn()
        except NotConfigured as e:
            status.append({"name": name, "ok": None, "count": 0, "error": str(e)})
            print(f"[-] {name}: {e}")
            continue
        except Exception as e:  # noqa: BLE001 - 任何來源失敗都只記錄
            status.append({"name": name, "ok": False, "count": 0, "error": redact(str(e))[:120]})
            print(f"[x] {name}: {redact(str(e))}", file=sys.stderr)
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
                "social": it["source"].startswith(SOCIAL_PREFIXES),
                "date": date,
                "first_seen": NOW.isoformat(timespec="minutes"),
                "region": it["region"],
                "arrival": it["arrival"],
                "stores": it["stores"],
                "resale": it["resale"],
                "score": it["score"],
            }
        status.append({"name": name, "ok": True, "count": kept})
        print(f"[ok] {name}: {len(raw)} 筆，相關 {kept} 筆")

    cutoff = (NOW - dt.timedelta(days=KEEP_DAYS)).isoformat()
    items = merge_similar(reclassify(i for i in history.values() if i["date"] >= cutoff))
    items.sort(key=lambda i: i["date"], reverse=True)
    # 被合併掉的轉載不算新消息
    new_count = sum(i["first_seen"] == NOW.isoformat(timespec="minutes") for i in items)

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
    """備用：在 GitHub App 直接打開 LATEST.md 也能看（依縣市分組）。"""
    lines = [
        "# 戰鬥陀螺到貨情報（全台）",
        "",
        f"更新時間：{payload['updated']}　今日新增 {payload['new_today']} 筆",
        "",
    ]
    groups = [(f"## 📍 {city}", [i for i in payload["items"] if city in i["region"]])
              for city in REGIONS]
    groups.append(("## 🌀 未標示地區", [i for i in payload["items"] if not i["region"]]))
    for heading, group in groups:
        if not group:
            continue
        lines += [heading, ""]
        for i in group[:30]:
            tags = " ".join(f"`{t}`" for t in i["stores"])
            lines.append(f"- {i['date'][:10]} [{i['title']}]({i['url']}) — {i['source']} {tags}")
        lines.append("")
    (ROOT / "LATEST.md").write_text("\n".join(lines), "utf-8")

if __name__ == "__main__":
    sys.exit(main())
