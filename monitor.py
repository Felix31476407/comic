import json
import os
import re
import time
from datetime import date
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# ============ 配置区（只改这里）============

# 贴吧：要盯的吧名（不带"吧"字）
TIEBA_FORUMS = ["漫画买卖", "漫画交易"]

# 贴吧标题里出现任一关键词就推送（不区分大小写）
TIEBA_KEYWORDS = [
    "火影忍者", "火影", "naruto",
    "死神", "bleach",
    "魔导少年", "魔導少年", "fairy tail",
    "七龙珠", "七龍珠", "dragon ball",
]

# 雅虎拍卖日本：搜索词（按最新上架排序）
YAHOO_KEYWORDS = [
    "NARUTO ナルト 全巻",
    "BLEACH ブリーチ 全巻",
    "FAIRY TAIL フェアリーテイル 全巻",
    "ドラゴンボール 全巻",
    "NARUTO ナルト コンビニ コミック セット",
    "BLEACH ブリーチ コンビニ コミック セット",
    "FAIRY TAIL フェアリーテイル コンビニ コミック セット",
    "ドラゴンボール コンビニ コミック セット",
    "NARUTO ナルト リミックス コミック セット",
    "BLEACH ブリーチ リミックス コミック セット",
    "FAIRY TAIL フェアリーテイル リミックス コミック セット",
    "ドラゴンボール リミックス コミック セット",
]

# 雅虎标题过滤：必须提到你要的作品
YAHOO_SERIES_RE = r"NARUTO|ナルト|BLEACH|ブリーチ|FAIRY\s*TAIL|フェアリーテイル|ドラゴンボール|DRAGON\s*BALL"
# 雅虎标题过滤：必须像"整套"（全巻、セット、まとめ、1-72巻 等）
YAHOO_SET_RE = r"全\s*\d*\s*巻|セット|まとめ|一括|完結|\d+\s*[-~〜～]\s*\d+\s*巻?"
# 雅虎标题过滤：出现这些词就丢掉（周边、影像、游戏等）
YAHOO_EXCLUDE = [
    "フィギュア", "DVD", "Blu-ray", "ブルーレイ", "ゲーム", "カード",
    "ポスター", "食玩", "ガシャ", "プラモ", "キーホルダー", "ストラップ",
    "Tシャツ", "小説", "画集", "サントラ", "CD", "ぬいぐるみ", "缶バッジ",
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


def yahoo_title_ok(title):
    if not re.search(YAHOO_SERIES_RE, title, re.I):
        return False
    if not re.search(YAHOO_SET_RE, title):
        return False
    low = title.lower()
    return not any(x.lower() in low for x in YAHOO_EXCLUDE)


def get_yahoo(kw):
    """请求一个关键词，遇到 5xx 或网络错误最多重试 3 次。返回 response，404 返回 None。"""
    last = ""
    for i in range(3):
        try:
            r = requests.get(
                "https://auctions.yahoo.co.jp/search/search",
                params={"p": kw, "s1": "new", "o1": "d"},
                headers=HEADERS,
                timeout=30,
            )
            if r.status_code == 404:
                return None
            if r.status_code == 200:
                return r
            last = f"HTTP {r.status_code}"
        except requests.RequestException as e:
            last = type(e).__name__
        time.sleep(3 * (i + 1))
    raise RuntimeError(last)


def fetch_yahoo():
    items = []
    failed = []
    for kw in YAHOO_KEYWORDS:
        try:
            r = get_yahoo(kw)
        except RuntimeError as e:
            print(f"失败: {kw} ({e})")
            failed.append(kw)
            time.sleep(2)
            continue
        if r is None:
            print(f"无结果: {kw}")
            time.sleep(2)
            continue
        soup = BeautifulSoup(r.text, "html.parser")

        # 同一件拍品有多个链接（图片、标题、"New!!"标签等），每件取最长的文字当标题
        best = {}
        for a in soup.select('a[href*="/jp/auction/"]'):
            href = a["href"].split("?")[0]
            aid = href.rstrip("/").split("/")[-1]
            texts = [a.get_text(strip=True)]
            for img in a.find_all("img"):
                texts.append((img.get("alt") or "").strip())
            title = max(texts, key=len)
            if aid not in best or len(title) > len(best[aid][0]):
                best[aid] = (title, href)

        for aid, (title, href) in best.items():
            if yahoo_title_ok(title):
                items.append((f"yahoo:{aid}", title, href))
        time.sleep(2)

    # 只有全部关键词都失败才算整站故障；个别失败下次运行会自动重试
    if failed and len(failed) == len(YAHOO_KEYWORDS):
        raise RuntimeError("雅虎拍卖所有关键词都无法访问")
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
