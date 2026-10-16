"""網店庫存監控：直接讀店家網店的公開商品資料，有補貨或新上架就產生一則消息。

目前支援 CYBERBIZ（台灣很多小店、文具店用的網店平台），商品分類網址後面加 /products.json
就能拿到每個商品的庫存。在 shops.json 的店家加上：
    "store": {"type": "cyberbiz", "url": "https://店家網址/collections/分類"}
"""

from __future__ import annotations

import hashlib
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


PLATFORMS = {"cyberbiz": cyberbiz_products}


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
            found = PLATFORMS[store["type"]](store["url"], fetch)
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
            p["first_seen"] = old["first_seen"] if old else stamp
            p["restocked_at"] = old.get("restocked_at") if old else None
            kind = None
            if old is None and known_shop:
                kind = "新上架"
            elif old is not None and not old["in_stock"] and p["in_stock"]:
                kind = "補貨"
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
    # 有貨的排前面，各自再依最近補貨／上架時間由新到舊
    products.sort(key=lambda p: p["restocked_at"] or p["first_seen"] or "", reverse=True)
    products.sort(key=lambda p: not p["in_stock"])
    return products, events, status, changed


def _event(p: dict, shop: dict, kind: str, now) -> dict:
    stamp = now.isoformat(timespec="minutes")
    price = f"（${p['price']:g}）" if p.get("price") else ""
    if not p["in_stock"]:
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
