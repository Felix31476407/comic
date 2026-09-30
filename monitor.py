import json
import os
import time
from datetime import date
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# ============ 配置区（只改这里）============

# 贴吧：要盯的吧名（不带"吧"字），请换成你实际常看的二手漫画吧
TIEBA_FORUMS = ["漫画买卖", "漫画交易"]

# 贴吧标题里出现任一关键词就推送（不区分大小写）
TIEBA_KEYWORDS = [
    "火影忍者", "火影", "naruto",
    "死神", "bleach",
    "魔导少年", "魔導少年", "fairy tail",
    "七龙珠", "七龍珠", "dragon ball",
]

# 雅虎拍卖日本：直接用日文关键词搜索，按最新上架排序
YAHOO_KEYWORDS = [
    "NARUTO ナルト 全巻",
    "BLEACH ブリーチ 全巻",
    "FAIRY TAIL フェアリーテイル 全巻",
    "ドラゴンボール 全巻",
    "NARUTO ナルト コンビニ コミック",
    "BLEACH ブリーチ コンビニ コミック",
    "FAIRY TAIL フェアリーテイル コンビニ コミック",
    "ドラゴンボール コンビニ コミック",
    "NARUTO ナルト リミックス コミック",
    "BLEACH ブリーチ リミックス コミック",
    "FAIRY TAIL フェアリーテイル リミックス コミック",
    "ドラゴンボール リミックス コミック",
]

MAX_PUSH_PER_RUN = 20  # 单次最多推送条数，防止刷屏

# ============ 以下不用改 ============

STATE_FILE = Path("state.json")
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9,ja;q=0.8,en;q=0.7",
}
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")


def send(text):
    if not TOKEN or not CHAT_ID:
        print("[未配置 Telegram]", text)
        return
    r = requests.post(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        data={"chat_id": CHAT_ID, "text": text},
        timeout=20,
    )
    if r.status_code != 200:
        print("Telegram 发送失败:", r.status_code, r.text[:200])


def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8")), False
    return {"seen": [], "warned": {}}, True


def save_state(state):
    state["seen"] = state["seen"][-5000:]
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8"
    )


def fetch_tieba():
    """返回 [(唯一ID, 标题, 链接)]"""
    items = []
    for forum in TIEBA_FORUMS:
        r = requests.get(
            "https://tieba.baidu.com/f",
            params={"kw": forum, "ie": "utf-8"},
            headers=HEADERS,
            timeout=30,
        )
        html = r.text
        if r.status_code != 200 or "安全验证" in html:
            raise RuntimeError(f"贴吧「{forum}」被拦截或无法访问 (HTTP {r.status_code})")
        # 贴吧把部分帖子列表放在 HTML 注释里，先去掉注释符号
        html = html.replace("<!--", "").replace("-->", "")
        soup = BeautifulSoup(html, "html.parser")
        found = set()
        for a in soup.select('a[href^="/p/"]'):
            title = (a.get("title") or a.get_text()).strip()
            href = a["href"].split("?")[0]
            if not title or href in found:
                continue
            found.add(href)
            low = title.lower()
            if any(k.lower() in low for k in TIEBA_KEYWORDS):
                items.append((f"tieba:{href}", title, "https://tieba.baidu.com" + href))
    return items


def fetch_yahoo():
    items = []
    for kw in YAHOO_KEYWORDS:
        r = requests.get(
            "https://auctions.yahoo.co.jp/search/search",
            params={"p": kw, "s1": "new", "o1": "d"},
            headers=HEADERS,
            timeout=30,
        )
        if r.status_code != 200:
            raise RuntimeError(f"雅虎拍卖无法访问 (HTTP {r.status_code})")
        soup = BeautifulSoup(r.text, "html.parser")
        found = set()
        for a in soup.select('a[href*="/jp/auction/"]'):
            href = a["href"].split("?")[0]
            aid = href.rstrip("/").split("/")[-1]
            if aid in found:
                continue
            title = a.get_text(strip=True)
            if not title:
                img = a.find("img")
                title = (img.get("alt") if img else "") or ""
            if not title:
                continue
            found.add(aid)
            items.append((f"yahoo:{aid}", title, href))
        time.sleep(2)
    return items


def main():
    state, first_run = load_state()
    seen = set(state["seen"])
    today = str(date.today())
    new_items = []

    for name, fn in [("贴吧", fetch_tieba), ("雅虎拍卖", fetch_yahoo)]:
        try:
            for uid, title, link in fn():
                if uid not in seen:
                    seen.add(uid)
                    state["seen"].append(uid)
                    new_items.append((name, title, link))
        except Exception as e:
            print(f"[{name}] 出错: {e}")
            if state["warned"].get(name) != today:
                state["warned"][name] = today
                send(f"⚠️ {name} 抓取失败：{e}")

    if first_run:
        send(f"✅ 监控已启动，已记录 {len(new_items)} 条现有帖子，之后只推送新的。")
    else:
        for name, title, link in new_items[:MAX_PUSH_PER_RUN]:
            send(f"【{name}】{title}\n{link}")
            time.sleep(1)
        extra = len(new_items) - MAX_PUSH_PER_RUN
        if extra > 0:
            send(f"另有 {extra} 条新帖未逐条推送。")

    save_state(state)
    print(f"完成，新增 {len(new_items)} 条")


if __name__ == "__main__":
    main()
