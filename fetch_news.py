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
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
import os
from urllib.parse import quote as urlquote

UA = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                  'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36'
}

# 热度阈值：低于此值不展示（用户要求「热度低的不显示」）
HEAT_THRESHOLD = 60

# ============================================================
# 一、AI 新闻白名单源
# tier: official=一手官方  media=权威媒体  community=社区热门
# 被墙的源(deepmind/huggingface)保留在此，脚本会自动容错跳过，
# 开代理后运行即可自动抓取。
# ============================================================
AI_SOURCES = [
    # ---------- T1 AI 巨头（weight 75，最高权重） ----------
    {"name": "OpenAI",        "url": "https://openai.com/news/rss.xml",                "tier": "official", "lang": "en", "weight": 75},
    {"name": "Google AI",     "url": "https://blog.google/technology/ai/rss/",         "tier": "official", "lang": "en", "weight": 75},
    {"name": "DeepMind",      "url": "https://deepmind.google/blog/rss.xml",           "tier": "official", "lang": "en", "weight": 75},
    {"name": "Meta AI",       "url": "https://engineering.fb.com/feed/",               "tier": "official", "lang": "en", "weight": 75},
    {"name": "Anthropic",     "url": "https://news.google.com/rss/search?q=Anthropic&hl=en-US&gl=US&ceid=US:en", "tier": "official", "lang": "en", "weight": 75},
    # ---------- T2 科技巨头（weight 71） ----------
    {"name": "Microsoft",     "url": "https://www.microsoft.com/en-us/research/feed/", "tier": "official", "lang": "en", "weight": 71},
    {"name": "NVIDIA",        "url": "https://blogs.nvidia.com/feed/",                 "tier": "official", "lang": "en", "weight": 71},
    {"name": "HuggingFace",   "url": "https://huggingface.co/blog/feed.xml",           "tier": "official", "lang": "en", "weight": 71},
    {"name": "IT之家",        "url": "https://www.ithome.com/rss/",                    "tier": "media",    "lang": "zh", "weight": 62},
    # ---------- 顶级权威媒体（weight 62） ----------
    {"name": "MIT Tech Review", "url": "https://www.technologyreview.com/topic/artificial-intelligence/feed", "tier": "media", "lang": "en", "weight": 62},
    {"name": "The Verge AI",  "url": "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml", "tier": "media", "lang": "en", "weight": 62},
    {"name": "TechCrunch AI", "url": "https://techcrunch.com/category/artificial-intelligence/feed/", "tier": "media", "lang": "en", "weight": 62},
    {"name": "钛媒体",        "url": "https://www.tmtpost.com/rss.xml",                "tier": "media",    "lang": "zh", "weight": 62},
    # ---------- 一般媒体（weight 58） ----------
    {"name": "Ars Technica",  "url": "https://arstechnica.com/ai/feed/",               "tier": "media", "lang": "en", "weight": 58},
    {"name": "Wired AI",      "url": "https://www.wired.com/feed/tag/ai/latest/rss",   "tier": "media", "lang": "en", "weight": 58},
    {"name": "VentureBeat",   "url": "https://venturebeat.com/category/ai/feed/",      "tier": "media", "lang": "en", "weight": 58},
    {"name": "量子位",          "url": "https://www.qbitai.com/feed",                   "tier": "media", "lang": "zh", "weight": 58},
    {"name": "InfoQ 中文",     "url": "https://www.infoq.cn/feed",                     "tier": "media", "lang": "zh", "weight": 64},
    {"name": "少数派",          "url": "https://sspai.com/feed",                       "tier": "media", "lang": "zh", "weight": 58},
    {"name": "爱范儿",          "url": "https://www.ifanr.com/feed",                   "tier": "media", "lang": "zh", "weight": 58},
    {"name": "极客公园",        "url": "https://www.geekpark.net/rss",                 "tier": "media", "lang": "zh", "weight": 58},
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

# 财经新闻平台（newsapi 接口；雪球已删除——其链接全部指向首页，点进去必然报错）
FINANCE_PLATFORMS = [
    ("cls",          "财联社"),
    ("eastmoney",    "东方财富"),
    ("sina_finance", "新浪财经"),
]

# 华尔街见闻 7x24 快讯（官方 API，含投行观点、政策、债市等权威财经内容）
WSCN_API = "https://api-one.wallstcn.com/apiv1/content/lives?channel=global-channel&limit=30"

# ---------- 投行研报：东方财富研报中心 ----------
# 官方公开接口，支持 beginTime/endTime，可回溯历史（这是唯一能真正取到「近一个月」的研报源）
# qType: 0=个股研报 1=行业研报 2=策略报告 3=宏观研究
EM_REPORT_API = "https://reportapi.eastmoney.com/report/list"
EM_REPORT_PAGE = {
    "stock":    "https://data.eastmoney.com/report/zw_stock.jshtml?infocode={code}",
    "industry": "https://data.eastmoney.com/report/zw_industry.jshtml?infocode={code}",
    "strategy": "https://data.eastmoney.com/report/zw_strategy.jshtml?infocode={code}",
}
REPORT_DAYS = 30          # 研报回溯天数
REPORT_LIMIT = 45         # 研报板块最终条数

# ---------- 国际投行 ----------
# Yahoo Finance 按股票代码提供的个股新闻 RSS（该投行的动态/观点，英文）
# 第三项是英文名正则，用于识别英文标题里提到的是哪家
BANK_TICKERS = [
    ("GS",   "高盛",       r"\bGoldman\b"),
    ("MS",   "摩根士丹利",  r"\bMorgan Stanley\b"),
    ("JPM",  "摩根大通",    r"\bJPMorgan\w*|\bJ\.?\s?P\.?\s?Morgan\b"),
    ("C",    "花旗",       r"\bCitigroup\b|\bCiti\b"),
    ("UBS",  "瑞银",       r"\bUBS\b"),
    ("BAC",  "美银",       r"\bBofA\b|\bBank of America\b"),
    ("DB",   "德意志银行",  r"\bDeutsche Bank\b"),
    ("HSBC", "汇丰",       r"\bHSBC\b"),
    ("BCS",  "巴克莱",     r"\bBarclays\b"),
    ("NMR",  "野村",       r"\bNomura\b"),
    ("BLK",  "贝莱德",     r"\bBlackRock\b"),
    ("BX",   "黑石",       r"\bBlackstone\b"),
    ("SCHW", "嘉信理财",    r"\bSchwab\b"),
]
YF_BANK_RSS = ("https://feeds.finance.yahoo.com/rss/2.0/headline"
               "?s={ticker}&region=US&lang=en-US")
BANK_NAMES = [b for _, b, _ in BANK_TICKERS]
# 交易所公告模板 / 基金推销类标题，对读者没有信息量，直接剔除
BANK_NOISE = ["Transaction in Own Shares", "Form 10-Q", "Form 8-K", "Form 4",
              "Form 3", "Dividend", "Notice of", "Proxy Statement", "Annual Report",
              "Interim Results", "Block Listing", "Total Voting Rights",
              "Holding(s) in Company", "Director/PDMR", "Result of AGM",
              "Variable Rate Fix", "Senior Notes Offering", "Mutual Funds",
              "Investing Radar", "Better Value Stock", "Chief Financial Officer",
              "Ex-Dividend", "to Offer Crypto Services", "Stock Looks About Right"]
# 只有命中「观点 / 动作」信号，才算一条投行资讯（否则只是文章里顺带提到这只股票）
BANK_SIGNAL = ["sees", "expects", "warns", "says", "rates", "cut", "raise", "upgrade",
               "downgrade", "target", "forecast", "outlook", "bullish", "bearish",
               "predict", "estimate", "analyst", "calls", "flag", "highlight", "debate",
               "top pick", "strategy", "strategist", "to open", "launch", "acquir",
               "recruit", "hire", "expand", "partner", "confidence",
               # 中文
               "目标价", "评级", "上调", "下调", "看好", "警告", "预测", "预计", "称",
               "买入", "增持", "减持", "首次覆盖", "观点", "报告"]


def _bank_of(title):
    """识别标题提到的是哪家投行（中文名 / 英文名均可），没提到返回空"""
    for _, cn, pat in BANK_TICKERS:
        if cn in title or re.search(pat, title, re.I):
            return cn
    return ""


def _bank_relevant(title):
    """判断一条标题是否值得进「国际投行」：不能是公告噪音，且要体现观点或动作"""
    low = title.lower()
    if any(n.lower() in low for n in BANK_NOISE):
        return False
    if not _bank_of(title):
        return False
    return any(s in low for s in BANK_SIGNAL)

# Google News 中文搜索国际投行（近 30 天）。本地被墙抓不到，云端 GitHub Actions 可抓
GN_BANK_URL = ("https://news.google.com/rss/search?q=" +
               urlquote("高盛 OR 摩根士丹利 OR 摩根大通 OR 花旗 OR 瑞银 OR 野村 OR 德意志银行 "
                        "OR 巴克莱 OR 汇丰 when:30d") +
               "&hl=zh-CN&gl=CN&ceid=CN:zh-Hans")
BANK_LIMIT = 45           # 国际投行板块最终条数


# 体育新闻：新浪体育滚动接口（RSS 源早已停更在 2018 年，改用官方 JSON 接口）
SINA_SPORTS_API = "https://feed.mix.sina.com.cn/api/roll/get?pageid=153&lid=2512&num=30&page=1"

# ---------- 体育：按项目分子栏目 ----------
# 标题关键词 → 子栏目。赛车关键词最独特放最前面，避免被通用词抢走
SPORT_CATS = [
    ("赛车", ["F1", "MotoGP", "方程式", "维斯塔潘", "汉密尔顿", "勒克莱尔", "诺里斯",
              "法拉利", "红牛车队", "梅赛德斯", "迈凯伦", "赛车", "拉力赛", "纳斯卡",
              "排位赛", "正赛", "车手积分", "Norris", "Verstappen", "Hamilton", "Leclerc",
              "Ferrari", "McLaren", "Red Bull", "Mercedes", "Wolff", "Ocon", "Haas",
              "Cadillac", "Grand Prix", "Prix", "Antonelli"]),
    ("篮球", ["NBA", "CBA", "篮球", "詹姆斯", "库里", "杜兰特", "湖人", "勇士", "凯尔特人",
              "雄鹿", "篮网", "文班", "约基奇", "东契奇", "塔图姆", "热火", "快船", "掘金",
              "独行侠", "太阳", "76人", "尼克斯", "雷霆", "森林狼", "季后赛", "火箭",
              "灰熊", "老鹰", "步行者", "黄蜂", "马刺", "开拓者", "爵士", "魔术", "活塞",
              "奇才", "骑士", "猛龙", "公牛", "国王", "鹈鹕", "恩比德", "字母哥", "爱德华兹",
              "亚历山大", "莫兰特", "特雷杨", "哈登", "欧文", "布克", "锡安", "班凯罗",
              "布朗", "库明加", "申京", "奥萨尔", "篮板", "助攻", "三分", "Doncic",
              "男篮", "女篮", "郭士强", "姚明", "易建联",
              # 「G联赛」是 NBA 发展联盟，不带这些会被足球的「联赛」抢走
              "杨瀚森", "G联赛", "G联盟", "NBA发展联盟", "发展联盟", "夏季联赛",
              "崔永熙", "曾凡博", "周琦", "余嘉豪", "林葳", "张镇麟", "胡明轩",
              "赵睿", "赵继伟", "郭艾伦", "王哲林", "G League"]),
    ("足球", ["足球", "中超", "英超", "西甲", "意甲", "德甲", "法甲", "欧冠", "世界杯",
              "欧洲杯", "亚洲杯", "国足", "梅西", "C罗", "皇马", "巴萨", "曼联", "利物浦",
              "阿森纳", "拜仁", "尤文", "切尔西", "曼城", "热刺", "AC米兰", "国际米兰",
              "点球", "红牌", "进球", "多特蒙德", "贝蒂斯", "波尔图",
              "亚冠", "欧联杯", "欧协联", "世预赛", "足总杯", "联赛杯", "德国杯",
              "那不勒斯", "费内巴切", "莱比锡", "布莱顿", "阿森纳",
              "Liverpool", "Arsenal", "Chelsea", "Tottenham", "Everton", "Reds",
              "Real Madrid", "Barcelona", "Bayern", "Juventus", "PSG",
              "Inter Milan", "Premier League", "Champions League", "World Cup",
              "Kane", "Messi", "Ronaldo", "Salah", "Haaland", "Mbappe", "football",
              # 天空体育的英文标题常只写球队/赛事名，不补这些会漏到「综合」
              "Europa League", "Conference League", "Bundesliga", "La Liga",
              "Serie A", "Ligue 1", "FA Cup", "Man Utd", "Man United",
              "Manchester United", "Man City", "Manchester City", "Brighton",
              "Celtic", "Rangers", "Newcastle", "Aston Villa", "West Ham",
              "Leeds", "Fulham", "Brentford", "Crystal Palace", "Bournemouth",
              "Wolves", "Leicester", "Southampton", "Sunderland", "Nottingham",
              "Betis", "Getafe", "Sevilla", "Valencia", "Atletico", "Dortmund",
              "Leipzig", "Leverkusen", "Napoli", "Lazio", "Benfica", "Porto",
              "Ajax", "PSV", "Feyenoord", "Besiktas", "Galatasaray",
              "Fenerbahce", "Deschamps", "Carrick"]),
]

# 综合类（乒乓/网球/橄榄球/板球/拳击等）的英文关键词，用于把天空体育的杂项归类
SPORT_OTHER_EN = ["Boxing", "boxer", "fight", "Wilder", "Usyk", "Billam-Smith", "Zuffa",
                  "Cricket", "wicket", "Ashes", "Rugby", "Tennis", "Wimbledon",
                  "snooker", "Golf", "athletics", "swimming"]

# 天空体育 RSS（英文战报，按运动分类，实测每个源 20 条）
SKY_SPORTS_RSS = [
    ("足球", "https://www.skysports.com/rss/12040"),
    ("赛车", "https://www.skysports.com/rss/12433"),
    ("综合", "https://www.skysports.com/rss/12183"),   # 拳击
    ("综合", "https://www.skysports.com/rss/12341"),   # 板球/橄榄球
]

# 虎扑版块（中文战报与流言，只取 [流言板] 这类资讯帖）
HUPU_SPORTS = [
    ("篮球", "nba"),
    ("篮球", "cba"),
    ("足球", "soccer"),
]

# 天空体育 RSS 里的导航/推广条目，直接跳过
SKY_JUNK = ["Get Sky Sports", "Pub Finder", "Sky Sports live dates", "TV Picks",
            "Watch on Sky", "How to watch", "Sign up", "Download the app"]

# ---------- 游戏资讯 ----------
# 判定「这条是不是游戏内容」：书名号基本都能锁定游戏名，再加平台/厂商词
GAME_KW = ["游戏", "Steam", "PS5", "PS4", "Xbox", "Switch", "任天堂", "索尼", "微软",
           "发售", "上线", "测试", "新作", "续作", "DLC", "版本", "更新", "手游", "端游",
           "主机", "评测", "试玩", "开发", "制作人", "工作室", "封测", "公测", "抢先体验",
           "独立游戏", "赛季", "开服", "联动", "副本", "资料片", "重制", "复刻",
           "育碧", "卡普空", "米哈游", "腾讯", "网易", "Steam", "Epic", "PlayStation"]

# 「近期热门」优先展示这类标题（新游发售/测试/定档）
GAME_HOT_KW = ["发售", "上线", "公测", "封测", "内测", "测试", "定档", "开服", "新作",
               "续作", "抢先体验", "预购", "首发", "登陆", "版本", "资料片", "重制",
               "复刻", "更新"]

# 近期热门：游戏媒体的综合资讯 RSS
GAME_HOT_RSS = [
    ("机核", "https://www.gcores.com/rss"),
    ("游研社", "https://www.yystv.cn/rss/feed"),
    ("触乐", "https://www.chuapp.com/feed"),
]

# 电竞资讯：虎扑各游戏版块的 [流言板]
GAME_ESPORTS_BOARDS = [
    ("英雄联盟", "lol"),
    ("CS2", "csgo"),
    ("王者荣耀", "kog"),
    ("PUBG", "pubg"),
]

# 电竞标题里的游戏名，用于打标签
ESPORTS_GAMES = {    "英雄联盟": ["LPL", "LCK", "LCS", "S赛", "世界赛", "MSI", "S16", "S15", "S14",
                 "BLG", "JDG", "TES", "WBG", "IG", "RNG", "EDG", "T1", "GEN", "DK",
                 "HLE", "Faker", "Chovy", "Wei", "英雄联盟", "LOL"],
    "CS2": ["CS2", "CSGO", "Major", "NAVI", "Vitality", "FaZe", "G2", "Liquid",
            "zonic", "iM", "HLTV"],
    "王者荣耀": ["KPL", "王者荣耀", "狼队", "AG超玩会", "TTG", "eStar", "挑杯"],
    "PUBG": ["PUBG", "PCL", "绝地求生"],
}


# 政治新闻源（RSS）：国家级时政 + 政策会议决策
POLITICS_FEEDS = [
    ("中国新闻网·时政", "https://www.chinanews.com.cn/rss/china.xml"),
    ("中国新闻网·要闻", "https://www.chinanews.com.cn/rss/scroll-news.xml"),
]

# 政治相关性：标题需命中「政策/会议/决策」类关键词
POLITICS_KW = [
    # 政策文件与决策
    "政策", "印发", "通知", "意见", "条例", "办法", "规划", "纲要", "决定", "部署",
    "实施方案", "若干措施", "指导意见", "白皮书", "工作报告", "立法", "法规", "草案",
    # 机构与会议
    "国务院", "全国人大", "全国政协", "中央", "省委", "市委", "县委", "自治区",
    "常委会", "全会", "常务会议", "政治局", "国常会", "部委", "发改委", "工信部",
    "农业农村部", "商务部", "财政部", "央行", "证监会",
    # 人事与治理
    "任命", "免职", "任副省长", "代省长", "任省长", "任书记", "当选", "选举",
    "督察", "巡视", "反腐", "受贿", "起诉", "双开", "调查",
    # 重要讲话与政务发布
    "讲话", "致辞", "座谈", "会见", "签署", "国台办", "新闻发布会", "记者会", "发言人",
    "峰会", "论坛", "召开", "举行", "大会", "调研", "督查", "考核",
]

# 政治噪音词：明显不属于「政策/会议/决策」
POLITICS_NOISE = [
    "美媒", "奥巴马", "特朗普", "民主党", "共和党", "伊朗", "中期选举", "白宫",
    "银杏", "滑雪", "美酒", "宠物", "明星", "艺人", "景区", "赛事", "旅游", "演唱会",
    "天气", "菜价", "批发价格", "电影", "综艺", "球队", "比分", "夺冠",
    "旅发大会", "以文塑旅", "相约", "美酒展", "品鉴",
]

# 同一主题最多保留条数（避免某类话题刷屏，例如国台办回应占满整栏）
POLITICS_TOPIC_CAP = {"国台办": 3, "陆委会": 1, "台独": 1}

# 体育噪音词：剔除非新闻的博彩推荐/彩票预测
SPORTS_NOISE = ["竞彩", "彩票", "赔率", "盘口", "让球", "亚盘", "大小球", "独赢", "串关",
                "小刀汇", "关小刀", "任九", "半全场", "比分预测", "投注", "赌球", "博彩",
                "六合彩", "双色球", "大乐透", "足彩", "竞猜", "开户", "充值", "盈利计划",
                "过关", "单关", "北单", "盈球", "彩经", "彩吧", "买球", "下注", "红单",
                # 媒体号的「荐彩」写法，标题里往往不带「彩票」二字
                "胜负彩", "复式", "比赛解读", "红王", "心水", "定胆", "稳胆", "独胆",
                "总汇", "荐彩", "解盘", "福彩", "奖号", "3D预测", "排列三",
                # 板球不在收录范围，别混进足球（橄榄球要保留，归综合）
                "T20", "cricket", "Cricket", "wicket", "板球", "IPL", "ODI",
                "Test match"]

# 篮球以 NBA 与海外话题为主：CBS 的 NBA 频道是最稳的英文源（本地云端都通）
NBA_RSS = [("CBS体育", "https://www.cbssports.com/rss/headlines/nba/")]
NBA_NOISE = ["betting", "bet ", "odds", "promo", "parlay", "spread",
             "bonus", "picks", "wagering", "如何下注"]

# 篮球只关注 NBA 与海外话题；CBA 仅保留决赛、冠军这类关键节点
CBA_KEEP = ["决赛", "总决赛", "冠军", "夺冠", "季后赛", "半决赛", "四强", "全明星", "夺冠"]

# 小红书热搜（独立免费接口，uapis.cn）
XHS_API = "https://uapis.cn/api/v1/misc/hotboard?type=xiaohongshu"

# 重磅关键词（命中数量越多，加成越高；覆盖 AI 巨头 / 科技企业 / 热门话题）
HOT_KEYWORDS = [
    # 动作/事件
    "发布", "开源", "融资", "收购", "上市", "突破", "首发", "官宣", "裁员", "封禁", "推出", "训练",
    # AI 巨头与模型
    "OpenAI", "Anthropic", "DeepSeek", "Gemini", "Claude", "GPT", "Llama", "Grok",
    "Mistral", "Qwen", "Sora", "大模型", "模型",
    # 热门科技企业
    "华为", "小米", "百度", "特斯拉", "苹果", "NVIDIA", "英伟达", "字节", "阿里", "腾讯",
    # 热门话题
    "具身智能", "机器人", "人形机器人", "智能体", "Agent", "多模态", "AGI", "算力", "芯片",
]

# AI 相关性关键词：媒体源标题必须命中其一才收录（用于剔除财经/产业/消费等非 AI 内容）
AI_RELEVANT = [
    "AI", "人工智能", "大模型", "模型", "智能", "机器人", "智能体", "Agent", "具身",
    "芯片", "算力", "GPU", "半导体", "数据中心", "算法", "机器学习", "深度学习", "神经网络",
    "GPT", "OpenAI", "Anthropic", "DeepSeek", "Gemini", "Claude", "Llama", "Grok",
    "Qwen", "Mistral", "NVIDIA", "英伟达", "多模态", "AGI", "生成式", "AIGC",
    "自动驾驶", "智能驾驶", "语音识别", "计算机视觉", "数据中心",
]


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
    """计算热度值 0-100：来源梯队 + 连续时间衰减 + 关键词分级 + 真实互动"""
    # 来源梯队权重（AI巨头75 > 科技巨头71 > 国内AI69 > 权威媒体62 > 一般媒体58 > 社区52）
    tier_score = item.get("weight") or {"official": 75, "media": 58, "community": 52}.get(item.get("tier"), 58)

    # 时间：72 小时内连续线性衰减，基数 15（不再固定档位，让每条新闻分数各不相同）
    now = time.time()
    age_h = (now - item.get("ts", 0)) / 3600 if item.get("ts") else 999
    freshness = 15.0 * (1 - age_h / 72) if age_h < 72 else 0.0

    # 关键词：命中越多加成越高（1个+5 / 2个+8 / 3个及以上+10）
    title = item.get("title", "")
    hits = sum(1 for k in HOT_KEYWORDS if k in title)
    keyword_bonus = 10 if hits >= 3 else (8 if hits == 2 else (5 if hits == 1 else 0))

    # 真实互动（仅 Hacker News）：点赞/50 + 评论/20，封顶 15
    points = item.get("points", 0)
    comments = item.get("comments", 0)
    engage_bonus = min(15, points / 50 + comments / 20) if (points or comments) else 0

    heat = min(100, tier_score + freshness + keyword_bonus + engage_bonus)
    return int(round(heat))


# ============================================================
# 英文条目的中文附注：脚本会同时生成两版，网页上可随时切换
#   brief_tr  = 方案A：标题的中文翻译
#   brief_sum = 方案B：正文摘要首句的中文极简总结（信息量更大）
# ============================================================
# 翻译缓存（避免每天重复翻译同一条）
TRANS_CACHE_FILE = "trans_cache.json"


def _cache_load():
    if os.path.exists(TRANS_CACHE_FILE):
        try:
            with open(TRANS_CACHE_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def _cache_save(cache):
    try:
        with open(TRANS_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False)
    except Exception:
        pass


_GOOGLE_OK = None   # None=尚未探测，True=可用，False=不可用


def _google_ok():
    """只探测一次 Google 翻译是否可达（本地常被墙，云端可用），避免每条都等超时"""
    global _GOOGLE_OK
    if _GOOGLE_OK is None:
        try:
            u = ("https://translate.googleapis.com/translate_a/single"
                 "?client=gtx&sl=en&tl=zh-CN&dt=t&q=hello")
            r = requests.get(u, headers=UA, timeout=4)
            _GOOGLE_OK = (r.status_code == 200)
        except Exception:
            _GOOGLE_OK = False
        print(f"  [通道] Google 翻译{'可用' if _GOOGLE_OK else '不可达，改用 MyMemory'}")
    return _GOOGLE_OK


def translate(text, cache):
    """翻译成中文：优先 Google（云端可用），失败退回 MyMemory（本地可用）"""
    text = clean(text)[:300]
    if not text:
        return ""
    if text in cache:
        return cache[text]

    # 1) Google 免费端点（GitHub Actions 海外环境可用，本地常被墙）
    if _google_ok():
        try:
            import urllib.parse
            u = ("https://translate.googleapis.com/translate_a/single"
                 "?client=gtx&sl=en&tl=zh-CN&dt=t&q=" + urllib.parse.quote(text))
            r = requests.get(u, headers=UA, timeout=8)
            if r.status_code == 200:
                data = r.json()
                out = "".join(seg[0] for seg in data[0] if seg and seg[0])
                out = clean(out)
                if out:
                    cache[text] = out
                    return out
        except Exception:
            pass

    # 2) MyMemory 免费接口（本地实测稳定）
    try:
        import urllib.parse
        u = ("https://api.mymemory.translated.net/get?q=" +
             urllib.parse.quote(text[:480]) + "&langpair=en|zh-CN")
        r = requests.get(u, headers=UA, timeout=10)
        j = r.json()
        out = clean(j.get("responseData", {}).get("translatedText", ""))
        # MyMemory 偶尔回原文或报错提示，过滤掉
        if out and "MYMEMORY WARNING" not in out.upper() and out.lower() != text.lower():
            cache[text] = out
            return out
    except Exception:
        pass
    return ""


def translate_qq(text, cache):
    """腾讯 TranSmart 翻译：本地也能用，质量接近 Google，专供国际投行板块

    AI 新闻仍走上面的 translate()，保持原有风格不变。
    """
    text = clean(text)[:500]
    if not text:
        return ""
    key = "qq|" + text          # 加引擎前缀，避免和 MyMemory 的译文混用
    if key in cache:
        return cache[key]

    body = {
        "header": {
            "fn": "auto_translation",
            "client_key": "browser-chrome-120.0.0-Windows-10-"
                          "6f1d2c3a-7b45-4e89-9c01-2d3e4f5a6b7c-1700000000000",
        },
        "type": "plain",
        "model_category": "normal",
        "source": {"lang": "en", "text_list": [text]},
        "target": {"lang": "zh"},
    }
    try:
        r = requests.post(
            "https://transmart.qq.com/api/imt", json=body,
            headers={"User-Agent": UA["User-Agent"],
                     "Content-Type": "application/json",
                     "Referer": "https://transmart.qq.com/zh-CN/index"},
            timeout=15)
        if r.status_code == 200:
            segs = r.json().get("auto_translation") or []
            out = clean("".join(segs))
            if out and out != text:
                cache[key] = out
                return out
    except Exception:
        pass
    return ""


def first_sentence(text, limit=180):
    """取首句作为摘要原料

    注意：英文缩写里的句点（J.P. Morgan / Inc. / U.S.）不能当句子边界，
    否则会把「J.P.」单独切成一句，翻译出来就是半截话。
    """
    text = clean(text)
    if not text:
        return ""
    abbr = (r"(J\.P\.|J\.P\.\s?Morgan|Inc\.|Ltd\.|Co\.|Corp\.|U\.S\.|U\.K\.|"
            r"Mr\.|Mrs\.|Ms\.|Dr\.|No\.|St\.|vs\.|etc\.|A\.I\.)")
    # 先把缩写里的句点替换成占位符，切句后再还原
    tmp = re.sub(abbr, lambda m: m.group(0).replace(".", "\x00"), text)
    parts = re.split(r'(?<=[.!?。！？])\s+', tmp)
    parts = [p.replace("\x00", ".") for p in parts]
    s = parts[0] if parts else text
    if len(s) < 25 and len(parts) > 1:
        s = s + " " + parts[1]
    return s[:limit]


def _tidy_cn(s, floor=24):
    """中文摘要收尾清理：去掉悬空标点、虚词尾巴、未闭合括号、英文碎片"""
    s = s.rstrip("，,。；;、：: 　")
    # 结尾挂在虚词上（「…轻量级设备上的」）会显得话没说完，去掉
    if s and s[-1] in "的了和与及或在把被将从向以于":
        s = s[:-1].rstrip("，,。；;、：: 　")
    # 括号没闭合就退到左括号前，避免出现「…（纳斯达克股票代码」
    for lb, rb in (("（", "）"), ("(", ")"), ("【", "】")):
        if s.count(lb) > s.count(rb):
            i = s.rfind(lb)
            if i > floor:
                s = s[:i].rstrip("，,。；;、：: 　")
    # 结尾只剩很短的英文/数字碎片（「…上的Ge」「…筹集3.」）时退到边界；
    # 长单词保留原样，宁可贴着边也不要把整个词删掉
    m = re.search(r"[A-Za-z0-9.]+$", s)
    if m and len(m.group()) <= 5 and m.start() >= floor:
        s = s[:m.start()].rstrip("，,。；;、：: 　-")
    # 中文标点前不该有空格（翻译接口偶尔会带）
    return re.sub(r"\s+([，。；：、！？])", r"\1", s).strip()


def truncate_cn(text, limit=48, hard=56, ratio=0.55):
    """极简总结：压到一行内说完，只收在标点处，**不留省略号**

    直接在原文上找断句位置截取（不重新拼接），顿号、书名号原样保留；
    第一个分句本身就超过 limit 时，语义完整优先，允许放到 hard 字。
    桌面宽度下一行约容 60 字，limit=48 留了余量；国际投行另传参数。
    """
    text = clean(text).replace("\n", " ").strip()
    if not text:
        return ""
    # 媒体名开头（"Investing.com -"）对读者没有意义，去掉
    text = re.sub(r"^(Investing\.com|Yahoo Finance|Reuters)\s*[-–—]?\s*", "", text)
    text = text.lstrip("-–—· ").strip()
    if len(text) <= limit:
        return _tidy_cn(text, floor=limit * 0.6)

    # 所有可断句的位置（标点之后）。不把英文句点当断句符，
    # 否则「J.P. Morgan」「Inc.」这类缩写会被拦腰切断
    cuts = [m.end() for m in re.finditer(r"[，。；;！!？?、：:]", text)]
    fits = [p for p in cuts if p <= limit]
    if fits and fits[-1] >= limit * ratio:
        # 断点够靠后，正常收在标点上
        out = text[:fits[-1]]
    elif not fits and cuts and cuts[0] <= hard:
        # limit 内没有断点，但第一个分句本身不超 hard，整句保留
        out = text[:cuts[0]]
    else:
        # 断点都太靠前（例如只剩「美国银行表示」「随着股票…」这种从句），
        # 说明结论在后半句，宁可多取一些也不要只留半句
        out = text[:min(hard, len(text))]

    return _tidy_cn(out, floor=limit * 0.6)


def build_briefs(item, cache):
    """同时产出两种方案：
       方案A brief_tr  = 标题的中文翻译
       方案B brief_sum = 正文摘要首句的中文总结（信息量更大）
    """
    if item.get("lang") == "zh":
        return "", ""
    # 方案A：翻译标题
    tr = translate(item.get("title", ""), cache)
    # 方案B：翻译摘要首句，没有摘要则退回标题
    raw = item.get("summary", "")
    src = first_sentence(raw) if raw else item.get("title", "")
    sm = translate(src, cache)
    if sm:
        sm = truncate_cn(sm)
        # 摘要翻译不可用（只翻了半句、返回「待定」）或长到一行放不下时，
        # 退回更短的中文标题，保证每行都是完整句子
        if tr and 8 <= len(tr) <= 46 and (len(sm) < 20 or len(sm) > 54):
            sm = tr
    return tr, sm


# ============================================================
# AI 摘要升级（可选功能）
#   只有配了 AI_API_KEY 才会启用；接口兼容 OpenAI 格式，
#   DeepSeek / 智谱 / 通义 / 火山方舟 都只需改 AI_BASE_URL 与 AI_MODEL。
#   设计原则：AI 只是「锦上添花」，任何异常都退回原来的翻译摘要，
#   绝不因为模型调用失败导致整站没有数据。
# ============================================================
AI_API_KEY = os.environ.get("AI_API_KEY", "").strip()
AI_BASE_URL = (os.environ.get("AI_BASE_URL") or "https://api.deepseek.com/v1").rstrip("/")
AI_MODEL = os.environ.get("AI_MODEL") or "deepseek-flash"
AI_BATCH = 15          # 每批发多少条，太大容易超时、太小浪费请求
AI_WORKERS = 3         # 并发批次数
_AI_USAGE = []         # 记录 token 用量，便于核对花费

AI_SYS_PROMPT = (
    "你是中文科技新闻编辑。为每条英文新闻写一句中文导语，"
    "唯一目标是让中文读者一眼看懂这条新闻在讲什么。\n"
    "先判断标题能不能看懂，再决定写法：\n"
    "- 标题本身已经说清「谁做了什么」：就把它忠实译成通顺中文\n"
    "- 标题是提问、口号、玩梗、术语堆砌、或信息不完整：不要硬译标题，"
    "直接用正文写成一句说明，讲清发生了什么\n"
    "- 标题和正文说的不一致：以正文为准\n"
    "硬性要求：\n"
    "- 一句话说完，40 个汉字以内，越精简越好；不换行、不分条、不写「译」「摘要」等前缀\n"
    "- 专有名词一律保留英文原样，不要译成中文：Gemini Omni 1.2 Flash、GPT-6、Anthropic、NVIDIA、Jensen Huang 等\n"
    "- 禁止「文本」「该文章」「本文」「据悉」这类空话，也不要复述读者已经看到的标题\n"
    "- 不加评价、不标题党、不用感叹号、结尾不加句号\n"
    '- 只输出 JSON，格式：{"items":[{"id":1,"line":"..."}]}'
)


def _ai_one(batch):
    """一次模型调用，返回 {url: 中文导语}"""
    lines = []
    for i, it in enumerate(batch, 1):
        body = first_sentence(re.sub(r"\s+", " ", it.get("summary") or ""), 400)
        lines.append(f'[{i}] 标题：{it["title"]}\n正文：{body or "(无正文)"}')
    r = requests.post(
        AI_BASE_URL + "/chat/completions",
        headers={"Authorization": "Bearer " + AI_API_KEY,
                 "Content-Type": "application/json"},
        json={
            "model": AI_MODEL,
            "messages": [
                {"role": "system", "content": AI_SYS_PROMPT},
                {"role": "user", "content": "\n\n".join(lines)},
            ],
            "temperature": 0.3,
            "response_format": {"type": "json_object"},
        },
        timeout=90,
    )
    r.raise_for_status()
    j = r.json()
    u = j.get("usage") or {}
    _AI_USAGE.append((u.get("prompt_tokens", 0), u.get("completion_tokens", 0)))

    out = {}
    for row in json.loads(j["choices"][0]["message"]["content"]).get("items", []):
        try:
            k = int(row["id"]) - 1
            if not (0 <= k < len(batch)):
                continue
            line = clean(str(row.get("line") or row.get("translate") or row.get("summary") or "")).strip()
            if len(line) >= 5:
                out[batch[k]["url"]] = truncate_cn(line, 52, hard=60, ratio=0.7)
        except Exception:
            continue
    return out


def ai_briefs(items, cache):
    """并发批量生成单行中文导语。失败只影响该批，返回已成功的部分。"""
    if not AI_API_KEY or not items:
        return {}

    hit, todo = {}, []
    for it in items:
        # ai3 前缀：ai2 存的是 {tr, sm} 两段，格式不同，不复用
        ck = "ai3|" + (it.get("title") or "")[:80]
        val = cache.get(ck)
        if isinstance(val, str) and len(val) >= 5:
            hit[it["url"]] = val
        else:
            todo.append(it)
    if hit:
        print(f"  [AI] 缓存命中 {len(hit)} 条（不重复花钱）")
    if not todo:
        return hit

    batches = [todo[i:i + AI_BATCH] for i in range(0, len(todo), AI_BATCH)]
    print(f"  [AI] {AI_MODEL} 处理 {len(todo)} 条（{len(batches)} 批 × 并发 {AI_WORKERS}）")
    done = 0
    with ThreadPoolExecutor(max_workers=AI_WORKERS) as ex:
        futs = {ex.submit(_ai_one, b): b for b in batches}
        for fu in as_completed(futs):
            batch = futs[fu]
            try:
                got = fu.result()
            except Exception as e:
                print(f"    [AI] 一批失败，该批保留原翻译：{str(e)[:70]}")
                continue
            for it in batch:
                line = got.get(it["url"])
                if line:
                    hit[it["url"]] = line
                    cache["ai3|" + (it.get("title") or "")[:80]] = line
            done += len(batch)
            print(f"    [AI] 进度 {done}/{len(todo)}", flush=True)

    if _AI_USAGE:
        pi = sum(x[0] for x in _AI_USAGE)
        po = sum(x[1] for x in _AI_USAGE)
        print(f"  [AI] token 用量：输入 {pi:,} / 输出 {po:,}")
    return hit


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
            "weight": source.get("weight", 50),
            "summary": clean(e.get("summary", "") or e.get("description", "")),
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
                "weight": 52,
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
                    "weight": 69,
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


def fetch_xiaohongshu():
    """抓取小红书热搜（uapis.cn 免费接口，含真实热度值）"""
    items = []
    try:
        r = requests.get(XHS_API, headers=UA, timeout=20)
        data = r.json().get("list", [])
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
                "desc": "",
                "heat": heat,
            })
        print(f"  [热点] 小红书热搜: {len(items)} 条")
    except Exception as e:
        print(f"  [热点] 小红书热搜: 抓取失败 {str(e)[:50]}")
    return items


def fix_finance_url(platform, url, title):
    """修正财经平台的原文链接（各平台 URL 规则不同，接口给的地址有坑）"""
    url = (url or "").strip()
    if platform == "eastmoney":
        # 东方财富：接口返回的地址缺 .html 后缀，会 404
        if url and not url.endswith(".html"):
            url = url.rstrip("/") + ".html"
    elif platform == "cls":
        # 财联社：接口统一给 /telegraph 列表页，改为按标题搜索，点进去能定位到原文
        import urllib.parse
        url = "https://www.cls.cn/searchPage?keyword=" + urllib.parse.quote(title[:20])
    return url


def _cut_report_title(text, limit):
    """研报标题截断：去掉截断处残留的孤立标点，避免出现「…首华燃气+」这种尾巴"""
    if len(text) <= limit:
        return text
    return text[:limit].rstrip("+、，,·|｜：:；;—- 　")


def _em_report_page(qtype, begin, end, page):
    """拼东方财富研报列表接口地址"""
    return (EM_REPORT_API +
            "?industryCode=*&pageSize=100&industry=*&rating=*&ratingChange=*"
            f"&beginTime={begin}&endTime={end}&pageNo={page}"
            f"&fields=&qType={qtype}&orgCode=&code=*&rcode=&p={page}&pageNumber={page}")


def fetch_em_reports(days=REPORT_DAYS):
    """投行研报：东方财富研报中心，取近 N 天（策略 / 行业 / 个股 三类）

    按「每天最多取一条」的方式采样，保证 45 条能铺满近一个月，
    而不是被同一天的几十篇日报占满。
    """
    end_dt = datetime.now()
    begin_dt = end_dt - timedelta(days=days)
    begin, end = begin_dt.strftime("%Y-%m-%d"), end_dt.strftime("%Y-%m-%d")
    print(f"  [研报] 时间范围 {begin} ~ {end}（近 {days} 天）")

    # qType / 中文标签 / 详情页路径 / 该类最多贡献多少天
    plan = [
        (2, "策略", "strategy", 18),
        (1, "行业", "industry", 14),
        (0, "个股", "stock", 13),
    ]
    # 个股研报每天上百篇且高度同质化，只留有明确观点/评级的
    stock_kw = ["首次覆盖", "深度", "目标价", "上调", "增持", "买入", "强烈推荐",
                "超预期", "龙头", "拐点", "反转", "受益"]

    items, seen_title = [], set()

    for qtype, label, page_key, day_cap in plan:
        buckets = {}          # 日期 -> 该日候选列表
        # 同一天研报量很大（策略约 30 篇/天、个股约 56 篇/天），
        # 页数不够就铺不满一个月，这里翻到凑够天数为止
        for pno in range(1, 16):
            if len(buckets) >= day_cap:
                break
            try:
                data = requests.get(_em_report_page(qtype, begin, end, pno),
                                    headers=UA, timeout=20).json().get("data", [])
            except Exception as e:
                print(f"    [研报] {label} 第{pno}页抓取失败 {str(e)[:50]}")
                break
            if not data:
                break
            for d in data:
                title = clean(d.get("title") or "")
                org = clean(d.get("orgSName") or "")
                info = d.get("infoCode") or ""
                date = (d.get("publishDate") or "")[:10]
                if not title or not org or not info or len(date) < 10:
                    continue
                if qtype == 0 and not any(k in title for k in stock_kw):
                    continue
                key = title[:26]
                if key in seen_title:
                    continue
                seen_title.add(key)
                buckets.setdefault(date, []).append({
                    "title": f"{org}｜{_cut_report_title(title, 46)}",
                    "url": EM_REPORT_PAGE[page_key].format(code=info),
                    "desc": f"{label}研报 · 近30天",
                    "time": date[5:],      # MM-DD
                    "sort": date,
                })

        # 从最近日期往回，每天取 1 条，最多 day_cap 天
        picked = 0
        for date in sorted(buckets, reverse=True):
            if picked >= day_cap:
                break
            items.append(buckets[date][0])
            picked += 1
        print(f"    [研报] {label}研报: {picked} 天 {picked} 条")

    # 按日期倒序混排，从最近到最早铺开一个月
    items.sort(key=lambda x: x["sort"], reverse=True)
    items = items[:REPORT_LIMIT]
    print(f"  [财经] 投行研报: 共 {len(items)} 条（近 {days} 天）")
    return items


def fetch_intl_banks():
    """国际投行动态：Google News 中文搜索（30 天）+ Yahoo Finance 各投行个股新闻

    Google 在本地通常被墙，云端 GitHub Actions 可正常抓取；
    Yahoo 两边都能抓，作为保底，因此本地预览也会有内容。
    两路各自限量后交错排列，避免英文条目把 30 天的中文内容全部挤掉。
    """
    gn, yf, seen = [], [], set()
    gn_quota, yf_quota = 26, 24        # 分源配额

    # ---- 1) Google News：中文投行资讯，覆盖近 30 天（云端有效）----
    try:
        r = requests.get(GN_BANK_URL, headers=UA, timeout=20)
        for e in feedparser.parse(r.content).entries:
            if len(gn) >= gn_quota:
                break
            title = clean(e.get("title", ""))
            link = e.get("link", "")
            if not title or not link:
                continue
            # Google News 标题尾部带「 - 媒体名」，去掉便于阅读
            title = re.sub(r"\s+-\s+[^-]{2,30}$", "", title).strip()
            if not _bank_relevant(title):
                continue
            if title[:24] in seen:
                continue
            seen.add(title[:24])
            ts = to_ts(e.get("published_parsed"))
            gn.append({
                "title": title[:80],
                "url": link,
                "desc": _bank_of(title),
                "time": time.strftime("%m-%d", time.localtime(ts)) if ts else "",
                "lang": "zh",
                "sort": ts or 0,
            })
        print(f"  [国际投行] Google News(中文·30天): {len(gn)} 条")
    except Exception:
        print("  [国际投行] Google News: 不可达（本地正常现象，云端会自动补上）")

    # ---- 2) Yahoo Finance：按股票代码取各行动态（英文，需中文附注）----
    per_bank = 4
    for ticker, bank, _pat in BANK_TICKERS:
        try:
            r = requests.get(YF_BANK_RSS.format(ticker=ticker), headers=UA, timeout=15)
            n = 0
            for e in feedparser.parse(r.content).entries:
                if n >= per_bank or len(yf) >= yf_quota:
                    break
                title = clean(e.get("title", ""))
                link = e.get("link", "")
                if not title or not link or title[:24] in seen:
                    continue
                if not _bank_relevant(title):
                    continue
                seen.add(title[:24])
                ts = to_ts(e.get("published_parsed"))
                yf.append({
                    "title": title,
                    "url": link,
                    "desc": "",
                    "summary": clean(e.get("summary", ""))[:220],
                    "time": time.strftime("%m-%d", time.localtime(ts)) if ts else "",
                    "lang": "en",
                    "bank": bank,
                    "sort": ts or 0,
                })
                n += 1
        except Exception:
            continue
    print(f"  [国际投行] Yahoo Finance(英文): {len(yf)} 条")

    # 各自按时间倒序，再交错合并
    gn.sort(key=lambda x: -x["sort"])
    yf.sort(key=lambda x: -x["sort"])
    merged = []
    for i in range(max(len(gn), len(yf))):
        if i < len(gn):
            merged.append(gn[i])
        if i < len(yf):
            merged.append(yf[i])

    for it in merged:
        it.pop("sort", None)
    merged = merged[:BANK_LIMIT]
    print(f"  [财经] 国际投行: 共 {len(merged)} 条")
    return merged


def fetch_finance():
    """抓取财经新闻：newsapi 多平台 + 华尔街见闻 API"""
    result = {}
    for code, label in FINANCE_PLATFORMS:
        try:
            r = requests.get(HOT_API.format(platform=code), headers=UA, timeout=20)
            data = r.json().get("data", [])
            items = []
            for i, d in enumerate(data[:20]):
                title = clean(d.get("title", ""))
                if not title:
                    continue
                items.append({
                    "title": title,
                    "url": fix_finance_url(code, d.get("url", ""), title),
                    "desc": clean(d.get("content") or d.get("desc") or ""),
                    "time": d.get("publish_time", "")[:16],
                })
            result[label] = items
            print(f"  [财经] {label}: {len(items)} 条")
        except Exception as e:
            print(f"  [财经] {label}: 抓取失败 {str(e)[:50]}")
            result[label] = []

    # 华尔街见闻 7x24（官方 API）
    try:
        r = requests.get(WSCN_API, headers=UA, timeout=20)
        items = []
        for it in r.json().get("data", {}).get("items", [])[:30]:
            title = clean(it.get("title") or it.get("content_text") or "")
            if not title:
                continue
            title = title[:80]
            ts = it.get("display_time", 0)
            items.append({
                "title": title,
                "url": it.get("uri") or "https://wallstreetcn.com/live/global",
                "desc": clean(it.get("content_text") or "")[:120],
                "time": time.strftime("%m-%d %H:%M", time.localtime(ts)) if ts else "",
            })
        result["华尔街见闻"] = items
        print(f"  [财经] 华尔街见闻: {len(items)} 条")
    except Exception as e:
        print(f"  [财经] 华尔街见闻: 抓取失败 {str(e)[:50]}")
        result["华尔街见闻"] = []

    # 投行研报：东方财富研报中心，取近 30 天（真正的研报，带机构/日期/评级）
    result["投行研报"] = fetch_em_reports()

    # 国际投行：外资投行动态与观点，近 30 天
    result["国际投行"] = fetch_intl_banks()
    return result


def _sport_cat(title):
    """按标题关键词判定运动项目，认不出就归到「综合」"""
    low = title.lower()
    for cat, kws in SPORT_CATS:
        if any(k.lower() in low for k in kws):
            return cat
    return "综合"


def _fetch_hupu_board(board, per=8):
    """抓虎扑某版块的资讯帖，优先 [流言板]（官方转载，质量最好）"""
    out = []
    try:
        r = requests.get(f"https://bbs.hupu.com/{board}", headers=UA, timeout=18)
        if r.status_code != 200:
            return out
        r.encoding = "utf-8"
        links = re.findall(r'<a[^>]+href="(/[0-9]+\.html)"[^>]*>(.*?)</a>', r.text, re.S)
        items = []
        for u, t in links:
            t = re.sub(r"<[^>]+>", "", t)
            t = re.sub(r"\s+", "", t).replace("&amp;", "&").replace("&quot;", '"')
            # 跳过广告位/活动页（标题本身就是链接地址）
            if "http" in t or "hupu.com" in t or "activity-static" in u:
                continue
            if 12 < len(t) < 120:
                items.append((t, "https://bbs.hupu.com" + u))
        lb = [x for x in items if "流言板" in x[0]]
        # 只取官方转载的 [流言板]，普通用户帖质量太杂（预测比分、阵容讨论等）
        pool = lb
        seen = set()
        for t, u in pool:
            t2 = t.replace("[流言板]", "").strip()
            if t2 in seen:
                continue
            seen.add(t2)
            out.append((t2, u))
            if len(out) >= per:
                break
    except Exception:
        pass
    return out


# ---------- 腾讯体育比分（JS 渲染页，但数据内嵌在 __INITIAL_STATE__ 里）----------
QQ_SPORTS_HOME = "https://sports.qq.com/"
QQ_SCORE_PAGE = "https://sports.qq.com/kbsweb/game.htm?mid={mid}"
# 电竞赛事归「游戏资讯」，体育这边不重复收录
SCORE_NOISE = ["S赛", "LPL", "KPL", "LCK", "LDL", "英雄联盟", "王者荣耀",
               "CS2", "DOTA", "无畏契约", "VALORANT"]


def fetch_qq_scores():
    """腾讯体育：解析首页内嵌的 window.__INITIAL_STATE__，取已结束赛事的比分

    这个页面是 Vue 渲染的，标签里没有一条新闻文本，但整份数据被序列化进了
    script 里。所以不需要无头浏览器，切出那段 JSON 再 json.loads 即可。
    matchPeriod == "2" 表示比赛已结束，此时 leftGoal / rightGoal 是最终比分。
    """
    out, seen = [], set()
    try:
        r = requests.get(QQ_SPORTS_HOME, headers=UA, timeout=25)
        r.encoding = "utf-8"
        html = r.text
        i = html.find("window.__INITIAL_STATE__=")
        if i < 0:
            print("  [体育] 腾讯比分: 页面里没有内嵌状态")
            return out
        j = html.find("{", i)
        depth, end = 0, None
        for k in range(j, len(html)):
            ch = html[k]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = k + 1
                    break
        state = json.loads(html[j:end])
    except Exception as e:
        print(f"  [体育] 腾讯比分: 失败 {str(e)[:50]}")
        return out

    rows = []

    def walk(o, dep=0):
        if dep > 12:
            return
        if isinstance(o, dict):
            if o.get("leftName") and o.get("rightName") and str(o.get("matchPeriod")) == "2":
                rows.append(o)
            for v in o.values():
                walk(v, dep + 1)
        elif isinstance(o, list):
            for v in o[:120]:
                walk(v, dep + 1)

    walk(state)

    for m in rows:
        desc = clean(str(m.get("matchDesc") or ""))
        left = clean(str(m.get("leftName") or ""))
        right = clean(str(m.get("rightName") or ""))
        lg, rg = m.get("leftGoal"), m.get("rightGoal")
        if not left or not right or lg is None or rg is None:
            continue
        if any(k in desc for k in SCORE_NOISE):
            continue
        mid = str(m.get("mid") or "")
        if not mid:
            continue
        title = f"{desc} · {left} {lg}-{rg} {right}" if desc else f"{left} {lg}-{rg} {right}"
        if title[:20] in seen:
            continue
        seen.add(title[:20])
        ts = 0
        st = str(m.get("startTime") or "")
        try:
            ts = int(time.mktime(time.strptime(st, "%Y-%m-%d %H:%M:%S")))
        except Exception:
            pass
        out.append({
            "title": title[:60],
            "url": QQ_SCORE_PAGE.format(mid=mid),
            "source": "腾讯体育",
            "time": st[5:16] if st else "",
            "ts": ts,
        })

    out.sort(key=lambda x: -(x.get("ts") or 0))

    # 按项目轮转取样：一天十几场 NBA 会把凌晨的欧冠、网球全压到列表底部
    by_cat = {}
    for it in out:
        by_cat.setdefault(_sport_cat(it["title"]), []).append(it)
    picked, idx = [], 0
    while len(picked) < 40:
        added = False
        for arr in by_cat.values():
            if idx < len(arr):
                picked.append(arr[idx])
                added = True
                if len(picked) >= 40:
                    break
        if not added:
            break
        idx += 1

    for it in picked:
        it.pop("ts", None)
    return picked


def fetch_sports():
    """体育新闻：新浪体育 + 天空体育 + 虎扑，按项目分栏目，彻底剔除博彩"""
    buckets = {"足球": [], "篮球": [], "赛车": [], "综合": []}
    seen, skipped = set(), 0

    def add(title, url, source, ts=0, cat=None, summary="", force_cat=False):
        nonlocal skipped
        title = clean(title)
        if not title or not url:
            return
        if any(k in title for k in SPORTS_NOISE):
            skipped += 1
            return
        # CBA 常规赛 / 集训名单这类不进篮球栏，只保留决赛冠军节点
        if "CBA" in title and not any(k in title for k in CBA_KEEP):
            skipped += 1
            return
        if title[:22] in seen:
            return
        seen.add(title[:22])
        # 标题关键词优先（天空体育的「足球」源里会混 F1、篮球），
        # 认不出来时才退回数据源自带的分类
        guess = _sport_cat(title)
        if force_cat and cat in buckets:
            c = cat
        else:
            c = guess if guess != "综合" or cat not in buckets else cat
        buckets[c].append({
            "title": title,
            "url": url,
            "source": source,
            "time": time.strftime("%m-%d %H:%M", time.localtime(ts)) if ts else "",
            "ts": ts,
            "summary": summary,
        })

    # 1) 新浪体育滚动（中文）
    try:
        r = requests.get(SINA_SPORTS_API, headers=UA, timeout=20)
        data = r.json().get("result", {}).get("data", [])
        for d in data[:40]:
            add(d.get("title", ""), d.get("url", ""), "新浪体育",
                int(d.get("ctime", 0) or 0))
        print(f"  [体育] 新浪体育: 抓 {len(data[:40])} 条")
    except Exception as e:
        print(f"  [体育] 新浪体育: 失败 {str(e)[:50]}")

    # 2) 天空体育 RSS（英文战报，按运动分类）
    for cat, url in SKY_SPORTS_RSS:
        try:
            r = requests.get(url, headers=UA, timeout=18)
            n = 0
            for e in feedparser.parse(r.content).entries:
                t = clean(e.get("title", ""))
                if any(j.lower() in t.lower() for j in SKY_JUNK):
                    continue
                add(t, e.get("link", ""), "天空体育",
                    to_ts(e.get("published_parsed")), None,
                    clean(e.get("summary", ""))[:400])
                n += 1
            print(f"  [体育] 天空体育·{cat}: {n} 条")
        except Exception as e:
            print(f"  [体育] 天空体育·{cat}: 失败 {str(e)[:40]}")

    # 3) 虎扑（中文战报与流言）
    for cat, board in HUPU_SPORTS:
        got = _fetch_hupu_board(board, per=10 if board == "nba" else 8)
        kept = 0
        for t, u in got:
            # CBA 版块整版都是 CBA，只留决赛/冠军类话题
            if board == "cba" and not any(k in t for k in CBA_KEEP):
                continue
            add(t, u, "虎扑", 0, cat)
            kept += 1
        print(f"  [体育] 虎扑·{board}: 抓 {len(got)} 条，留 {kept} 条")

    # 4) NBA 英文源：CBA 不要、国内篮球也不要，篮球栏靠它撑住
    for name, url in NBA_RSS:
        try:
            r = requests.get(url, headers=UA, timeout=18)
            n = 0
            for e in feedparser.parse(r.content).entries:
                tt = clean(e.get("title", ""))
                low = tt.lower()
                if any(k in low for k in NBA_NOISE):
                    continue
                add(tt, e.get("link", ""), name, to_ts(e.get("published_parsed")),
                    "篮球", clean(e.get("summary", ""))[:400], force_cat=True)
                n += 1
            print(f"  [体育] {name}·NBA: {n} 条")
        except Exception as e:
            print(f"  [体育] {name}·NBA: 失败 {str(e)[:40]}")

    # 各子栏目按来源轮转取样：虎扑的帖子没有时间戳，纯按时间排会被全部挤掉
    def pick_mixed(lst, cap=18):
        by_src = {}
        for it in lst:
            by_src.setdefault(it["source"], []).append(it)
        for arr in by_src.values():
            arr.sort(key=lambda x: -(x.get("ts") or 0))
        out, i = [], 0
        while len(out) < cap:
            added = False
            for arr in by_src.values():
                if i < len(arr):
                    out.append(arr[i])
                    added = True
                    if len(out) >= cap:
                        break
            if not added:
                break
            i += 1
        return out

    # 4) 腾讯体育：已结束赛事的比分（结构化，最硬的结果数据）
    scores = fetch_qq_scores()
    print(f"  [体育] 腾讯比分: {len(scores)} 场已结束")

    result = {}
    allitems = []
    for cat in ("足球", "篮球", "赛车", "综合"):
        lst = buckets.get(cat, [])
        lst.sort(key=lambda x: -(x.get("ts") or 0))
        result[cat] = pick_mixed(lst, 18)
        allitems.extend(lst)

    # 「全部」按来源配额混合，避免某一个源把列表占满
    quota = {"新浪体育": 15, "天空体育": 14, "虎扑": 10, "CBS体育": 10}
    mix = []
    for src in ("新浪体育", "天空体育", "虎扑", "CBS体育"):
        mix.extend([x for x in allitems if x["source"] == src][:quota.get(src, 10)])
    mix.sort(key=lambda x: -(x.get("ts") or 0))
    # 赛果放最前：体育板块最该先看到的是「今天打完的结果」
    result = {"今日赛果": scores, "全部": mix[:45], **result}

    # 天空体育是英文源，给标题配一句中文摘要（只作用于这一家，别的源不动）
    cache = _cache_load()
    done, n_tr = set(), 0
    for lst in result.values():
        for it in lst:
            if it.get("source") not in ("天空体育", "CBS体育") or it["url"] in done:
                continue
            done.add(it["url"])
            raw = it.pop("summary", "")
            if not raw or n_tr >= 60:
                continue
            src = first_sentence(raw, 260) or it["title"]
            # 腾讯在海外 IP 上不可达（云端就抓不到），所以必须再兜一层 Google
            cn = translate_qq(src, cache) or translate(src, cache)
            if cn:
                it["desc"] = truncate_cn(cn, 46, hard=60, ratio=0.7)
                n_tr += 1
    if n_tr:
        _cache_save(cache)
    print(f"  [体育] 天空体育中文摘要: {n_tr} 条")
    for it in allitems:
        it.pop("summary", None)

    for it in allitems:
        it.pop("ts", None)
    total = sum(len(v) for k, v in result.items() if k not in ("全部", "今日赛果"))
    note = f"（剔除博彩 {skipped} 条）" if skipped else ""
    print(f"  [体育] 资讯共 {total} 条{note}，赛果 {len(scores)} 场")
    return result


# 完美世界 CS2 官网：列表在 HTML 锚点里，日期藏在 /article/details/YYYYMMDD/ 路径中
WANMEI_CS_URL = "https://csgo.wanmei.com/"
# 腾讯 CMS 赛事内容接口：target=25 是英雄联盟赛事官方报道（S 赛、MSI 等）
CMC_NEWS_API = ("https://apps.game.qq.com/cmc/zmMcnTargetContentList"
                "?r0=json&page=1&num={num}&target={target}&source=web_pc&_=1")
CMC_LOL_TARGET = 25
LPL_NEWS_PAGE = "https://lpl.qq.com/es/news_detail.shtml?nid={docid}"


def _fetch_hltv(per=12):
    """HLTV 官方 RSS：CS 项目最权威的资讯源，英文标题 + 英文摘要

    Cloudflare 对浏览器 UA 返回 403，只有非浏览器 UA 才放行（实测 curl UA=200）。
    云端若被拦，函数返回空列表，不影响其他板块。
    """
    out = []
    try:
        r = None
        # Cloudflare 对 UA 敏感，两个非浏览器 UA 依次试一次
        for hdrs in (HLTV_HEADERS,
                     {"User-Agent": "Mozilla/5.0 (compatible; Googlebot/2.1; "
                                    "+http://www.google.com/bot.html)"}):
            r = requests.get(HLTV_RSS, headers=hdrs, timeout=20)
            if r.status_code == 200:
                break
        if r is None or r.status_code != 200:
            print(f"  [游戏] HLTV: HTTP {getattr(r, 'status_code', '?')}"
                  f"（被 Cloudflare 拦截，跳过，其他板块不受影响）")
            return []
        for e in feedparser.parse(r.content).entries:
            title = clean(e.get("title", ""))
            link = e.get("link", "")
            if not title or not link:
                continue
            ts = to_ts(e.get("published_parsed"))
            out.append({
                "title": title[:90],
                "url": link,
                "source": "HLTV",
                "summary": clean(e.get("summary", ""))[:260],
                "time": time.strftime("%m-%d", time.localtime(ts)) if ts else "",
                "ts": ts,
            })
    except Exception as e:
        print(f"  [游戏] HLTV: 失败 {str(e)[:40]}")
        return []
    out.sort(key=lambda x: -(x["ts"] or 0))
    out = out[:per]
    print(f"  [游戏] HLTV: {len(out)} 条")
    return out


def _fetch_wanmei_cs(per=10):
    """完美世界 CS2 官网新闻（服务端渲染，锚点里带日期）"""
    out, seen = [], set()
    try:
        r = requests.get(WANMEI_CS_URL, headers=UA, timeout=20)
        r.encoding = r.apparent_encoding or "utf-8"
        pat = r'<a[^>]+href="([^"]*?/article/details/(\d{8})/(\d+)\.html)"[^>]*>(.*?)</a>'
        for href, date, aid, raw in re.findall(pat, r.text, re.S):
            title = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", raw)).strip()
            if len(title) < 8 or aid in seen:
                continue
            seen.add(aid)
            if href.startswith("//"):
                url = "https:" + href
            elif href.startswith("http"):
                url = href
            else:
                url = "https://www.csgo.com.cn" + href
            try:
                ts = int(time.mktime(time.strptime(date, "%Y%m%d")))
            except Exception:
                ts = 0
            out.append({
                "title": title[:72],
                "url": url,
                "source": "完美世界",
                "time": f"{date[4:6]}-{date[6:]}",
                "ts": ts,
            })
            if len(out) >= per * 3:
                break
    except Exception as e:
        print(f"  [游戏] 完美世界: 失败 {str(e)[:40]}")
    # 官网顶部是置顶的老文章（3 月、9 月各一篇），按日期重排并丢掉过期的
    cutoff = time.time() - 120 * 86400
    out = [x for x in out if not x["ts"] or x["ts"] >= cutoff]
    out.sort(key=lambda x: -(x["ts"] or 0))
    return out[:per]


def _fetch_lpl_news(per=10):
    """腾竞官方英雄联盟赛事报道（腾讯 CMS 接口，返回标题+发布时间+文档号）"""
    out = []
    try:
        r = requests.get(CMC_NEWS_API.format(num=per * 2, target=CMC_LOL_TARGET),
                         headers=UA, timeout=18)
        for it in (r.json().get("data") or {}).get("result") or []:
            title = clean(it.get("sTitle") or "")
            docid = it.get("iDocID")
            if not title or not docid:
                continue
            # 海报 / 宣传片 / 壁纸属于物料，不是报道
            if any(k in title for k in ("海报", "宣传片", "壁纸", "表情包")):
                continue
            created = str(it.get("sCreated") or "")
            out.append({
                "title": title[:72],
                "url": LPL_NEWS_PAGE.format(docid=docid),
                "source": "腾竞官方",
                "time": created[5:16] if len(created) >= 16 else "",
                "ts": 0,
            })
            if len(out) >= per:
                break
    except Exception as e:
        print(f"  [游戏] 腾竞官方: 失败 {str(e)[:40]}")
    return out


# ---------- 小黑盒（纯 SPA，接口要 _rnd 动态签名，只有跑 JS 才能渲染）----------
XHH_HOME = "https://www.xiaoheihe.cn/app/home"

# 本地默认不启用：本机不装 180MB 浏览器；GitHub Actions 会自动注入
# GITHUB_ACTIONS=true，所以在云端无需配置即可生效。
PLAYWRIGHT_ENABLED = (
    os.environ.get("ENABLE_PLAYWRIGHT", "").strip() == "1"
    or os.environ.get("GITHUB_ACTIONS", "").strip().lower() == "true"
)


def _launch_browser(p):
    """优先驱动系统已装的浏览器，避免再下一次 Chromium"""
    for ch in ("chrome", "msedge"):
        try:
            return p.chromium.launch(channel=ch, headless=True)
        except Exception:
            continue
    return p.chromium.launch(headless=True)


def fetch_xiaoheihe(limit=60):
    """小黑盒：首页 + 社区 + PC游戏话题，跑两轮合并去重

    首页是 @heybox-spa 的空壳（3.8KB），接口要 _rnd 签名，静态请求一条都拿不到，
    只有真正执行 JS 才渲染得出来。三个流内容互不重复，且每次刷新都会换一批，
    所以跑两轮能把候选池从 35 条翻到 60 条以上。
    """
    if not PLAYWRIGHT_ENABLED:
        print("  [游戏] 小黑盒: 本地跳过（仅云端启用）")
        return []
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        print("  [游戏] 小黑盒: playwright 未安装，跳过")
        return []

    sel = "a[href*='/app/bbs/link/']"
    out, seen = [], set()
    try:
        with sync_playwright() as p:
            browser = _launch_browser(p)
            page = browser.new_page(locale="zh-CN",
                                    viewport={"width": 1440, "height": 900})
            for rnd in range(XHH_ROUNDS):
                for url in XHH_FEEDS:
                    try:
                        # 不用 networkidle：小黑盒有长连接，永远等不到 idle
                        page.goto(url, wait_until="domcontentloaded", timeout=45000)
                        page.wait_for_selector(sel, timeout=20000)
                    except Exception:
                        continue        # 单个流失败不影响其他流
                    for a in page.query_selector_all(sel):
                        el = a.query_selector(".bbs-content__title")
                        title = clean(el.inner_text()) if el else ""
                        href = a.get_attribute("href") or ""
                        if not title or not href:
                            continue
                        full = href if href.startswith("http") else "https://www.xiaoheihe.cn" + href
                        if full in seen:
                            continue
                        seen.add(full)
                        out.append({
                            "title": title[:80],
                            "url": full,
                            "source": "小黑盒",
                            "time": "",
                            "ts": 0,
                        })
                if len(out) >= limit:
                    break
            browser.close()
    except Exception as e:
        print(f"  [游戏] 小黑盒: 失败 {str(e)[:50]}")
    print(f"  [游戏] 小黑盒: {len(out)} 条（{XHH_ROUNDS} 轮 × {len(XHH_FEEDS)} 个信息流去重）")
    return out



GAME_TENCENT_BAN = ["王者荣耀", "瓦洛兰特", "无畏契约", "VALORANT", "金铲铲",
                    "云顶之弈", "和平精英", "穿越火线", "元梦之星", "英雄联盟",
                    "LPL", "KPL"]

# 来源权重：小黑盒略高于游研社/游民星空，后者略高于机核/触乐
GAME_SRC_WEIGHT = {"小黑盒": 100, "游研社": 95, "游民星空": 95, "机核": 85, "触乐": 85}
# 每源进入「近期热门」的条数上限，避免一个源刷满一屏
GAME_SRC_CAP = {"小黑盒": 12, "游民星空": 8, "机核": 8, "游研社": 6, "触乐": 6}
# 社区帖很少写「发售/版本」这种书面词，补一批口语表达，只在收录小黑盒时放宽
XHH_KW = ["通关", "喜加一", "史低", "打折", "联机", "开服", "内测", "试玩",
          "卡池", "抽卡", "赛季", "攻略", "剧透", "预购", "炸服", "平衡性"]
# 首页只有 10 条，再叠两个信息流（社区 / PC游戏话题）内容互不重复，合计约 37 条
XHH_FEEDS = [
    "https://www.xiaoheihe.cn/app/home",
    "https://www.xiaoheihe.cn/app/bbs/home",
    "https://www.xiaoheihe.cn/app/topic/link/1",
]
# 「近期热门」主要看新游发售与报道，命中这些词的小黑盒帖优先
XHH_RELEASE_KW = ["发售", "上线", "定档", "预售", "预购", "抢先体验", "公测", "封测",
                  "试玩", "新品节", "新作", "续作", "首曝", "公布", "上市", "开服",
                  "发售日", "上线时间", "DLC", "资料片", "重制", "复刻", "评测"]
# 社区帖大多是日记体闲聊，只有带书名号 / 发售词 / 平台评测词才算「资讯」
XHH_NEWSY_KW = XHH_RELEASE_KW + [
    "Steam", "PS5", "PS4", "Xbox", "Switch", "Epic", "任天堂", "索尼",
    "IGN", "MC评分", "销量", "下架", "涨价", "史低", "打折", "喜加一",
    "前作", "续作", "试玩版", "Demo", "补丁", "更新"]
# 社区帖常只写游戏名、不加书名号，认这些名字
XHH_GAME_NAMES = ["影之刃", "三角洲行动", "黑神话", "使命召唤", "战地", "塞尔达",
                  "宝可梦", "最终幻想", "生化危机", "艾尔登法环", "只狼", "赛博朋克",
                  "巫师", "上古卷轴", "辐射", "魔兽", "暗黑", "英雄联盟", "CS2",
                  "无畏契约", "原神", "崩坏", "绝区零", "鸣潮", "明日方舟", "深空当铺",
                  "死亡搁浅", "战神", "荒野大镖客", "我的世界", "泰拉瑞亚", "星露谷",
                  "空洞骑士", "杀戮尖塔", "GTA", "COD", "文明", "刺客信条",
                  "战锤", "星际争霸", "守望先锋", "Apex", "永劫无间", "剑星", "天国拯救"]
# 盘点/推荐/攻略这类内容帖也算「报道」，纯日记体不算
XHH_CONTENT_KW = ["盘点", "推荐", "测评", "评测", "攻略", "值得一试", "合集", "排行",
                  "对比", "上手", "体验报告", "财报", "行业"]
# 书名号可能是书/电影，必须再过一道游戏语境
XHH_GAME_CTX = ["游戏", "玩家", "发售", "上线", "评测", "攻略", "3A", "主机", "Steam",
                "DLC", "版本", "玩法", "单机", "联机", "关卡", "剧情", "开发", "厂商"]
# 社区帖常直接写游戏绰号/译名，不带书名号也不含「发售」这类书面词，单列一批补召回。
# 只放指向明确的专名，不放「游戏」「玩家」这类泛词，否则「舍友配主机该提刀还是提臀」也会被收进来
XHH_GAME_ADD = ["PUBG", "绝地求生", "异人之下", "战术小队", "阿卡姆", "蝙蝠侠",
                "彩虹六号", "塔科夫", "逃离塔科夫", "APEX", "暗区突围", "模拟人生",
                "如龙", "女神异闻录", "激战2", "猛兽派对", "雾锁王国", "幻兽帕鲁",
                "王国之泪", "机甲战魔", "潜行者", "剑星", "漫威刀锋战士"]
# 赛事/厂商/研发三类专用词：都是「一看就是资讯」的词，避免用「决赛」「赛季」这类口语词
XHH_ESPORTS_KW = ["S赛", "世界赛", "MSI", "Major", "LCK", "LPL", "KPL", "夺冠", "赛点"]
XHH_INDUSTRY_KW = ["Unity", "Unreal", "Epic", "V社", "Valve", "销量", "营收", "收购",
                   "裁员", "工作室", "引擎", "微软", "任天堂", "索尼", "财报"]
XHH_PLAY_KW = ["攻略", "打法", "配装", "副本", "图鉴", "重制", "续作"]
# 首页每次刷新内容完全不同，多跑几轮能把候选池翻倍；云端订阅制不用省这点时间
XHH_ROUNDS = 3

# 电竞筛选：LPL 只看世界赛，CS 只看大赛
LOL_WORLD_KW = ["全球总决赛", "世界赛", "季中冠军赛", "MSI", "世界锦标赛", "S赛", "世界冠军"]
# 虎扑习惯写「S16淘汰赛」而不是「全球总决赛」，用正则兜住 S10 之后的届次
# 注意不能用 \b：中文也算 \w，S16 后面跟汉字时 \b 不成立
LOL_WORLD_RE = re.compile(r"S1\d(?!\d)|(?<![A-Za-z])S[6-9](?!\d)")
# 腾竞会发纯口号（「狭路相逢，勇者向前！」），没有任何赛事信号的不收录
LOL_SIGNAL_KW = ["对阵", "赛程", "晋级", "挺进", "止步", "淘汰", "战胜", "击败", "比分",
                 "夺冠", "加冕", "出线", "生死局", "MVP", "阵容", "首发", "决赛", "半决赛",
                 "四强", "八强", "瑞士轮", "BLG", "TES", "IG", "JDG", "WBG", "LNG", "EDG",
                 "RNG", "T1", "GEN", "DK", "HLE", "KT", "KDF", "BRO", "NS"]
CS_MAJOR_KW = ["IEM", "BLAST", "PGL", "Major", "major", "ESL", "卡托维兹", "科隆",
               "RMR", "冠军组", "传奇组", "挑战者组", "安特卫普", "里约", "哥本哈根",
               "奥斯汀", "布达佩斯", "上海Major", "世界锦标赛"]
CS_DOMESTIC_NOISE = ["PNL", "高校", "城市赛", "网吧", "网咖", "全民赛", "校园",
                     "国服", "完美世界城市", "高校联赛", "CAC"]

# 世界赛「对阵 / 结果」信号：真正的比赛信息才置顶
LOL_WORLD_MATCH_KW = ["对阵", "赛程", "预告", "晋级", "挺进", "止步", "淘汰", "战胜",
                      "击败", "扳倒", "比分", "夺冠", "加冕", "出线", "生死局",
                      "BO3", "BO5", "bo3", "bo5", "3-0", "3-1", "3-2"]
# 选手言论 / 返图不是赛果，不参与置顶
LOL_COMMENT_NOISE = ["谈", "发文", "分享", "表示", "评论", "社媒", "直言", "回应",
                     "采访", "晒", "认为", "觉得", "返图", "聚餐", "合照"]
# 采访 / 花絮 / 活动招募不是赛事信息
LOL_NOISE = ["MINITALK", "麦克疯", "TOP5", "观赛活动", "HELLO WORLDS", "中场时间",
             "云顶之弈", "招募", "抽奖", "壁纸", "表情包", "海报", "宣传片",
             "选手故事", "纪录片", "幕后", "生日快乐", "直播预告"]

# HLTV：CS 项目最权威的资讯源（英文，需中文摘要）。
# 注意：Cloudflare 会拦浏览器 UA，用非浏览器 UA 才拿得到 RSS（实测 curl UA 返回 200）
HLTV_RSS = "https://www.hltv.org/rss/news"
HLTV_HEADERS = {"User-Agent": "curl/8.4.0", "Accept": "application/rss+xml, */*"}
# 翻译引擎不认识 CS 黑话，先把缩写展开成普通英文，译后再修一遍高频错译
HLTV_PRE = [("compatriot", "fellow countryman"),
            ("talent announced", "commentators announced")]
HLTV_FIX = [("执行副总裁奖", "EVP 奖"), ("执行副总裁", "EVP"), ("最有价值球员", "MVP"),
            ("球员", "选手"), ("奇幻游戏", "竞猜"), ("奇幻", "竞猜"),
            ("赛点王牌", "赛点局"), ("咬大小件", "短讯合集"), ("点头", "入选"),
            ("反恐精英新闻", "CS 新闻"), ("反恐精英", "CS")]
# 固定栏目直接给中文说明，比机翻通顺
HLTV_TITLE_DESC = {"Short news": "本周短讯合集：多条 CS 小新闻汇总"}

# 电竞排序权重：官方大赛源 > 国际权威源 > 社区转载
ESPORTS_SRC_WEIGHT = {"腾竞官方": 96, "HLTV": 94, "完美世界": 90,
                      "英雄联盟": 82, "CS2": 80, "王者荣耀": 74, "PUBG": 72}


def _fetch_gamersky(per=20):
    """游民星空新闻列表（RSS 已关，只能解析 HTML；同页混有娱乐新闻，靠关键词过滤）"""
    out, seen = [], set()
    try:
        r = requests.get("https://www.gamersky.com/news/", headers=UA, timeout=20)
        r.encoding = "utf-8"
        pat = r'<a[^>]+href="(https?://www\.gamersky\.com/news/(\d{6})/(\d+)\.shtml)"[^>]*>(.*?)</a>'
        now = time.time()
        for url, ym, aid, raw in re.findall(pat, r.text, re.S):
            title = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", raw)).strip()
            if len(title) < 8 or aid in seen:
                continue
            seen.add(aid)
            # 列表本身按时间倒序，用位置倒推时间，让排序更符合直觉
            ts = int(now) - len(out) * 900
            out.append({
                "title": title[:80],
                "url": url,
                "source": "游民星空",
                "time": time.strftime("%m-%d", time.localtime(ts)),
                "ts": ts,
            })
            if len(out) >= per * 3:
                break
    except Exception as e:
        print(f"  [游戏] 游民星空: 失败 {str(e)[:40]}")
    return out


def _game_is_banned(title):
    """腾讯系网游不列入「近期热门」（电竞板块另有入口）"""
    low = title.lower()
    return any(k.lower() in low for k in GAME_TENCENT_BAN)


def _interleave_sources(items, max_run=2):
    """按分数顺序重排，但同一来源不连续超过 max_run 条"""
    pool, out = list(items), []
    while pool:
        pick = 0
        for i, it in enumerate(pool):
            if len(out) < max_run or any(x["source"] != it["source"] for x in out[-max_run:]):
                pick = i
                break
        out.append(pool.pop(pick))
    return out


def _game_score(it, now_ts):
    """排序依据：来源权重 + 时效热度 + 新游关键词，不对外展示分数"""
    w = GAME_SRC_WEIGHT.get(it["source"], 80)
    ts = it.get("ts") or now_ts
    age_h = max(0.0, (now_ts - ts) / 3600.0)
    rec = max(0.0, 8.0 - age_h * 0.4)
    hot_b = 6.0 if any(k in it["title"] for k in GAME_HOT_KW) else 0.0
    # 用户要求：小黑盒主要看新游发售与报道，命中的再加一档
    if it["source"] == "小黑盒" and any(k in it["title"] for k in XHH_RELEASE_KW):
        hot_b += 6.0
    # 同权重下加一点确定性抖动，避免同类标题扎堆
    jitter = (sum(ord(c) for c in it["title"][:12]) % 7) - 3
    return w + rec + hot_b + jitter


def _lol_is_world_match(title):
    """世界赛的「对阵 / 结果」才置顶：选手言论、返图、花絮都排除"""
    if not any(k in title for k in LOL_WORLD_KW) and not LOL_WORLD_RE.search(title):
        return False
    if any(k in title for k in LOL_COMMENT_NOISE):
        return False
    return any(k in title for k in LOL_WORLD_MATCH_KW)


# 电竞标签：项目 + 来源 + 赛事，一眼能看出这是什么新闻
ESPORTS_GAME_TAG = {
    "腾竞官方": "LOL", "英雄联盟": "LOL",
    "HLTV": "CS", "完美世界": "CS", "CS2": "CS",
    "王者荣耀": "王者荣耀", "PUBG": "PUBG",
}
ESPORTS_PLATFORM_TAG = {
    "腾竞官方": "腾竞官方", "HLTV": "HLTV", "完美世界": "完美世界",
    "英雄联盟": "虎扑", "CS2": "虎扑", "王者荣耀": "虎扑", "PUBG": "虎扑",
}
CS_EVENT_TAGS = [("Major", ["Major", "major", "RMR"]), ("IEM", ["IEM"]),
                 ("BLAST", ["BLAST"]), ("PGL", ["PGL"]),
                 ("ESL", ["ESL", "卡托维兹", "科隆"]),
                 ("世界锦标赛", ["世界锦标赛", "World Championship"])]


def _esports_tags(it):
    """给电竞条目打标签：项目 / 来源 / 赛事"""
    src, title = it["source"], it["title"]
    game = ESPORTS_GAME_TAG.get(src, "电竞")
    tags = [game]
    plat = ESPORTS_PLATFORM_TAG.get(src)
    if plat:
        tags.append(plat)
    if game == "CS":
        for name, kws in CS_EVENT_TAGS:
            if any(k in title for k in kws):
                tags.append(name)
                break
    elif game == "LOL":
        if any(k in title for k in LOL_WORLD_KW) or LOL_WORLD_RE.search(title):
            tags.append("世界赛")
        else:
            tags.append("LPL")
    elif game == "王者荣耀":
        tags.append("KPL")
    elif game == "PUBG":
        for name in ("PGC", "PCL", "PWS"):
            if name in title:
                tags.append(name)
                break
    return tags


def _esports_score(it, now_ts):
    """电竞内部排序：来源权重 + 时效 + 大赛关键词，分数不对外展示"""
    w = ESPORTS_SRC_WEIGHT.get(it["source"], 75)
    ts = it.get("ts") or 0
    # 没有时间戳（虎扑帖子）不享受时效加成，否则会被当成刚发布而刷屏
    age_h = max(0.0, (now_ts - ts) / 3600.0) if ts else 999.0
    rec = max(0.0, 10.0 - age_h * 0.25)      # 40 小时衰减到 0
    big = 6.0 if any(k in it["title"] for k in CS_MAJOR_KW) else 0.0
    jitter = (sum(ord(c) for c in it["title"][:12]) % 7) - 3
    return w + rec + big + jitter


def fetch_games():
    """游戏资讯：近期热门（小黑盒 + 游戏媒体 RSS）+ 电竞资讯（腾竞 / 完美世界 / 虎扑）"""
    hot, esports, seen = [], [], set()

    src_used = {}

    def add_hot(title, url, source, ts=0, strict=False, extra=None):
        if not title or not url or title[:22] in seen:
            return False
        if _game_is_banned(title):
            return False
        # 会员抽奖 / 福利帖不是新闻
        if "抽奖" in title:
            return False
        pool = GAME_KW + (extra or [])
        hit = any(k.lower() in title.lower() for k in pool)
        # 严格模式必须命中游戏词：游民星空同页混着影视娱乐，书名号不可靠
        if not hit and (strict or "《" not in title):
            return False
        if src_used.get(source, 0) >= GAME_SRC_CAP.get(source, 8):
            return False
        src_used[source] = src_used.get(source, 0) + 1
        seen.add(title[:22])
        hot.append({
            "title": title,
            "url": url,
            "source": source,
            "time": time.strftime("%m-%d", time.localtime(ts)) if ts else "",
            "ts": ts,
        })
        return True

    # 1) 小黑盒：社区首页（云端才跑），权重最高
    # 小黑盒首页是实时信息流，没有时间字段，按当天算（否则列表里没有日期）
    now0 = int(time.time())
    n = 0
    xhh_all = fetch_xiaoheihe(limit=60)
    dropped = []
    for it in xhh_all:
        tt = it["title"]
        # 社区帖大小写混用（steam / STEAM / Steam），统一转小写再比，否则小写的全漏
        low = tt.lower()
        # 社区帖只有带书名号 / 发售词 / 平台评测词才算资讯，纯闲聊不进
        hit_newsy = any(k.lower() in low for k in XHH_NEWSY_KW)
        hit_name = any(k.lower() in low for k in XHH_GAME_NAMES + XHH_GAME_ADD)
        hit_body = any(k.lower() in low for k in XHH_CONTENT_KW)
        hit_esp = any(k.lower() in low for k in XHH_ESPORTS_KW)
        hit_ind = any(k.lower() in low for k in XHH_INDUSTRY_KW)
        hit_play = any(k.lower() in low for k in XHH_PLAY_KW)
        # 只有书名号时，还要带游戏语境，否则《查理九世》这类书评也会混进来
        hit_book = "《" in tt and any(k.lower() in low for k in XHH_GAME_CTX)
        if not (hit_newsy or hit_name or hit_body or hit_esp
                or hit_ind or hit_play or hit_book):
            dropped.append(tt)
            continue
        if add_hot(tt, it["url"], "小黑盒", now0, extra=XHH_KW + XHH_RELEASE_KW):
            n += 1
    print(f"  [游戏] 小黑盒过滤后: {n} 条（候选 {len(xhh_all)}，剔除 {len(dropped)}）")
    if dropped:
        # 留几个样本，方便日后判断是规则太严还是源内容变了
        print("  [游戏] 剔除样本: " + " / ".join(d[:16] for d in dropped[:6]))

    # 2) 机核 / 游研社 / 触乐
    for name, url in GAME_HOT_RSS:
        try:
            r = requests.get(url, headers=UA, timeout=20)
            k = 0
            for e in feedparser.parse(r.content).entries:
                ts = to_ts(e.get("published_parsed"))
                if add_hot(clean(e.get("title", "")), e.get("link", ""), name, ts):
                    k += 1
            print(f"  [游戏] {name}: {k} 条")
        except Exception as e:
            print(f"  [游戏] {name}: 失败 {str(e)[:40]}")

    # 3) 游民星空（HTML 列表，同页混娱乐新闻，靠关键词过滤）
    k = sum(1 for it in _fetch_gamersky(per=20)
            if add_hot(it["title"], it["url"], "游民星空", it.get("ts") or 0, strict=True))
    print(f"  [游戏] 游民星空: {k} 条")

    # 4) 电竞资讯
    #    腾竞官方：世界赛的「对阵 / 结果」置顶，其余按 LPL 常规话题参与热度排序
    worlds, n_lpl = [], 0
    for it in _fetch_lpl_news(per=20):
        tt = it["title"]
        if any(k in tt for k in LOL_NOISE):
            continue
        # 没有任何赛事信号的纯口号不收
        if not any(k in tt for k in LOL_SIGNAL_KW) and not LOL_WORLD_RE.search(tt):
            continue
        if tt[:22] in seen:
            continue
        seen.add(tt[:22])
        if _lol_is_world_match(tt):
            worlds.append(it)           # 世界赛对阵 / 结果 → 置顶
        else:
            esports.append(it)          # LPL 常规话题，混进热度池
            n_lpl += 1
    print(f"  [游戏] 电竞·腾竞：世界赛置顶 {len(worlds)} 条 / LPL {n_lpl} 条")

    #    CS2：HLTV（国际权威）+ 完美世界 + 虎扑混在一起，按热度排
    cs_n = 0
    for it in _fetch_hltv(per=12):
        if it["title"][:22] in seen:
            continue
        seen.add(it["title"][:22])
        esports.append(it)              # 带 summary，循环末尾统一翻译
        cs_n += 1

    #    完美世界只留 IEM / BLAST / PGL / Major 这类大赛，国内校园赛、城市赛全剔
    for it in _fetch_wanmei_cs(per=15):
        tt = it["title"]
        if not any(k in tt for k in CS_MAJOR_KW):
            continue
        if any(n.lower() in tt.lower() for n in CS_DOMESTIC_NOISE):
            continue
        if tt[:22] in seen:
            continue
        seen.add(tt[:22])
        esports.append(it)
        cs_n += 1
    print(f"  [游戏] 电竞·CS2（HLTV + 完美世界）: {cs_n} 条")

    for game, board in GAME_ESPORTS_BOARDS:
        got = _fetch_hupu_board(board, per=10)
        n = 0
        for tt, u in got:
            # CS2 版块只留大赛话题
            if board == "csgo" and not any(k in tt for k in CS_MAJOR_KW):
                continue
            if any(k in tt for k in LOL_NOISE):
                continue
            if tt[:22] in seen:
                continue
            seen.add(tt[:22])
            item = {"title": tt, "url": u, "source": game, "time": "", "ts": 0}
            # 虎扑的世界赛对阵 / 结果同样置顶
            if board == "lol" and _lol_is_world_match(tt):
                worlds.append(item)
            else:
                esports.append(item)
            n += 1
        print(f"  [游戏] 电竞·虎扑 {game}: {n} 条")

    # 近期热门：按「来源权重 + 时效 + 新游关键词」排序，各源自然交错
    now_ts = time.time()
    hot.sort(key=lambda x: -_game_score(x, now_ts))
    hot = _interleave_sources(hot)

    # 电竞：世界赛对阵/结果置顶（按时间倒序），其余 CS + LPL 混合按热度排
    worlds.sort(key=lambda x: -(x.get("ts") or 0))
    esports.sort(key=lambda x: -_esports_score(x, now_ts))
    esports = _interleave_sources(esports)
    esports = worlds + esports

    # HLTV 是英文源，给标题配一句中文摘要（只作用于这一家，别的源不动）
    cache = _cache_load()
    n_tr = 0
    for it in esports:
        if it.get("source") != "HLTV":
            it.pop("summary", None)
            continue
        raw = it.pop("summary", "")
        if not raw or n_tr >= 15:
            continue
        for key, cn_desc in HLTV_TITLE_DESC.items():
            if it["title"].startswith(key):
                it["desc"] = cn_desc
                break
        else:
            src = first_sentence(raw, 240) or it["title"]
        if it.get("desc"):
            n_tr += 1
            continue
        for a, b in HLTV_PRE:
            src = src.replace(a, b)
        # 腾讯在海外 IP 上不可达（云端抓不到），必须再兜一层 Google
        cn = translate_qq(src, cache) or translate(src, cache)
        if cn:
            for a, b in HLTV_FIX:
                cn = cn.replace(a, b)
            it["desc"] = truncate_cn(cn, 46, hard=60, ratio=0.7)
            n_tr += 1
    if n_tr:
        _cache_save(cache)
    print(f"  [游戏] HLTV 中文摘要: {n_tr} 条")

    for it in hot + esports:
        it.pop("ts", None)
    for it in esports:
        it["tags"] = _esports_tags(it)
    return {"近期热门": hot[:36], "电竞资讯": esports[:40]}


# ============================================================
# 官方政策源（政府部门官网公开信息）
# 合法性说明：政策文件与公告属《政府信息公开条例》要求主动公开的信息，
# 抓取仅访问公开页面、低频请求、保留原文链接与来源标识，不绕过任何访问控制。
# ============================================================
GOV_POLICY_MAX_AGE_DAYS = 45     # 只保留最近 45 天的政策，保证时效性
GOV_POLICY_PER_SOURCE = 15       # 每个来源最多展示条数

# 国务院最新政策（中国政府网官方 JSON）
GOV_CN_POLICY_JSON = "https://www.gov.cn/zhengce/zuixin/ZUIXINZHENGCE.json"

# 国家发改委政策发布 / 公告（HTML 列表，日期在链接里）
NDRC_PAGES = [
    "https://www.ndrc.gov.cn/xxgk/zcfb/fzggwl/",   # 委令（规章）
    "https://www.ndrc.gov.cn/xxgk/zcfb/gg/",       # 公告
]

# 证监会新闻发布（首页锚点，含《…》类政策发布）
CSRC_PAGE = "http://www.csrc.gov.cn/csrc/c100028/"

# 省级政府政策文件（HTML 列表）
PROVINCE_PAGES = [
    ("北京市", "https://www.beijing.gov.cn/zhengce/zhengcefagui/"),
    ("上海市", "https://www.shanghai.gov.cn/nw12344/index.html"),
    ("广东省", "https://www.gd.gov.cn/zwgk/wjk/qbwj/"),
]

# 时政要闻的排除词：社会案件 / 娱乐体育类，不属于「时政」
NEWS_NOISE = [
    "埋尸", "失联", "身亡", "遇难", "车祸", "坠楼", "溺亡", "火灾", "地震",
    "明星", "艺人", "热搜", "演唱会", "电影", "票房", "综艺", "剧集",
    "球队", "比分", "夺冠", "球员", "联赛", "赛事",
    "美食", "景区", "天气", "菜价",
]

# 澎湃新闻·时事频道（页面内嵌 JSON-LD 结构化数据，含精确发布时间）
PENGPAI_CHANNEL = "https://www.thepaper.cn/channel_25950"


def _recent(time_str):
    """判断日期是否在有效期内（time_str 形如 2026-11-03 或 20261103）"""
    if not time_str:
        return True
    s = time_str.replace("-", "").replace("/", "")[:8]
    if len(s) != 8 or not s.isdigit():
        return True
    try:
        dt = datetime.strptime(s, "%Y%m%d")
    except Exception:
        return True
    return (datetime.now() - dt).days <= GOV_POLICY_MAX_AGE_DAYS


def _fmt_date(s):
    s = (s or "").replace("-", "").replace("/", "")
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}" if len(s) == 8 and s.isdigit() else ""


def _md_to_date(md):
    """把 MM-DD 补成 YYYY-MM-DD（证监会列表只给月日）"""
    try:
        now = datetime.now()
        dt = datetime.strptime(f"{now.year}-{md}", "%Y-%m-%d")
        if dt > now:                      # 形如 12-25 出现在 11 月，说明是去年的
            dt = dt.replace(year=now.year - 1)
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return ""


def fetch_gov_cn_policy():
    """国务院最新政策（中国政府网官方 JSON，含中办/国办/国务院文件）"""
    items = []
    try:
        r = requests.get(GOV_CN_POLICY_JSON, headers=UA, timeout=25)
        data = r.json()
        rows = []
        for it in data:
            title = clean(it.get("TITLE", ""))
            url = it.get("URL", "")
            d = it.get("DOCRELPUBTIME", "")
            if title and url:
                rows.append((d, title, url))
        rows.sort(key=lambda x: x[0], reverse=True)
        for d, title, url in rows[:40]:
            if not _recent(d):
                continue
            items.append({"title": title, "url": url, "time": d, "ts": 0})
        print(f"  [政策] 国务院: {len(items)} 条")
    except Exception as e:
        print(f"  [政策] 国务院: 抓取失败 {str(e)[:50]}")
    return items[:GOV_POLICY_PER_SOURCE]


def fetch_ndrc():
    """国家发改委 委令 + 公告（HTML 列表，日期编码在链接文件名中）"""
    items = []
    seen = set()
    for page in NDRC_PAGES:
        try:
            r = requests.get(page, headers=UA, timeout=20)
            r.encoding = "utf-8"
            for href, d, raw in re.findall(
                    r'<a[^>]*href="([^"]*t(\d{8})_\d+\.html)"[^>]*>(.*?)</a>', r.text, re.S):
                title = clean(re.sub(r"<[^>]+>", "", raw))
                if not title or len(title) < 6:
                    continue
                url = requests.compat.urljoin(page, href)
                if url in seen:
                    continue
                seen.add(url)
                if not _recent(d):
                    continue
                items.append({"title": title, "url": url, "time": _fmt_date(d), "ts": 0})
        except Exception as e:
            print(f"  [政策] 发改委({page[-20:]}): {str(e)[:40]}")
    # 只要政策/规章/办法/公告类；剔除「答记者问」（同一政策的重复解读）
    kw = ["办法", "规定", "条例", "意见", "通知", "公告", "规划", "方案", "令", "指南", "标准"]
    items = [x for x in items
             if any(k in x["title"] for k in kw) and "答记者问" not in x["title"]]
    # 同一政策只留一条（按《…》文件名去重，避免「第50号令」和「印发…」重复出现）
    dedup, seen_doc = [], set()
    for x in items:
        m = re.search(r"《(.+?)》", x["title"])
        key = clean(m.group(1)) if m else x["title"]
        if key in seen_doc:
            continue
        seen_doc.add(key)
        dedup.append(x)
    items = dedup
    items.sort(key=lambda x: x["time"], reverse=True)
    print(f"  [政策] 国家发改委: {len(items)} 条")
    return items[:GOV_POLICY_PER_SOURCE]


def fetch_csrc():
    """证监会政策发布（首页锚点：中国证监会发布《…》、第N号公告 等）"""
    items = []
    seen = set()
    try:
        r = requests.get(CSRC_PAGE, headers=UA, timeout=20)
        r.encoding = "utf-8"
        # 按 <li> 切块，这样能把标题和旁边的 <span class="time"> 配对
        for block in re.findall(r"<li[^>]*>(.*?)</li>", r.text, re.S):
            m = re.search(r'<a[^>]*href="(/csrc/c\d+/c\d+/content\.shtml)"[^>]*>(.*?)</a>',
                          block, re.S)
            if not m:
                continue
            href, raw = m.group(1), m.group(2)
            title = clean(re.sub(r"<[^>]+>", "", raw))
            if not title or len(title) < 8:
                continue
            # 只要政策/规章/公告类
            if not any(k in title for k in ["《", "公告", "办法", "规定", "意见", "通知", "规则"]):
                continue
            # 剔除行政处罚/市场禁入决定书（执法个案，不是政策）
            if "决定书" in title:
                continue
            if title in seen:
                continue
            seen.add(title)
            dm = re.search(r'<span class="time">\s*(\d{2}-\d{2})', block) or \
                 re.search(r"(\d{4}-\d{2}-\d{2})", block)
            if dm:
                d = dm.group(1)
                d = _md_to_date(d) if len(d) == 5 else d
            else:
                d = ""
            items.append({"title": title, "url": "http://www.csrc.gov.cn" + href, "time": d, "ts": 0})
        # 同一份文件常以「就…征求意见」和「关于…征求意见的通知」重复出现，按《…》文件名去重
        dedup, seen_doc = [], set()
        for x in items:
            m = re.search(r"《([^《》]+)》", x["title"])
            key = clean(m.group(1)) if m else x["title"]
            if key in seen_doc:
                continue
            seen_doc.add(key)
            dedup.append(x)
        items = dedup
        items = [x for x in items if _recent(x["time"])]
        items.sort(key=lambda x: x["time"], reverse=True)
        print(f"  [政策] 证监会: {len(items)} 条")
    except Exception as e:
        print(f"  [政策] 证监会: 抓取失败 {str(e)[:50]}")
    return items[:GOV_POLICY_PER_SOURCE]


def fetch_provinces():
    """省级政府政策文件（只保留省/市政府层级文件，剔除各局小通知）"""
    items = []
    for name, page in PROVINCE_PAGES:
        got = 0
        try:
            r = requests.get(page, headers=UA, timeout=20)
            r.encoding = "utf-8"
            seen = set()

            # ---- 广东省：<li> 块内含 <span class="date">，最规整 ----
            if name == "广东省":
                for block in re.findall(r"<li[^>]*>(.*?)</li>", r.text, re.S):
                    m = re.search(r'<a[^>]*href="([^"]+)"[^>]*>([^<]{8,95})</a>', block)
                    if not m:
                        continue
                    url, title = m.group(1), clean(m.group(2))
                    if not url.startswith("http"):
                        continue
                    dm = re.search(r'<span class="date">\s*(\d{4}-\d{2}-\d{2})', block)
                    d = dm.group(1) if dm else ""
                    if title in seen or not d or not _recent(d):
                        continue
                    # 只要省政府/办公厅发文（粤府、粤办函、粤府函）
                    wh = re.search(r'<span class="wh">([^<]*)</span>', block)
                    docno = clean(wh.group(1)) if wh else ""
                    if docno and not docno.startswith("粤"):
                        continue
                    seen.add(title)
                    items.append({"title": title, "url": url, "time": d, "ts": 0, "region": name})
                    got += 1
                print(f"  [政策] {name}: {got} 条")
                continue

            # ---- 北京 / 上海：日期在链接路径里 ----
            if name == "北京市":
                pat = r'<a[^>]*href="([^"]*t(\d{8})_\d+\.html)"[^>]*>([^<]{8,90})</a>'
            else:  # 上海市：/nw12344/20261029/...
                pat = r'<a[^>]*href="([^"]*/(20\d{6})/[^"]+)"[^>]*>([^<]{8,90})</a>'
            for href, d, raw in re.findall(pat, r.text):
                title = clean(raw)
                if not title or title in seen:
                    continue
                seen.add(title)
                # 只要省/市政府层级文件，不要各局/各区的小通知
                if name == "北京市":
                    if not any(k in title for k in ["北京市人民政府", "市人民政府办公厅", "京政发", "京政办发"]):
                        continue
                else:  # 上海市
                    if not any(k in title for k in ["上海市人民政府", "市人民政府办公厅", "中共上海市委办公厅"]):
                        continue
                if not any(k in title for k in ["办法", "规定", "条例", "意见", "通知", "规划",
                                                "方案", "决定", "细则", "批复", "公告"]):
                    continue
                if not _recent(d):
                    continue
                url = requests.compat.urljoin(page, href)
                items.append({"title": title, "url": url,
                              "time": _fmt_date(d), "ts": 0, "region": name})
                got += 1
            print(f"  [政策] {name}: {got} 条")
        except Exception as e:
            print(f"  [政策] {name}: 抓取失败 {str(e)[:50]}")
    items.sort(key=lambda x: x["time"], reverse=True)
    # 各省级均分名额，避免某地文件多就把另一个省挤掉
    per = max(4, GOV_POLICY_PER_SOURCE // max(1, len(PROVINCE_PAGES)))
    balanced, used = [], defaultdict(int)
    for x in items:
        r_ = x.get("region", "")
        if used[r_] >= per:
            continue
        used[r_] += 1
        balanced.append(x)
    return balanced[:GOV_POLICY_PER_SOURCE]


def fetch_pengpai():
    """澎湃新闻·时事频道（解析页面内嵌 JSON-LD，稳定且有精确时间）"""
    items = []
    try:
        r = requests.get(PENGPAI_CHANNEL, headers=UA, timeout=20)
        r.encoding = "utf-8"
        seen = set()
        for raw in re.findall(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', r.text, re.S):
            try:
                data = json.loads(raw)
            except Exception:
                continue
            lst = (data.get("mainEntity") or {}).get("itemListElement") or []
            for it in lst:
                title = clean(it.get("name", ""))
                url = it.get("url", "")
                dp = it.get("datePublished", "")
                if not title or not url or title in seen:
                    continue
                # 澎湃时事频道混有社会案件/娱乐新闻，排除明显非时政的
                if any(k in title for k in NEWS_NOISE):
                    continue
                seen.add(title)
                ts = 0
                try:
                    ts = int(datetime.strptime(dp[:19], "%Y-%m-%dT%H:%M:%S").timestamp())
                except Exception:
                    pass
                items.append({
                    "title": title,
                    "url": url,
                    "source": "澎湃新闻·时事",
                    "time": time.strftime("%m-%d %H:%M", time.localtime(ts)) if ts else "",
                    "ts": ts,
                })
        print(f"  [时政] 澎湃新闻·时事: {len(items)} 条")
    except Exception as e:
        print(f"  [时政] 澎湃新闻·时事: 抓取失败 {str(e)[:50]}")
    return items


def fetch_gov_policy():
    """汇总各官方政策源，返回 {来源: [条目]}"""
    result = {}
    result["国务院"] = fetch_gov_cn_policy()
    result["国家发改委"] = fetch_ndrc()
    result["证监会"] = fetch_csrc()
    return result


def fetch_politics():
    """抓取政治新闻：国家到地方的政策发布、重要会议与重要决策"""
    items = []
    seen_title = set()
    topic_count = defaultdict(int)
    total = kept = 0
    for name, url in POLITICS_FEEDS:
        try:
            r = requests.get(url, headers=UA, timeout=20)
            feed = feedparser.parse(r.content)
            n = 0
            for e in feed.entries[:30]:
                title = clean(e.get("title", ""))
                link = e.get("link", "")
                if not title or not link:
                    continue
                total += 1
                # 标题去重（两个源内容有重叠，URL 不同但标题相同）
                if title in seen_title:
                    continue
                # 政策相关性：必须命中政策/会议/决策关键词
                if not any(k in title for k in POLITICS_KW):
                    continue
                # 噪音剔除
                if any(k in title for k in POLITICS_NOISE):
                    continue
                # 主题限流：同一话题不刷屏
                topic = next((k for k in POLITICS_TOPIC_CAP if k in title), None)
                if topic and topic_count[topic] >= POLITICS_TOPIC_CAP[topic]:
                    continue
                if topic:
                    topic_count[topic] += 1
                seen_title.add(title)
                kept += 1
                ts = to_ts(e.get("published_parsed")) or to_ts(e.get("updated_parsed"))
                items.append({
                    "title": title,
                    "url": link,
                    "source": name,
                    "time": time.strftime("%m-%d %H:%M", time.localtime(ts)) if ts else "",
                    "ts": ts,
                })
                n += 1
            print(f"  [政治] {name}: {n} 条")
        except Exception as e:
            print(f"  [政治] {name}: 抓取失败 {str(e)[:50]}")
    if total:
        print(f"  [政治] 政策相关性过滤：{total} 条 → {kept} 条（已去重+限流）")
    # 补充澎湃新闻·时事频道（时效性好，作为中新网之外的第二来源）
    for x in fetch_pengpai():
        if x["title"] in seen_title:
            continue
        seen_title.add(x["title"])
        items.append(x)
    items.sort(key=lambda x: -x["ts"])
    return items[:40]


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


# ============================================================
# 周榜：积累历史数据，找出持续 2-3 天热度高的新闻，热度取平均
# ============================================================
HISTORY_FILE = "history.json"
WEEKLY_MIN_DAYS = 2      # 至少持续 2 天
WEEKLY_HEAT_LINE = 60    # 单日热度需达到 60 才计入


def load_history():
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def update_history(history, today, news):
    """把今天热度高的新闻追加进历史，只保留最近 5 天"""
    today_items = {}
    for n in news:
        if n.get("heat", 0) >= WEEKLY_HEAT_LINE:
            today_items[n["url"]] = {
                "title": n["title"],
                "url": n["url"],
                "source": n["source"],
                "heat": n["heat"],
            }
    history[today] = today_items
    dates = sorted(history.keys())
    while len(dates) > 5:
        del history[dates[0]]
        dates = dates[1:]
    return history


def compute_weekly(history):
    """统计每条新闻出现天数和热度，持续达标者进入周榜，热度取平均"""
    url_days = {}
    for date, items in history.items():
        for url, item in items.items():
            if url not in url_days:
                url_days[url] = {"heats": [], "item": item}
            url_days[url]["heats"].append(item["heat"])
    weekly = []
    for url, info in url_days.items():
        if len(info["heats"]) >= WEEKLY_MIN_DAYS:
            avg = sum(info["heats"]) / len(info["heats"])
            weekly.append({
                "title": info["item"]["title"],
                "url": url,
                "source": info["item"]["source"],
                "heat": int(round(avg)),
                "days": len(info["heats"]),
            })
    weekly.sort(key=lambda x: -x["heat"])
    return weekly[:20]


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

    # 计算热度 + AI 相关性过滤 + 阈值过滤 + 打乱
    for it in news:
        it["heat"] = calc_heat(it)
    # AI 相关性过滤：媒体源标题必须含 AI 关键词（官方源本身就是 AI 机构，不过滤）
    before = len(news)
    news = [n for n in news
            if n.get("tier") == "official" or any(k in n["title"] for k in AI_RELEVANT)]
    dropped = before - len(news)
    if dropped:
        print(f"  [过滤] 剔除 {dropped} 条非 AI 相关的媒体新闻")
    news = [n for n in news if n["heat"] >= HEAT_THRESHOLD]
    # 后端统一按热度倒序输出（前端「按热度」直接用该顺序，「按来源」由前端重新分组）
    news = sorted(news, key=lambda x: -x["heat"])

    print()
    print("开始抓取时事热点...")
    hot = fetch_hot()
    hot["小红书热搜"] = fetch_xiaohongshu()

    print()
    print("开始抓取财经新闻...")
    finance = fetch_finance()

    print()
    print("开始抓取体育新闻...")
    sports = fetch_sports()

    print()
    print("开始抓取游戏资讯...")
    games = fetch_games()

    print()
    print("开始抓取官方政策（政府官网公开信息）...")
    gov_policy = fetch_gov_policy()

    print()
    print("开始抓取时政要闻...")
    politics_news = fetch_politics()
    gov_policy["时政要闻"] = politics_news
    politics = gov_policy

    # 英文条目的中文附注：同时生成「翻译」和「极简总结」两版，前端可切换
    print()
    print("生成英文条目的中文附注（翻译 + 极简总结）...")
    cache = _cache_load()
    ok_tr = ok_sum = 0
    todo = [it for it in news if it.get("lang") != "zh"]
    for i, it in enumerate(todo, 1):
        tr, sm = build_briefs(it, cache)
        if tr:
            it["brief_tr"] = tr
            ok_tr += 1
        if sm:
            it["brief_sum"] = sm
            ok_sum += 1
        if i % 10 == 0:
            print(f"    进度 {i}/{len(todo)}（翻译 {ok_tr} / 总结 {ok_sum}）", flush=True)
    _cache_save(cache)
    print(f"  [OK] 翻译 {ok_tr} 条 / 总结 {ok_sum} 条")

    # 配了 AI key 时，用模型重写为「单行中文导语」：
    # AI 自己判断该译标题还是该说内容，只覆盖 brief_sum，并删掉旧的机器翻译 brief_tr，
    # 避免前端出现「译 + 摘要」两行重复的内容
    ai_map = ai_briefs(todo, cache)
    if ai_map:
        n_ai = 0
        for it in todo:
            line = ai_map.get(it["url"])
            if line:
                it["brief_sum"] = line
                it.pop("brief_tr", None)
                n_ai += 1
        _cache_save(cache)
        print(f"  [AI] 单行导语已覆盖 {n_ai}/{len(todo)} 条")
    else:
        print("  [AI] 未启用（没有配置 AI_API_KEY），沿用翻译摘要")
    # summary 字段只用于生成附注，不进最终数据，减小体积
    for it in news:
        it.pop("summary", None)

    # 国际投行的英文条目：中文翻译放宽到两行（约 38 字），把这篇在讲什么说完整；
    # 与 AI 新闻的极简摘要（27 字）区分，这里优先保证信息量
    bank_en = [it for it in finance.get("国际投行", []) if it.get("lang") == "en"]
    if bank_en:
        print(f"  生成国际投行英文条目的中文翻译（{len(bank_en)} 条）...")
        for it in bank_en:
            raw = re.sub(r"^\s*Key Takeaways\s*", "", it.get("summary", ""))
            src = first_sentence(raw, 240) or it["title"]
            # 优先腾讯翻译（本地可用、质量好），失败再退回通用链路
            cn = translate_qq(src, cache) or translate(src, cache)
            bank = it.get("bank", "")
            if cn:
                body = truncate_cn(cn, 40, hard=54, ratio=0.75)
                # 摘要本身以机构名开头时不再重复加前缀
                if bank and not body.startswith(bank):
                    it["desc"] = f"{bank} · {body}"
                else:
                    it["desc"] = body or bank
            else:
                it["desc"] = bank
            it.pop("bank", None)
        _cache_save(cache)
    for it in finance.get("国际投行", []):
        it.pop("summary", None)
        it.pop("lang", None)

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    today = datetime.now().strftime("%Y-%m-%d")

    # 周榜：积累历史 + 计算持续热门
    print()
    print("计算周榜（持续热门）...")
    history = load_history()
    history = update_history(history, today, news)
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False)
    weekly = compute_weekly(history)
    if not weekly:
        # 数据不足 2 天时，用当天热度 Top 20 作为临时周榜
        weekly = [{
            "title": n["title"],
            "url": n["url"],
            "source": n["source"],
            "heat": n["heat"],
            "days": 1,
        } for n in sorted(news, key=lambda x: -x["heat"])[:20]]
        print(f"  [OK] 周榜数据不足 2 天，改用当天 Top 20 填充（{len(weekly)} 条）")
    else:
        print(f"  [OK] 周榜 {len(weekly)} 条")

    data = {
        "updated": now,
        "news": news,
        "hot": hot,
        "finance": finance,
        "sports": sports,
        "politics": politics,
        "games": games,
        "weekly": weekly,
    }

    with open("data.js", "w", encoding="utf-8") as f:
        f.write("window.DATA = " + json.dumps(data, ensure_ascii=False) + ";\n")

    print()
    print(f"完成！AI 新闻 {len(news)} 条（按热度排序），周榜 {len(weekly)} 条，"
          f"财经 {sum(len(v) for v in finance.values())} 条，"
          f"体育 {sum(len(v) for v in sports.values())} 条（{len(sports)} 类），"
          f"政治 {sum(len(v) for v in politics.values())} 条（{len(politics)} 类），"
          f"游戏 {sum(len(v) for v in games.values())} 条（{len(games)} 类），"
          f"写入 data.js（更新于 {now}）")


if __name__ == "__main__":
    main()
