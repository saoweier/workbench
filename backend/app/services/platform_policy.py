"""平台差异的**单一约束来源**：封面策略与文案取向。

参考仓库（GitHub 周榜卡片）把「小红书需要封面、抖音可以没有封面」写成文档说明，
但文档各页之间说法并不一致。本项目不重复那个问题：平台差异只在这一个模块里定义，
生成提示词、平台稿校验、规则稿与交付物清单都从这里取，避免主 Skill 与说明互相冲突。

约定（可校验的数据，不是祈愿）：

- 小红书 ``required``：必须有一页 ``layout=cover`` 的封面；封面本身也要给核心信息。
- 抖音 ``optional``：可以不单独做封面页，第一页直接给内容；若做了封面也不拒绝。
  但「不是封面页却用了封面图解」仍然拒绝——那只是把封面换了个名字。

本模块只描述版式约定，不判断事实，也不接触网络。
"""
from __future__ import annotations

#: 平台封面策略：required / optional
COVER_POLICY: dict[str, str] = {"douyin": "optional", "xiaohongshu": "required"}

PLATFORM_NAME: dict[str, str] = {"douyin": "抖音", "xiaohongshu": "小红书"}

#: 文案取向：同一份母稿在两平台要写成不同的读者收益，而不是改个平台名。
COPY_ANGLE: dict[str, str] = {
    "douyin": "直观对比与可立刻执行的行动，短句、结论先行",
    "xiaohongshu": "解释与可收藏复用的清单，标题完整、层次清楚",
}


def platform_name(platform: str) -> str:
    return PLATFORM_NAME.get(platform, platform)


def cover_policy(platform: str) -> str:
    """未知平台按最严格处理（要求封面），不静默放宽。"""
    return COVER_POLICY.get(platform, "required")


def requires_cover(platform: str) -> bool:
    return cover_policy(platform) == "required"


def needs_cover(platform: str, force: bool = False) -> bool:
    """这份稿需不需要封面页。``force`` 是用户明确要求（简报 want_cover）。"""
    return bool(force) or requires_cover(platform)


def cover_instruction(platform: str, force: bool = False) -> str:
    """写进生成提示词的封面约定。两平台必须给出不同指令。"""
    if needs_cover(platform, force):
        if requires_cover(platform):
            return ("第一页 layout 必须为 cover，且封面要给出核心信息（不是只有标题和插画的装饰页）；"
                    "其余为 checklist。")
        # 抖音默认不要求封面；这里只出现在用户明确要求封面（want_cover）时。
        return ("用户明确要求每个平台都有封面页：第一页 layout 用 cover，"
                "封面要给出核心信息（不是只有标题和插画的装饰页）；其余为 checklist。")
    return ("抖音不要求单独封面页：不要为了装饰单独做一页空封面，"
            "第一页直接给实质内容（layout 用 checklist）；确有概览需要时才用 layout=cover。"
            "若某页不是封面，它的 visual.kind 就不能是 cover。")


def copy_angle_instruction(platform: str) -> str:
    return f"{platform_name(platform)}侧重：{COPY_ANGLE.get(platform, COPY_ANGLE['douyin'])}。"


def cover_problem(platform: str, pages: list[dict]) -> str | None:
    """返回封面约定的违反原因；符合约定返回 None。

    - required：首页必须是 cover。
    - optional：首页可以不是 cover，但不能在非封面页使用封面图解。
    """
    if not pages:
        return None
    first = pages[0] or {}
    layout = first.get("layout")
    if requires_cover(platform):
        if layout != "cover":
            return (f"{platform} 首页 layout={layout!r}，"
                    f"{platform_name(platform)}图文必须有封面页（layout=cover）")
        return None
    if layout == "cover":
        return None
    if (first.get("visual") or {}).get("kind") == "cover":
        return (f"{platform} 首页不是封面页，但图解仍标为 cover 封面样式；"
                "请改成实际内容图解（map / flow / rank / checklist）")
    return None


def cover_note(platform: str, pages: list[dict]) -> str:
    """给交付物清单用的可读说明：这份稿有没有封面。"""
    if not pages:
        return "尚无页面"
    has_cover = (pages[0] or {}).get("layout") == "cover"
    if requires_cover(platform):
        return "含封面页" if has_cover else "缺少封面页（小红书必须有封面）"
    return "含封面页" if has_cover else "无独立封面（抖音允许，第一页直接给内容）"


#: 封面能承载的要点条数上限（渲染器封面版式的实际容量）
COVER_MAX_ITEMS = 4


def cover_missing(platform: str, pages: list[dict], force: bool = False) -> bool:
    """平台/用户要求封面，但首页不是封面页。"""
    if not pages or not needs_cover(platform, force):
        return False
    return (pages[0] or {}).get("layout") != "cover"


def as_cover_page(page: dict) -> dict:
    """把一页内容改成封面页：保留标题、正文与主张，只换版式和图解样式。"""
    out = dict(page or {})
    out["layout"] = "cover"
    visual = out.get("visual")
    if isinstance(visual, dict):
        visual = dict(visual)
        visual["kind"] = "cover"
        visual["items"] = list(visual.get("items") or [])[:COVER_MAX_ITEMS]
        out["visual"] = visual
    return out


def insert_cover(platform: str, pages: list[dict], force: bool = False,
                 max_pages: int | None = None) -> list[dict]:
    """平台要求封面、模型却没给时，由程序补一页封面，而不是把整份稿判死。

    内容全部来自模型自己写的第一页（标题、正文、主张都照抄，不编造），程序只改版式：

    - 页数还有余量、第一页是普通内容页 → 在它前面补一页封面，正文一页不少；
    - 第一页本来就是封面图解、只是 ``layout`` 标错 → 就地改成封面页，页数不变；
    - ``max_pages`` 说明用户把页数锁死了 → 不加页，把第一页就地改成封面页。

    不符合条件时**原样返回同一个列表对象**，调用方可用 ``is`` 判断有没有改动。
    """
    if not cover_missing(platform, pages, force):
        return pages
    first = dict(pages[0] or {})
    cover_shaped = (first.get("visual") or {}).get("kind") == "cover"
    room = max_pages is None or len(pages) < int(max_pages)
    # 不能复制的情况：本来就写成封面图解却标了正文版式（复制出来的封面会和原页的
    # 「封面图解 + 非封面版式」组合冲突，渲染器 check_layout 会判 error）；
    # 以及用户页数预算已经用满（多一页会被 compose 的页数校验直接拒掉）。
    rest = pages if (room and not cover_shaped) else pages[1:]
    out = [as_cover_page(first), *[dict(p or {}) for p in rest]]
    for n, page in enumerate(out, start=1):
        page["index"] = n
    return out
