"""網店庫存監控：直接讀店家網店的公開商品資料，有補貨或新上架就產生一則消息。

支援的平台：
- cyberbiz：台灣很多小店、文具店用的網店平台，商品分類網址後面加 /products.json 就有每個商品的庫存。
    "store": {"type": "cyberbiz", "url": "https://店家網址/collections/分類"}
- funbox：Funbox 官網（麗嬰國際直營，也是 CYBERBIZ，但分類網址是 /categories/...），
  /category_products/分類.json 有整個分類（含子分類）的庫存和開賣時間。
    "store": {"type": "funbox", "url": "https://shop.funbox.com.tw/categories/XI/KB"}
- pchome：PChome 24h 站內搜尋（多組關鍵字用「｜」分隔），只留 24h 自營、名稱符合 name_filter 的商品
  （例如只要麗嬰公司貨，排除平行輸入、黃牛賣場）。
    "store": {"type": "pchome", "url": "funbox 戰鬥陀螺｜funbox BEYBLADE", "name_filter": ["^funbox 麗嬰", "陀螺|BEYBLADE"]}
- toysrus：玩具反斗城官網（Salesforce 平台），讀商品列表上的「暫時缺貨」標示。
    "store": {"type": "toysrus", "url": "https://www.toysrus.com.tw/on/demandware.store/.../Search-ShowAjax?cgid=beyblade&sz=96"}
"""

from __future__ import annotations

import hashlib
import html
import json
import re
import urllib.parse
from pathlib import Path

STOCK_FILE = Path(__file__).resolve().parent.parent / "data" / "stock.json"
MAX_PAGES = 5
# 只有陀螺本體（有型號）才發補貨／新上架消息，收納箱、手套等周邊不發
BEY_CODE = re.compile(r"\b(?:BX|UX|CX|BXG)-?\d+", re.I)


def _quote_url(url: str) -> str:
    p = urllib.parse.urlsplit(url)
    return p._replace(path=urllib.parse.quote(urllib.parse.unquote(p.path))).geturl()


def cyberbiz_products(url: str, fetch) -> list[dict]:
    base = _quote_url(url.rstrip("/"))
    root = urllib.parse.urlsplit(base)._replace(path="", query="").geturl()
    out, seen = [], set()
    for page in range(1, MAX_PAGES + 1):
        data = json.loads(fetch(f"{base}/products.json?limit=50&page={page}"))
        new = [p for p in data if p["url"] not in seen]
        if not new:
            break
        for p in new:
            seen.add(p["url"])
            variants = p.get("variants") or []
            qty = sum(max(v.get("inventory_quantity") or 0, 0) for v in variants)
            in_stock = qty > 0 or any(v.get("inventory_policy") == "continue" for v in variants)
            out.append({
                "title": p["title"],
                "url": root + urllib.parse.quote(p["url"]),
                "price": p.get("price"),
                "qty": qty,
                "in_stock": in_stock,
            })
    return out


def _sell_from(raw: str | None) -> str | None:
    """CYBERBIZ 的開賣時間「2026-10-20 12:00:00」（台灣時間）轉成 ISO 格式。"""
    return raw.replace(" ", "T")[:16] + "+08:00" if raw else None


def funbox_products(url: str, fetch) -> list[dict]:
    """Funbox 官網：/categories/XI/KB 的商品列表放在 /category_products/XI/KB.json（含所有子分類）。"""
    root = "https://shop.funbox.com.tw"
    cat = urllib.parse.urlsplit(url).path.rstrip("/").removeprefix("/categories")
    out, seen = [], set()
    for n in range(1, MAX_PAGES * 2 + 1):
        data = json.loads(fetch(f"{root}/category_products{cat}.json?limit=50&page={n}"))
        new = [p for p in data if p["url"] not in seen]
        if not new:
            break
        for p in new:
            seen.add(p["url"])
            variants = p.get("variants") or []
            qty = sum(max(v.get("inventory_quantity") or 0, 0) for v in variants)
            price = p.get("price")
            out.append({
                "title": p["title"],
                "url": root + urllib.parse.quote(p["url"]),
                "price": price if price and price < 99999 else None,  # APP 兌換券標 999999
                "qty": qty,
                "in_stock": qty > 0 and p.get("sales_mode_purchasable", True),
                "sell_from": _sell_from(p.get("sell_from")),
            })
    return out


def pchome_products(queries: str, fetch, name_filter: str | list[str] = "",
                    ids: list[dict] | None = None, known: dict[str, str] | None = None) -> list[dict]:
    """PChome 24h：用站內搜尋找商品（分類列表沒收麗嬰的商品），只留 24h 自營、名稱符合 name_filter 的，
    再用 button API 查庫存與販售狀態。queries 用「｜」分隔多組關鍵字；name_filter 可以是多個條件（全部都要符合）。"""
    filters = [re.compile(f, re.I) for f in ([name_filter] if isinstance(name_filter, str) else name_filter) if f]
    # 搜尋只會列出「現在買得到」的商品，尚未開賣／售完的搜不到，
    # 所以設定檔裡的商品編號（ids）和以前看過的商品（known）都直接查販售狀態
    prods: dict[str, str] = {i["id"]: i["name"] for i in ids or []}
    prods.update(known or {})
    for q in queries.split("｜"):
        for n in range(1, 11):
            params = urllib.parse.urlencode({"q": q, "page": n})
            data = json.loads(fetch(f"https://ecshweb.pchome.com.tw/search/v4.3/all/results?{params}"))
            for p in data.get("Prods") or []:
                if p.get("isPChome") == 1 and all(f.search(p["Name"]) for f in filters):
                    prods[p["Id"]] = p["Name"]
            if n >= (data.get("TotalPage") or 1):
                break
    out = []
    ids = list(prods)
    for i in range(0, len(ids), 20):
        batch = ids[i:i + 20]
        raw = fetch("https://ecapi.pchome.com.tw/ecshop/prodapi/v2/prod/button&id="
                    + ",".join(f"{x}-000" for x in batch) + "&fields=Id,Price,Qty,ButtonType")
        buttons = {b["Id"].rsplit("-", 1)[0]: b for b in json.loads(raw)}
        for pid in batch:
            b = buttons.get(pid, {})
            out.append({
                "title": prods[pid],
                "url": f"https://24h.pchome.com.tw/prod/{pid}",
                "price": (b.get("Price") or {}).get("P"),
                "qty": b.get("Qty") or 0,
                # ForSale＝可購買；NotReady＝尚未開賣；SoldOut＝售完
                "in_stock": b.get("ButtonType") == "ForSale",
            })
    return out


def toysrus_products(url: str, fetch) -> list[dict]:
    page = fetch(url)
    root = "https://www.toysrus.com.tw"
    out = []
    # 每個商品是一個 product-tile，裡面有 data-metadata（名稱、價格）和「暫時缺貨」狀態
    for tile in page.split('class="card product-tile')[1:]:
        tile = tile.split("END_dwmarker")[0]
        meta = re.search(r"data-metadata='([^']*)'", tile)
        href = re.search(r'href="(/zh-tw/[^"]+\.html)"', tile)
        if not meta or not href:
            continue
        m = json.loads(html.unescape(meta.group(1)))
        status = re.search(r'<div class="status">\s*([^<]*?)\s*</div>', tile)
        status = status.group(1) if status else ""
        out.append({
            "title": m.get("name_local") or m.get("name", ""),
            "url": root + href.group(1),
            "price": float(m["price"]) if m.get("price") else None,
            "qty": 0,
            "in_stock": not re.search(r"缺貨|售完", status),
        })
    return out


PLATFORMS = {
    "cyberbiz": cyberbiz_products,
    "funbox": funbox_products,
    "toysrus": toysrus_products,
    "pchome": lambda url, fetch, **kw: pchome_products(url, fetch, **kw),
}


def load_state() -> dict[str, dict]:
    if STOCK_FILE.exists():
        return {p["url"]: p for p in json.loads(STOCK_FILE.read_text("utf-8"))}
    return {}


def check_stores(shops: list[dict], fetch, now) -> tuple[list[dict], list[dict], list[dict], bool]:
    """回傳 (目前所有商品, 補貨／新上架消息, 來源狀態, 庫存有沒有變)。"""
    state = load_state()
    stamp = now.isoformat(timespec="minutes")
    products, events, status = [], [], []
    for shop in shops:
        store = shop.get("store")
        if not store:
            continue
        name = f"網店庫存：{shop['name']}"
        try:
            extra = {k: store[k] for k in ("name_filter", "ids") if store.get(k)}
            if store["type"] == "pchome":
                extra["known"] = {p["url"].rsplit("/", 1)[1]: p["title"]
                                  for p in state.values() if p["shop"] == shop["name"]}
            found = PLATFORMS[store["type"]](store["url"], fetch, **extra)
        except Exception as e:  # noqa: BLE001 - 網店掛掉不影響其他來源
            status.append({"name": name, "ok": False, "count": 0, "error": str(e)[:120]})
            # 抓不到就沿用上次的資料，不要讓整家店從清單消失
            products += [p for p in state.values() if p["shop"] == shop["name"]]
            continue
        # 第一次看到這家店時不發消息，不然整個分類都會變成「新上架」
        known_shop = any(p["shop"] == shop["name"] for p in state.values())
        for p in found:
            old = state.get(p["url"])
            p["shop"] = shop["name"]
            # 還沒到開賣時間的商品就算有庫存也買不到
            p["upcoming"] = bool(p.get("sell_from") and p["sell_from"] > stamp)
            if p["upcoming"]:
                p["in_stock"] = False
            p["first_seen"] = old["first_seen"] if old else stamp
            p["restocked_at"] = old.get("restocked_at") if old else None
            kind = None
            if old is None and known_shop:
                kind = "新上架"
            elif old is not None and not old["in_stock"] and p["in_stock"]:
                kind = "開賣" if old.get("upcoming") else "補貨"
            if kind:
                p["restocked_at"] = stamp
                # 快閃商品常常先上架、時間到才開賣，所以新上架就算還沒貨也先通知
                if BEY_CODE.search(p["title"]):
                    events.append(_event(p, shop, kind, now))
            products.append(p)
        status.append({"name": name, "ok": True, "count": sum(p["in_stock"] for p in found)})
        print(f"[ok] {name}: {len(found)} 項商品，有貨 {sum(p['in_stock'] for p in found)} 項")

    snapshot = lambda ps: sorted((p["url"], p["in_stock"], p["qty"]) for p in ps)  # noqa: E731
    changed = snapshot(products) != snapshot(state.values())
    if changed:
        STOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
        STOCK_FILE.write_text(json.dumps(products, ensure_ascii=False, indent=1), "utf-8")
    # 有貨的排前面、其次是即將開賣，各自再依最近補貨／上架時間由新到舊
    products.sort(key=lambda p: p["restocked_at"] or p["first_seen"] or "", reverse=True)
    products.sort(key=lambda p: (not p["in_stock"], not p.get("upcoming")))
    return products, events, status, changed


def _event(p: dict, shop: dict, kind: str, now) -> dict:
    stamp = now.isoformat(timespec="minutes")
    price = f"（${p['price']:g}）" if p.get("price") else ""
    if p.get("upcoming"):
        price += f"　⏰ {p['sell_from'][5:16].replace('-', '/').replace('T', ' ')} 開賣"
    elif not p["in_stock"]:
        price += "　目前還沒開賣／已售完"
    return {
        "id": hashlib.sha1(f"{p['url']}|{kind}|{stamp}".encode()).hexdigest()[:16],
        "title": f"【{shop['name']}網店{kind}】{p['title']}{price}",
        "url": p["url"],
        "summary": "",
        "source": f"網店｜{shop['name']}",
        "social": False,
        "date": stamp,
        "first_seen": stamp,
        "region": [shop["city"]] if shop.get("city") else [],
        "arrival": [kind],
        "stores": [],
        "resale": False,
        "event": False,
        "shop": shop["name"],
        "branch": None,
        "score": 4,
        "kind": "stock",
    }
