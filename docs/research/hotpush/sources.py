"""
热榜源配置
定义支持的热榜来源和对应的 RSSHub 路由

注意：部分源需要在 RSSHub 配置额外的环境变量才能正常工作
- 微博：需要 WEIBO_COOKIE
- B站：需要 BILIBILI_COOKIE
- 抖音：需要反代理配置
- GitHub Trending：可能需要代理

详见：https://docs.rsshub.app/deploy/config
"""

def _icon(source_id: str) -> str:
    """使用前端内置图标，避免依赖外站 favicon。"""
    return f"/icons/sources/{source_id}.png"

# ============ 稳定源（无需额外配置）============
HOT_SOURCES = {
    # --- 热搜榜 ---
    "weibo": {
        "name": "微博热搜",
        "route": "/weibo/search/hot",
        "icon": _icon("weibo"),
        "category": "热搜榜"
    },
    "zhihu": {
        "name": "知乎热榜",
        "route": "/zhihu/hot",
        "icon": _icon("zhihu"),
        "category": "热搜榜"
    },
    "bilibili": {
        "name": "B站热搜",
        "route": "/bilibili/hot-search",
        "icon": _icon("bilibili"),
        "category": "视频"
    },

    # --- 技术社区 ---
    "v2ex": {
        "name": "V2EX 热门",
        "route": "/v2ex/topics/hot",
        "icon": _icon("v2ex"),
        "category": "技术"
    },
    "hackernews": {
        "name": "Hacker News",
        "route": "/hackernews/best",
        "icon": _icon("hackernews"),
        "category": "技术"
    },
    "juejin": {
        "name": "掘金热榜",
        "route": "/juejin/trending/all/weekly",
        "icon": _icon("juejin"),
        "category": "技术"
    },
    "ithome": {
        "name": "IT之家热榜",
        "route": "/ithome/ranking/24h",
        "icon": _icon("ithome"),
        "category": "科技资讯"
    },
    "nodeseek": {
        "name": "NodeSeek",
        "route": "https://rss.nodeseek.com",
        "icon": _icon("nodeseek"),
        "category": "技术"
    },

    # --- 科技资讯 ---
    "sspai": {
        "name": "少数派",
        "route": "/sspai/index",
        "icon": _icon("sspai"),
        "category": "科技资讯"
    },

    # --- 影视/娱乐 ---
    "douban_movie": {
        "name": "豆瓣热映",
        "route": "/douban/movie/playing",
        "icon": _icon("douban_movie"),
        "category": "影视"
    },
    "douban_book": {
        "name": "豆瓣新书",
        "route": "/douban/book/latest",
        "icon": _icon("douban_book"),
        "category": "阅读"
    },

    # --- 新闻 ---
    "zaobao": {
        "name": "联合早报",
        "route": "/zaobao/realtime/china",
        "icon": _icon("zaobao"),
        "category": "新闻"
    },
    "thepaper": {
        "name": "澎湃新闻",
        "route": "/thepaper/featured",
        "icon": _icon("thepaper"),
        "category": "新闻"
    },
}

# ============ 需要额外配置的源 ============
SOURCES_REQUIRE_CONFIG = {}

# 分类汇总
CATEGORIES = {
    "热搜榜": ["weibo", "zhihu"],
    "技术": ["v2ex", "hackernews", "juejin", "nodeseek"],
    "科技资讯": ["sspai", "ithome"],
    "视频": ["bilibili"],
    "影视": ["douban_movie"],
    "阅读": ["douban_book"],
    "新闻": ["zaobao", "thepaper"],
}


def get_source_info(source_id: str) -> dict:
    """获取源信息"""
    return HOT_SOURCES.get(source_id, {})


def get_sources_by_category(category: str) -> list:
    """按分类获取源列表"""
    source_ids = CATEGORIES.get(category, [])
    return [{"id": sid, **HOT_SOURCES[sid]} for sid in source_ids if sid in HOT_SOURCES]


def get_all_categories() -> dict:
    """获取所有分类"""
    return CATEGORIES
