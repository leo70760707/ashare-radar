#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A股雷达 · 海外新闻 → A股板块【预测】

核心定位：
  用户在香港，能无翻墙看到海外新闻 → 比内地人更早知道国际消息
  程序抓"对中国有影响的海外新闻" → AI 预判该消息会带动哪些 A股板块、涨还是跌
  → 提前布局

不做的事：
  ✗ 不抓国内新闻（用户在香港没有这个优势）
  ✗ 不显示股价反应（滞后数据，无意义）
  ✗ 不做"事后验证"

数据源全部为境外可访问的公开 RSS。
"""

import os
import re
import json
import hashlib
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta

# ============ 配置 ============
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "https://api.deepseek.com/v1")
LLM_MODEL = os.environ.get("LLM_MODEL", "deepseek-chat")

STATE_FILE = "seen.json"
BEIJING = timezone(timedelta(hours=8))

# ============ 海外新闻源（全部境外可直接访问）============
# 核心思路：只抓"对中国/全球有实质影响"的题材
NEWS_SOURCES = [
    # —— 对华直接相关（最重要）——
    ("对华制裁", "https://news.google.com/rss/search?q=when:12h+China+sanctions+OR+export+controls+OR+entity+list&hl=en-US&gl=US&ceid=US:en"),
    ("中美贸易", "https://news.google.com/rss/search?q=when:12h+US+China+trade+tariff+negotiation&hl=en-US&gl=US&ceid=US:en"),
    ("芯片管制", "https://news.google.com/rss/search?q=when:12h+chip+export+ban+semiconductor+China&hl=en-US&gl=US&ceid=US:en"),
    # —— 影响大宗商品/通胀 ——
    ("原油能源", "https://news.google.com/rss/search?q=when:12h+oil+crude+OPEC+production+cut&hl=en-US&gl=US&ceid=US:en"),
    ("粮食农产品", "https://news.google.com/rss/search?q=when:12h+wheat+corn+grain+export+ban+food+price&hl=en-US&gl=US&ceid=US:en"),
    ("贵金属", "https://news.google.com/rss/search?q=when:12h+gold+copper+rare+earth+price&hl=en-US&gl=US&ceid=US:en"),
    # —— 影响A股情绪/流动性 ——
    ("美联储", "https://news.google.com/rss/search?q=when:12h+Fed+interest+rate+Powell+inflation&hl=en-US&gl=US&ceid=US:en"),
    ("美元美债", "https://news.google.com/rss/search?q=when:12h+dollar+index+Treasury+yield&hl=en-US&gl=US&ceid=US:en"),
    # —— 地缘冲突（影响油价/军工/避险）——
    ("中东局势", "https://news.google.com/rss/search?q=when:12h+Iran+Israel+Middle+East+oil+strait&hl=en-US&gl=US&ceid=US:en"),
    ("俄乌战事", "https://news.google.com/rss/search?q=when:12h+Russia+Ukraine+grain+weapons&hl=en-US&gl=US&ceid=US:en"),
    # —— 影响科技供应链 ——
    ("AI算力", "https://news.google.com/rss/search?q=when:12h+Nvidia+AI+chip+export+China+ban&hl=en-US&gl=US&ceid=US:en"),
    # —— 疫情/公共卫生（你举的鼠疫案例属于这类）——
    ("全球疫情", "https://news.google.com/rss/search?q=when:12h+outbreak+plague+epidemic+pandemic+WHO+death&hl=en-US&gl=US&ceid=US:en"),
    # —— 中国经济相关 ——
    ("中国经济", "https://news.google.com/rss/search?q=when:12h+China+economy+GDP+yuan+property&hl=en-US&gl=US&ceid=US:en"),
    # —— 英文媒体直连 ——
    ("BBC中文", "https://feeds.bbci.co.uk/zhongwen/simp/rss.xml"),
    ("纽约时报中文", "https://cn.nytimes.com/rss/"),
    ("德国之声中文", "https://rss.dw.com/rdf/rss-chi-all"),
]

# A股板块库（供AI参考）
SECTOR_HINTS = """
医药医疗：疫情、鼠疫、瘟疫、霍乱、埃博拉、病毒爆发、疫苗、新冠、流感、药品FDA、创新药
半导体芯片：芯片管制、出口管制、半导体、晶圆、光刻、GPU、算力限制、实体清单、国产替代
AI算力：AI芯片、Nvidia对华出口、大模型、GPU管制、数据中心
新能源车：电动车、锂电池、固态电池、光伏、储能、关税、特斯拉、比亚迪
军工国防：军工订单、乌克兰、俄罗斯、北约、导弹、无人机、军贸、地缘冲突
能源资源：原油、天然气、OPEC减产、油价、黄金、铜、稀土、煤炭、锂、铀
消费白酒：白酒、消费、茅台、社零、关税、消费刺激
地产基建：房地产、楼市、房贷、限购、基建、专项债、城中村、债务
金融银行：降息、降准、LPR、美联储、美元、资本流动、银行、券商
农业粮食：粮食、农业、化肥、种业、小麦、玉米、大豆、猪肉、出口禁令
有色金属：铜、铝、锂、钴、镍、稀土、出口管制
电力能源：电力、电网、核电、特高压、电力设备
"""

SYSTEM_PROMPT = f"""你是A股市场预判助手。

用户的特殊优势：他在香港，能第一时间看到海外新闻，比内地投资者早知道消息。
你的任务：判断这条海外新闻如果传到中国，**A股哪些板块会涨、哪些会跌**。

【核心要求】
1. **只判断"对中国有影响"的新闻**。美国国内政治、欧洲经济、日本琐事 → relevance=1（不发）
2. **预测涨跌方向**，不是描述已发生的涨跌
3. 板块要具体到能买 ETF 的程度

【相关性分级】
- 3强：对华直接相关，或影响中国进出口/供应链/货币政策
  （例：对华制裁、芯片管制、关税、OPEC减产、疫情爆发、美联储加息）
- 2中：影响中国大宗商品成本或避险情绪
  （例：中东冲突推高油价、美元大涨、全球通胀）
- 1弱：与中国无实质关系 → 不要发

【判断示例】
"OPEC+宣布减产" → sectors:["能源资源","石油石化"] direction:"利好" reason:"原油涨价利好油企成本收入"
"美国限制对华AI芯片出口" → sectors:["半导体芯片","AI算力"] direction:"利空" reason:"国产替代加速但短期供给受限"
"俄罗斯暴发鼠疫" → sectors:["医药医疗","生物制品"] direction:"利好" reason:"防疫需求利好原料药与检测试剂"
"美联储加息" → sectors:["金融银行","地产基建"] direction:"利空" reason:"外资流出压制估值"

【板块参考库】
{SECTOR_HINTS}

【严格输出格式】
[{{"index":1,"relevance":3,"sectors":["最多2个"],"direction":"利好","confidence":"高或中","summary":"30字内核心内容","reason":"25字内为什么影响A股"}}]

direction 只能填：利好 / 利空
sectors 最多 2 个
confidence 只能填：高 / 中
summary 不超过 30 字
reason 不超过 25 字"""


def fetch_news():
    """抓取海外新闻"""
    items = []
    for source, url in NEWS_SOURCES:
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
            )
            with urllib.request.urlopen(req, timeout=20) as resp:
                root = ET.fromstring(resp.read())
            count = 0
            for item in root.iter("item"):
                if count >= 10:
                    break
                title = (item.findtext("title") or "").strip()
                link = (item.findtext("link") or "").strip()
                pub = (item.findtext("pubDate") or "").strip()
                if title:
                    items.append({
                        "source": source,
                        "title": title,
                        "link": link,
                        "pub": pub,
                    })
                    count += 1
        except Exception as e:
            print(f"[warn] {source}: {e}")
    return items


def load_seen():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_seen(seen):
    if len(seen) > 300:
        seen = dict(list(seen.items())[-300:])
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(seen, f, ensure_ascii=False, indent=1)


def news_hash(title):
    return hashlib.md5(title.encode("utf-8")).hexdigest()[:16]


def call_llm(news_list):
    if not LLM_API_KEY:
        return []

    news_text = "\n".join(
        [f"[{i+1}] {n['title'][:110]}" for i, n in enumerate(news_list[:35])]
    )
    user_prompt = f"""以下是从海外抓到的最新新闻。请判断哪些会影响A股，预判涨跌方向。

{news_text}

每条格式：
{{"index":1,"relevance":3,"sectors":["板块"],"direction":"利好","confidence":"高","summary":"30字内","reason":"25字内"}}

只输出JSON数组。"""

    try:
        payload = json.dumps({
            "model": LLM_MODEL,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.2,
            "max_tokens": 2500,
        }).encode("utf-8")

        req = urllib.request.Request(
            LLM_BASE_URL.rstrip("/") + "/chat/completions",
            data=payload,
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {LLM_API_KEY}"},
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            content = json.loads(resp.read())["choices"][0]["message"]["content"]
        m = re.search(r"\[.*\]", content, re.S)
        return json.loads(m.group(0)) if m else []
    except Exception as e:
        print(f"[warn] LLM: {e}")
        return []


def send_telegram(text):
    if not CHAT_ID:
        print("[error] 未配置 CHAT_ID")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": CHAT_ID, "text": text,
        "parse_mode": "HTML", "disable_web_page_preview": "True",
    }).encode("utf-8")
    try:
        with urllib.request.urlopen(url, data=data, timeout=30) as resp:
            json.loads(resp.read())
        print("[ok] 已推送")
    except Exception as e:
        print(f"[error] 推送失败: {e}")


def main():
    now = datetime.now(BEIJING).strftime("%m-%d %H:%M")
    print(f"[{now}] 开始扫描海外新闻...")

    news = fetch_news()
    seen = load_seen()

    fresh = []
    for n in news:
        h = news_hash(n["title"])
        if h not in seen:
            fresh.append(n)
            seen[h] = n["title"][:70]

    save_seen(seen)
    print(f"抓取 {len(news)} 条，新增 {len(fresh)} 条")

    if not fresh:
        send_telegram(f"<b>海外雷达</b>　<i>{now}</i>\n\n无新增相关消息")
        return

    print("AI 预判中...")
    analyses = call_llm(fresh)

    results = []
    for a in analyses:
        idx = a.get("index", 0) - 1
        if idx < 0 or idx >= len(fresh):
            continue
        rel = a.get("relevance", 0)
        sectors = a.get("sectors", [])[:2]
        if rel < 2 or not sectors:
            continue
        direction = a.get("direction", "")
        if direction not in ("利好", "利空"):
            continue
        results.append((rel, idx, direction, sectors, a))

    # 排序：强相关在前，同级利好优先（红球，找机会优先）
    results.sort(key=lambda x: (-x[0], 0 if x[2] == "利好" else 1))

    if not results:
        send_telegram(f"<b>海外雷达</b>　<i>{now}</i>\n\n本轮无对中国有实质影响的海外消息")
        return

    lines = [f"<b>海外雷达 → A股预判</b>　<i>{now}</i>"]

    for rel, idx, direction, sectors, a in results[:8]:
        # A股习惯：红涨绿跌
        ball = "🔴" if direction == "利好" else "🟢"
        conf = a.get("confidence", "中")
        reason = (a.get("reason") or "")[:25]
        src = fresh[idx]["source"][:10]
        clean_title = re.sub(r"[【】\[\]]", "", fresh[idx]["title"])[:60]

        lines.append("")
        lines.append(f"{ball} <b>{'、'.join(sectors)}</b>")
        lines.append(clean_title)
        lines.append(f"来源：{src}")
        lines.append(f"把握：{conf}")
        lines.append(f"逻辑：{reason}")

    send_telegram("\n".join(lines))


if __name__ == "__main__":
    main()