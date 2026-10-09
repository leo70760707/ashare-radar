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

# ============ 海外新闻源（权威媒体直连）============
# 说明：source 必须是真实媒体名，不是搜索关键词
# 分两类：① 权威财经媒体直连 ② Google News 按题材聚合（会显示真实媒体名）
NEWS_SOURCES = [
    # —— 权威财经媒体直连（已实测可用）——
    ("彭博社", "https://feeds.bloomberg.com/markets/news.rss", True),
    ("华尔街日报", "https://feeds.a.dj.com/rss/RSSMarketsMain.xml", True),
    ("MarketWatch", "https://feeds.content.dowjones.io/public/rss/mw_topstories", True),
    ("经济学人", "https://www.economist.com/finance-and-economics/rss.xml", True),
    ("卫报财经", "https://www.theguardian.com/uk/business/rss", True),
    ("NBC世界", "https://feeds.nbcnews.com/nbcnews/public/world", True),
    # —— 中文权威媒体 ——
    ("BBC中文", "https://feeds.bbci.co.uk/zhongwen/simp/rss.xml", True),
    ("纽约时报中文", "https://cn.nytimes.com/rss/", True),
    ("德国之声", "https://rss.dw.com/rdf/rss-chi-all", True),
    # —— Google News 题材聚合（source 显示真实媒体名）——
    ("综合·对华", "https://news.google.com/rss/search?q=when:12h+China+sanctions+OR+export+controls&hl=en-US&gl=US&ceid=US:en", False),
    ("综合·贸易", "https://news.google.com/rss/search?q=when:12h+US+China+trade+tariff&hl=en-US&gl=US&ceid=US:en", False),
    ("综合·芯片", "https://news.google.com/rss/search?q=when:12h+semiconductor+chip+export+ban&hl=en-US&gl=US&ceid=US:en", False),
    ("综合·能源", "https://news.google.com/rss/search?q=when:12h+oil+crude+OPEC+cut&hl=en-US&gl=US&ceid=US:en", False),
    ("综合·疫情", "https://news.google.com/rss/search?q=when:12h+outbreak+epidemic+virus+WHO&hl=en-US&gl=US&ceid=US:en", False),
    ("综合·美联储", "https://news.google.com/rss/search?q=when:12h+Fed+rate+Powell+inflation&hl=en-US&gl=US&ceid=US:en", False),
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


def extract_real_source(title, fallback):
    """Google News 的标题格式是「标题 - 媒体名」，提取真实媒体名"""
    # 去掉结尾的 " - 媒体名"
    m = re.search(r"\s-\s([^-]{2,40})$", title)
    if m:
        src = m.group(1).strip()
        if src and not src.isdigit():
            return src
    return fallback


def clean_headline(title):
    """去掉标题里的【】和结尾的 - 媒体名"""
    t = re.sub(r"[【】\[\]]", "", title)
    t = re.sub(r"\s-\s[A-Za-z][^-]{1,35}$", "", t)
    return t.strip()


def fetch_news():
    """抓取海外新闻"""
    items = []
    for entry in NEWS_SOURCES:
        source, url = entry[0], entry[1]
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
                if title:
                    real = extract_real_source(title, source)
                    # 有真实媒体名时，只显示媒体名；否则显示配置名
                    items.append({
                        "source": real,
                        "title": title,
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
    """分批调用，避免单次输出过长被截断"""
    if not LLM_API_KEY:
        return []

    all_results = []
    BATCH = 12# 每批12条，确保输出不被截断

    for start in range(0, min(len(news_list), 36), BATCH):
        batch = news_list[start:start + BATCH]
        results = _call_llm_batch(batch, start)
        all_results.extend(results)

    return all_results


def _call_llm_batch(batch, offset):
    """分析一批新闻，index 已做偏移校正"""
    if not batch:
        return []

    news_text = "\n".join(
        [f"[{i + 1}] {n['title'][:110]}" for i, n in enumerate(batch)]
    )
    user_prompt = f"""以下是从海外抓到的最新新闻。请判断哪些会影响A股，预判涨跌方向。

{news_text}

每条格式：
{{"index":1,"relevance":3,"sectors":["板块"],"direction":"利好","confidence":"高","summary":"30字内","reason":"25字内"}}

只输出JSON数组。每条都必须填 reason（25字内说明为什么影响A股）。"""

    try:
        payload = json.dumps({
            "model": LLM_MODEL,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.2,
            "max_tokens": 3000,
        }).encode("utf-8")

        req = urllib.request.Request(
            LLM_BASE_URL.rstrip("/") + "/chat/completions",
            data=payload,
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {LLM_API_KEY}"},
        )
        with urllib.request.urlopen(req, timeout=180) as resp:
            raw = json.loads(resp.read())
        content = raw["choices"][0]["message"]["content"]

        # 兜底：输出被截断时，补齐括号再解析
        content = content.strip()
        if not content.endswith("]"):
            content = content[:content.rfind("]") + 1] if "]" in content else content + "]"

        m = re.search(r"\[.*\]", content, re.S)
        results = json.loads(m.group(0)) if m else []

        # 校正 index（批次内编号 → 全局编号）
        for r in results:
            idx = r.get("index", 0) - 1 + offset
            r["index"] = idx + 1
            # 兜底 reason
            if not r.get("reason"):
                r["reason"] = r.get("summary", "")[:25] or "影响A股市场情绪"
        return results
    except Exception as e:
        print(f"[warn] LLM 批次失败: {e}")
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
        reason = (a.get("reason") or "").strip()
        if not reason:
            continue
        results.append((rel, idx, direction, sectors, reason, a))

    # 排序：强相关在前，同级利好优先（红球，找机会优先）
    results.sort(key=lambda x: (-x[0], 0 if x[2] == "利好" else 1))

    if not results:
        send_telegram(f"<b>海外雷达</b>　<i>{now}</i>\n\n本轮无对中国有实质影响的海外消息")
        return

    lines = [f"<b>海外雷达 → A股预判</b>　<i>{now}</i>"]

    for rel, idx, direction, sectors, reason, a in results[:8]:
        # A股习惯：红涨绿跌
        ball = "🔴" if direction == "利好" else "🟢"
        conf = a.get("confidence", "中")
        reason = reason[:25]
        src = fresh[idx]["source"][:10]
        clean_title = clean_headline(fresh[idx]["title"])[:60]

        lines.append("")
        lines.append(f"{ball} <b>{'、'.join(sectors)}</b>")
        lines.append(clean_title)
        lines.append(f"来源：{src}")
        lines.append(f"把握：{conf}")
        lines.append(f"逻辑：{reason}")

    send_telegram("\n".join(lines))


if __name__ == "__main__":
    main()