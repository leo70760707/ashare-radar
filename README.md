# A股雷达 · 海外新闻 → A股板块预警

每小时的第 7 分钟自动抓取海外新闻，用 DeepSeek AI 判断可能影响的 A 股板块，推送到 Telegram。
- 只推有实质影响的，无影响的不推
- 同一条新闻不重复推（seen.json 去重）
- 你的电脑不用开着，跑在 GitHub Actions 上

## 原理

1. 抓 11 个免费新闻源（谷歌新闻定向 7 个 + BBC中文/纽约时报中文/德国之声中文）
2. 按标题 md5 去重，只处理新增的
3. 调 DeepSeek 判断：是否影响 A 股 → 哪些板块 → 利好还是利空 → 一句话逻辑
4. 推送到 Telegram

## 需要配置的 Secrets

进 Settings → Secrets and variables → Actions → New repository secret

| 名称 | 值 |
|---|---|
| TELEGRAM_TOKEN | `8971408710:AAFaIFqwAySQ4yp8JKrRYHzpTkES6Kyrkg4` |
| TELEGRAM_CHAT_ID | `5593244247` |
| LLM_API_KEY | `sk-df448204eb3c43818cea6eedce18dbe1` |
| LLM_BASE_URL | `https://api.deepseek.com/v1` |
| LLM_MODEL | `deepseek-chat` |
| GITHUB_TOKEN | github.com/settings/tokens 生成，勾选 repo |

## 成本

GitHub Actions 免费；DeepSeek 5元余额够用几个月。
