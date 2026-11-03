# -*- coding: utf-8 -*-
"""
每日 AI 新闻 + 时事热点 抓取脚本
用法：python fetch_news.py
输出：data.js（供 index.html 直接引用，双击网页即可查看）
"""
import requests
import feedparser
import json
import time
import re
from collections import defaultdict
from datetime import datetime, timezone

UA = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                  'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36'
}

# 热度阈值：低于此值不展示（用户要求「热度低的不显示」）
HEAT_THRESHOLD = 45

# ============================================================
# 一、AI 新闻白名单源
# tier: official=一手官方  media=权威媒体  community=社区热门
# 被墙的源(deepmind/huggingface)保留在此，脚本会自动容错跳过，
# 开代理后运行即可自动抓取。
# ============================================================
AI_SOURCES = [
    {"name": "OpenAI",        "url": "https://openai.com/news/rss.xml",                "tier": "official", "lang": "en"},
    {"name": "Google AI",     "url": "https://blog.google/technology/ai/rss/",         "tier": "official", "lang": "en"},
    {"name": "Microsoft",     "url": "https://www.microsoft.com/en-us/research/feed/", "tier": "official", "lang": "en"},
    {"name": "NVIDIA",        "url": "https://blogs.nvidia.com/feed/",                 "tier": "official", "lang": "en"},
    {"name": "DeepMind",      "url": "https://deepmind.google/blog/rss.xml",           "tier": "official", "lang": "en"},
    {"name": "HuggingFace",   "url": "https://huggingface.co/blog/feed.xml",           "tier": "official", "lang": "en"},
    {"name": "MIT Tech Review", "url": "https://www.technologyreview.com/topic/artificial-intelligence/feed", "tier": "media", "lang": "en"},
    {"name": "The Verge AI",  "url": "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml", "tier": "media", "lang": "en"},
    {"name": "Ars Technica",  "url": "https://arstechnica.com/ai/feed/",               "tier": "media", "lang": "en"},
    {"name": "Wired AI",      "url": "https://www.wired.com/feed/tag/ai/latest/rss",   "tier": "media", "lang": "en"},
    {"name": "VentureBeat",   "url": "https://venturebeat.com/category/ai/feed/",      "tier": "media", "lang": "en"},
    {"name": "TechCrunch AI", "url": "https://techcrunch.com/category/artificial-intelligence/feed/", "tier": "media", "lang": "en"},
    {"name": "量子位",          "url": "https://www.qbitai.com/feed",                   "tier": "media", "lang": "zh"},
    {"name": "InfoQ 中文",     "url": "https://www.infoq.cn/feed",                     "tier": "media", "lang": "zh"},
    {"name": "少数派",          "url": "https://sspai.com/feed",                       "tier": "media", "lang": "zh"},
    {"name": "爱范儿",          "url": "https://www.ifanr.com/feed",                   "tier": "media", "lang": "zh"},
    {"name": "极客公园",        "url": "https://www.geekpark.net/rss",                 "tier": "media", "lang": "zh"},
]

# 国内 AI 巨头官方 GitHub 仓库（官方模型发布信号）
CN_GITHUB_REPOS = [
    ("DeepSeek", "deepseek-ai/DeepSeek-V3"),
    ("DeepSeek", "deepseek-ai/DeepSeek-R1"),
    ("DeepSeek", "deepseek-ai/DeepSeek-Coder"),
    ("通义千问 Qwen", "QwenLM/Qwen3"),
    ("通义千问 Qwen", "QwenLM/Qwen3-VL"),
    ("通义千问 Qwen", "QwenLM/Qwen3-Coder"),
    ("智谱 GLM", "zai-org/GLM-5"),
    ("智谱 GLM", "zai-org/GLM-V"),
    ("月之暗面 Kimi", "MoonshotAI/Kimi-K2"),
    ("月之暗面 Kimi", "MoonshotAI/Kimi-K3"),
]

# 时事热点平台（免费 API，无需 key）
HOT_PLATFORMS = [
    ("baidu",    "百度热搜"),
    ("weibo",    "微博热搜"),
    ("zhihu",    "知乎热榜"),
    ("bilibili", "B站热搜"),
]
HOT_API = "https://newsapi.ws4.cn/api/v1/dailynews/?platform={platform}"

# 重磅关键词（命中则热度加成）
HOT_KEYWORDS = ["发布", "开源", "融资", "收购", "上市", "突破", "首发", "官宣",
                "裁员", "封禁", "GPT", "Gemini", "Claude", "Llama", "Sora",
                "模型", "发布", "训练", "推出"]


def clean(text):
    if not text:
        return ""
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def to_ts(struct):
    if not struct:
        return 0
    try:
        return int(time.mktime(struct))
    except Exception:
        return 0


def calc_heat(item):
    """计算热度值 0-100：来源权重 + 新鲜度 + 重磅关键词 + 真实互动信号"""
    tier_score = {"official": 72, "media": 55, "community": 46}.get(item.get("tier"), 50)

    # 新鲜度
    now = time.time()
    age_h = (now - item.get("ts", 0)) / 3600 if item.get("ts") else 999
    if age_h <= 12:
        freshness = 18
    elif age_h <= 24:
        freshness = 13
    elif age_h <= 72:
        freshness = 7
    elif age_h <= 168:
        freshness = 3
    else:
        freshness = 0

    # 重磅关键词
    title = item.get("title", "")
    keyword_bonus = 10 if any(k in title for k in HOT_KEYWORDS) else 0

    # 真实互动信号（Hacker News 的点数/评论数）
    points = item.get("points", 0)
    comments = item.get("comments", 0)
    engage_bonus = min(20, points / 25 + comments / 40) if points else 0

    heat = min(100, tier_score + freshness + keyword_bonus + engage_bonus)
    return int(round(heat))


def fetch_rss(source):
    """抓取单个 RSS 源，返回条目列表"""
    items = []
    r = requests.get(source["url"], headers=UA, timeout=20)
    feed = feedparser.parse(r.content)
    for e in feed.entries[:12]:
        title = clean(e.get("title", ""))
        link = e.get("link", "")
        if not title or not link:
            continue
        ts = to_ts(e.get("published_parsed")) or to_ts(e.get("updated_parsed"))
        items.append({
            "title": title,
            "url": link,
            "source": source["name"],
            "tier": source["tier"],
            "lang": source["lang"],
            "time": time.strftime("%m-%d %H:%M", time.localtime(ts)) if ts else "",
            "ts": ts,
        })
    return items


def fetch_hn():
    """抓取 Hacker News（Algolia API，含真实点数/评论数）"""
    items = []
    try:
        r = requests.get("https://hn.algolia.com/api/v1/search?tags=front_page&hitsPerPage=15",
                         headers=UA, timeout=20)
        for h in r.json().get("hits", []):
            title = clean(h.get("title", ""))
            if not title:
                continue
            items.append({
                "title": title,
                "url": h.get("url") or f"https://news.ycombinator.com/item?id={h.get('objectID')}",
                "source": "Hacker News",
                "tier": "community",
                "lang": "en",
                "points": h.get("points", 0),
                "comments": h.get("num_comments", 0),
                "time": time.strftime("%m-%d %H:%M", time.localtime(h.get("created_at_i", 0))) if h.get("created_at_i") else "",
                "ts": h.get("created_at_i", 0),
            })
    except Exception as e:
        print(f"  [跳过] Hacker News: {str(e)[:50]}")
    return items


def fetch_github_releases():
    """抓取国内 AI 巨头官方 GitHub releases（官方模型发布信号）"""
    items = []
    for brand, repo in CN_GITHUB_REPOS:
        try:
            r = requests.get(f"https://api.github.com/repos/{repo}/releases?per_page=3",
                             headers=UA, timeout=20)
            releases = r.json()
            if not isinstance(releases, list):
                continue
            for rel in releases:
                ts = 0
                try:
                    ts = int(datetime.strptime(rel["published_at"],
                                               "%Y-%m-%dT%H:%M:%SZ").timestamp())
                except Exception:
                    pass
                title = f"{brand} 发布 {rel['tag_name']}"
                name = rel.get("name") or ""
                if name and name != rel["tag_name"]:
                    title = f"{brand} 发布 {rel['tag_name']}：{name}"
                items.append({
                    "title": title,
                    "url": rel.get("html_url", f"https://github.com/{repo}/releases"),
                    "source": f"{brand} 官方",
                    "tier": "official",
                    "lang": "zh",
                    "time": time.strftime("%m-%d", time.localtime(ts)) if ts else "",
                    "ts": ts,
                })
        except Exception as e:
            print(f"  [跳过] {brand} GitHub ({repo}): {str(e)[:40]}")
    return items


def fetch_hot():
    """抓取各平台热点，返回按平台分组的字典（含热度值）"""
    result = {}
    for code, label in HOT_PLATFORMS:
        try:
            r = requests.get(HOT_API.format(platform=code), headers=UA, timeout=20)
            data = r.json().get("data", [])
            items = []
            for i, d in enumerate(data[:20]):
                title = clean(d.get("title", ""))
                url = d.get("url", "")
                if not title:
                    continue
                # 排名即热度：第1名100，逐名递减
                heat = max(0, 100 - i * 5)
                items.append({
                    "title": title,
                    "url": url,
                    "desc": clean(d.get("content") or d.get("desc") or ""),
                    "heat": heat,
                })
            result[label] = items
            print(f"  [热点] {label}: {len(items)} 条")
        except Exception as e:
            print(f"  [热点] {label}: 抓取失败 {str(e)[:50]}")
            result[label] = []
    return result


def fetch_bilibili():
    """抓取 B站热门视频（官方 popular 接口，无需登录）"""
    items = []
    headers = dict(UA)
    headers["Referer"] = "https://www.bilibili.com/"
    try:
        r = requests.get("https://api.bilibili.com/x/web-interface/popular?ps=20&pn=1",
                         headers=headers, timeout=20)
        j = r.json()
        for i, v in enumerate(j.get("data", {}).get("list", [])):
            bvid = v.get("bvid", "")
            title = v.get("title", "")
            if not bvid or not title:
                continue
            play = v.get("stat", {}).get("view", 0)
            items.append({
                "title": title,
                "url": f"https://www.bilibili.com/video/{bvid}",
                "up": v.get("owner", {}).get("name", ""),
                "play": format_play(play),
                "heat": max(0, 100 - i * 4),
            })
        print(f"  [OK] B站热门视频: {len(items)} 条")
    except Exception as e:
        print(f"  [跳过] B站热门视频: {str(e)[:50]}")
    return items


def format_play(n):
    try:
        n = int(n)
    except Exception:
        return str(n)
    if n >= 100000000:
        return f"{n / 100000000:.1f}亿"
    if n >= 10000:
        return f"{n / 10000:.1f}万"
    return str(n)


def interleave(news):
    """按来源轮转打乱，避免同一来源聚集，同时大致保持热度优先"""
    groups = defaultdict(list)
    for n in news:
        groups[n["source"]].append(n)
    for g in groups.values():
        g.sort(key=lambda x: -x["heat"])
    # 组按最高热度排序
    ordered = sorted(groups.keys(), key=lambda s: -groups[s][0]["heat"])
    result = []
    while ordered:
        next_ordered = []
        for s in ordered:
            if groups[s]:
                result.append(groups[s].pop(0))
                next_ordered.append(s)
        ordered = next_ordered
    return result


def main():
    print("=" * 50)
    print("开始抓取 AI 新闻...")
    print("=" * 50)

    news = []
    seen = set()
    for source in AI_SOURCES:
        try:
            items = fetch_rss(source)
            added = 0
            for it in items:
                if it["url"] in seen:
                    continue
                seen.add(it["url"])
                news.append(it)
                added += 1
            print(f"  [OK] {source['name']}: {added} 条")
        except Exception as e:
            print(f"  [跳过] {source['name']}: {str(e)[:50]}（该源当前网络不可达，已自动容错）")

    # Hacker News（真实互动热度）
    print()
    print("抓取 Hacker News...")
    for it in fetch_hn():
        if it["url"] in seen:
            continue
        seen.add(it["url"])
        news.append(it)

    # 国内官方 GitHub 发布
    print()
    print("抓取国内官方 GitHub 发布...")
    for it in fetch_github_releases():
        if it["url"] in seen:
            continue
        seen.add(it["url"])
        news.append(it)

    # 计算热度 + 打乱 + 过滤低热度
    for it in news:
        it["heat"] = calc_heat(it)
    news = [n for n in news if n["heat"] >= HEAT_THRESHOLD]
    news = interleave(news)

    print()
    print("开始抓取时事热点...")
    hot = fetch_hot()

    print()
    print("开始抓取 B站热门视频...")
    bilibili_videos = fetch_bilibili()

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    data = {
        "updated": now,
        "news": news,
        "hot": hot,
        "bilibili": bilibili_videos,
    }

    with open("data.js", "w", encoding="utf-8") as f:
        f.write("window.DATA = " + json.dumps(data, ensure_ascii=False) + ";\n")

    print()
    print(f"完成！AI 新闻 {len(news)} 条（已过滤低热度并打乱），写入 data.js（更新于 {now}）")


if __name__ == "__main__":
    main()
