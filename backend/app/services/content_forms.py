"""题材形态（Content Form）与创作简报（Creative Brief）。

## 这一层为什么必须存在

用户输入的「做一个精致的排行榜 / TOP10 / 1~2 页就结束」过去只被当成一段
自由文本，拼进模型提示词就算完事。于是出现两类可复现的问题：

1. **用户页数被平台固定策略吃掉。** `PAGE_STRATEGY` 写死抖音 4–6 页、
   小红书 5–7 页，提示词里直接写「页数策略：4–6 页」。用户要 1~2 页，
   模型照旧被要求凑 4–7 页，产出的自然是 5 页、7 页。
2. **题型没有被结构化。** 「排行榜」在你的系统里没有任何对应版式，
   模型只能把它写成普通清单或分组要点，TOP10 的名次在成稿里消失；
   而且所有题材共用同一套「封面 + checklist + 图解」模板，看起来像一个模子刻的。

本模块把自由文本要求解析成**结构化简报**，并在四处统一使用：
规划（planning）、母稿生成（master）、平台改写（variant）与渲染（render），
让「页数、题型、条目数、主题、框架」成为可校验的约束，而不是一句祈愿。

解析规则是确定性的纯函数，不调用模型，便于测试与复现。
"""
from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

FormId = Literal[
    "ranking", "directory", "listicle", "tutorial", "comparison", "review", "guide", "meme", "explainer"
]

# ---------------------------------------------------------------- 主题配色
#
# 不同题材配不同主题。颜色只影响渲染层（PNG），不参与事实判断。
# explainer 保持原有配色，确保既有产物的观感不发生无谓变化。

FORM_THEMES: dict[str, dict] = {
    "ranking": dict(name="榜单红金", bg="#FBF3E6", ink="#3B2712", muted="#8A7357",
                    accent="#C0392B", soft="#F0DCC2", on_ink="#FFF6E8",
                    tones=["#F7E4C8", "#F2D3A6", "#ECBE80", "#E3A659"]),
    "listicle": dict(name="清单青绿", bg="#F3F7F0", ink="#22402E", muted="#5F7A68",
                     accent="#3F7D4E", soft="#E0EBDC", on_ink="#F7FBF4",
                     tones=["#E3EEDC", "#D3E5CB", "#C2DBB8", "#AFCFA4"]),
    "tutorial": dict(name="教程蓝", bg="#F1F5FA", ink="#1B3350", muted="#5B7089",
                     accent="#2E6FB0", soft="#DCE8F6", on_ink="#F5F9FF",
                     tones=["#DDEAF7", "#C9DCF0", "#B3CBE6", "#9BB9DA"]),
    "comparison": dict(name="对比紫", bg="#F6F2FA", ink="#33234E", muted="#6D5C85",
                       accent="#7A4FB5", soft="#E7DDF4", on_ink="#F9F5FF",
                       tones=["#E9DFF5", "#D9CBEE", "#C7B3E3", "#B49AD7"]),
    "review": dict(name="测评橙", bg="#FCF4EB", ink="#4A331C", muted="#8A6B45",
                   accent="#D2762B", soft="#F7E3CD", on_ink="#FFF7EC",
                   tones=["#FAE6D0", "#F4D4B1", "#EEBF93", "#E6A772"]),
    "guide": dict(name="指南青", bg="#EFF7F6", ink="#16403D", muted="#557A76",
                  accent="#2A8C86", soft="#DBEDEB", on_ink="#F2FBFA",
                  tones=["#DDEEEC", "#CAE3E0", "#B5D7D3", "#9ECAC5"]),
    "explainer": dict(name="科普绿", bg="#F7F4EB", ink="#17382F", muted="#668073",
                      accent="#E38454", soft="#E4EDE4", on_ink="#FFFAF1",
                      tones=["#E2ECE4", "#FBE1D1", "#DCE8F5", "#F5E8B9"]),
}


class ContentForm(BaseModel):
    """一种内容形态：它决定「这一页该长什么样」，而不是所有题材一个样。"""

    model_config = ConfigDict(extra="forbid")

    id: FormId
    name: str
    keywords: tuple[str, ...]
    #: 没有用户显式页数时，规划阶段的默认页数区间
    page_min: int = 4
    page_max: int = 6
    #: 每页正文条数（平台策略未覆盖时使用）
    body_lines: int = 4
    #: 该形态建议使用的图解类型（用于提示词引导，不做硬拒）
    allowed_kinds: tuple[str, ...] = ("cover", "map", "flow", "compare", "example", "checklist")
    #: 必须出现的图解类型（硬约束）
    required_kinds: tuple[str, ...] = ()
    #: 榜单形态的默认条目数；None 表示未指定
    rank_items: int | None = None
    #: 写作框架：每一页承担的角色，逐页列出
    structure: tuple[str, ...]
    #: 主题名（对应 FORM_THEMES）
    theme: str


FORMS: dict[str, ContentForm] = {
    "meme": ContentForm(id='meme',name='热梗指南',keywords=('什么梗','啥梗','热梗','梗指南','梗的出处','网络梗'),
        page_min=2,page_max=4,allowed_kinds=('cover','flow','example','compare','map'),
        structure=('直接还原梗的剧情与完整经典台词，第一张也是内容页','一句话讲清实际含义，以及重复套路为什么好笑','给出可套用公式和三个具体原创例句','来源只在一行短脚注交代，详细核验记录留在诊断页'),theme='explainer'),
    "ranking": ContentForm(
        id="ranking", name="榜单排行",
        keywords=("排行榜", "榜单", "排行", "排名", "top", "十大", "前五", "前三",
                  "前十", "最热门", "最火", "最受欢迎", "热度榜"),
        page_min=2, page_max=4, body_lines=2,
        allowed_kinds=("cover", "rank"), required_kinds=("rank",),
        structure=("封面：点明榜单主题、范围与评选口径（不声称实测热度）",
                   "榜单：一页给出完整名次，每条＝名次＋名称＋一句入选理由"),
        theme="ranking",
    ),
    "directory": ContentForm(
        id="directory", name="分类速查", keywords=("分类速查", "速查表", "分类表", "功能表", "分类整理"),
        page_min=1, page_max=2, body_lines=1, allowed_kinds=("catalog",), required_kinds=("catalog",),
        structure=("分类速查：按同一维度分类，完整列出名称和主要用途；有依据才附数据",), theme="explainer",
    ),
    "listicle": ContentForm(
        id="listicle", name="清单盘点",
        keywords=("清单", "合集", "盘点", "必备", "汇总", "系列", "n个", "几类"),
        page_min=4, page_max=6, body_lines=4,
        allowed_kinds=("cover", "checklist", "map", "rank"),
        structure=("封面：说明清单的用途与范围",
                   "分组清单：把小项按同一维度归类，每组给出选择理由",
                   "重点项：挑 2–3 项展开为什么值得留",
                   "适用边界：谁不需要这份清单，以及怎么按自己情况删减"),
        theme="listicle",
    ),
    "tutorial": ContentForm(
        id="tutorial", name="教程步骤",
        keywords=("教程", "步骤", "怎么做", "如何", "操作流程", "保姆级", "一步步", "上手"),
        page_min=4, page_max=6, body_lines=4,
        allowed_kinds=("cover", "flow", "example", "checklist"),
        required_kinds=(),
        structure=("封面：说清学完能做到什么",
                   "准备：动手前需要的东西与前置条件",
                   "步骤：按先后顺序拆成可执行动作",
                   "示例：一个具体到能照抄的例子",
                   "常见错误：最容易卡住的地方与绕过办法"),
        theme="tutorial",
    ),
    "comparison": ContentForm(
        id="comparison", name="对比横评",
        keywords=("对比", "横评", "区别", "差异", "哪个好", "优缺点", "vs", "比较"),
        page_min=3, page_max=5, body_lines=3,
        allowed_kinds=("cover", "compare", "checklist"),
        required_kinds=("compare",),
        structure=("封面：点明比较对象与比较维度",
                   "对比：把两个（组）对象放在同一组维度下逐项对照",
                   "选择建议：给出「什么情况下选哪个」的判据",
                   "边界：说明对比没有覆盖的场景"),
        theme="comparison",
    ),
    "review": ContentForm(
        id="review", name="测评体验",
        keywords=("测评", "实测", "评测", "体验", "使用感受", "上手体验"),
        page_min=3, page_max=5, body_lines=3,
        allowed_kinds=("cover", "compare", "checklist", "example"),
        structure=("封面：对象、场景与结论（区分亲历与编辑观点）",
                   "关键表现：按维度给出可核对的表现",
                   "适用与不适用：什么人适合，什么人别买",
                   "边界：未实测的部分如实标注"),
        theme="review",
    ),
    "guide": ContentForm(
        id="guide", name="指南选购",
        keywords=("指南", "选购", "避坑", "入门", "怎么选", "挑选", "推荐", "吃法", "搭配"),
        page_min=4, page_max=6, body_lines=4,
        allowed_kinds=("cover", "checklist", "map", "flow", "compare"),
        structure=("封面：一句话给出这份指南的适用对象",
                   "判据：先列选择标准，再谈具体对象",
                   "对象：按标准给出具体推荐与理由",
                   "避坑：常见误区与不合适的情形"),
        theme="guide",
    ),
    "explainer": ContentForm(
        id="explainer", name="科普说明",
        keywords=(),
        page_min=4, page_max=8, body_lines=4,
        allowed_kinds=("cover", "map", "flow", "compare", "example", "checklist"),
        structure=("问题与结论：明确读者的问题，先给结论",
                   "选择与依据：展示具体对象及理由",
                   "怎样做：给出实际做法",
                   "核对与行动：交代证据边界与下一步"),
        theme="explainer",
    ),
}


def forms_index() -> dict[str, ContentForm]:
    return FORMS


# ---------------------------------------------------------------- 简报模型

class CreativeBrief(BaseModel):
    """从「选题 + 用户要求 + 提纲」解析出的结构化创作简报。"""

    model_config = ConfigDict(extra="forbid")

    form: FormId
    direction: str = 'general'
    direction_name: str = '通用内容'
    template_id: str = 'illustrated'
    template_version: int = 4
    template_package: dict | None = None
    item_count: int | None = Field(default=None,ge=1)
    form_name: str
    theme: str
    framework: list[str] = Field(default_factory=list)
    allowed_kinds: list[str] = Field(default_factory=list)
    required_kinds: list[str] = Field(default_factory=list)
    #: 用户显式页数预算；None 表示未指定（交给平台策略）
    page_min: int | None = None
    page_max: int | None = None
    explicit_pages: bool = False
    #: 用户明确要求每个平台都出封面页；False 表示按平台默认（小红书必有、抖音可无）
    want_cover: bool = False
    #: 榜单条目数（TOP10 → 10）
    rank_count: int | None = None
    #: 解析依据，便于在界面与日志里向用户解释
    detected_from: str = ""
    original_topic: str = ""
    original_requirements: str = ""
    time_range: str | None = None
    caption_min: int | None = Field(default=None,ge=1)
    caption_max: int | None = Field(default=None,ge=1)
    detail_max: int | None = Field(default=None,ge=1)

    def strategy_pages(self) -> tuple[int, int] | None:
        """返回用户显式页数预算；未指定时为 None（不覆盖平台策略）。"""
        if self.explicit_pages and self.page_min and self.page_max:
            return self.page_min, self.page_max
        return None


# ---------------------------------------------------------------- 解析

_CN_NUM = {"一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5,
           "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
_NUM = r"(\d{1,2}|[一二两三四五六七八九十])"


def _to_int(raw: str) -> int | None:
    raw = (raw or "").strip()
    if raw.isdigit():
        return int(raw)
    return _CN_NUM.get(raw)


def parse_page_budget(text: str) -> tuple[int, int] | None:
    """从自由文本里解析页数预算。

    支持：「1~2页」「1-2 页」「两页以内」「不超过 3 页」「共 4 页」「恰好 2 页」。
    解析不到返回 None —— 不猜，交给平台默认策略。
    """
    t = text or ""
    m = re.search(rf"{_NUM}\s*[~～\-—–至到]\s*{_NUM}\s*页", t)
    if m:
        a, b = _to_int(m.group(1)), _to_int(m.group(2))
        if a and b:
            return (min(a, b), max(a, b))
    m = re.search(rf"(?:共|刚好|恰好|就是)\s*{_NUM}\s*页", t)
    if m:
        n = _to_int(m.group(1))
        if n: return (n, n)
    m = re.search(rf"(?:不超过|最多|至多|控制在|限制在|只要|做|做成)\s*{_NUM}\s*页", t)
    if m:
        n = _to_int(m.group(1))
        if n:
            return (1, n)
    m = re.search(rf"{_NUM}\s*页\s*(?:以内|以下|结束|即可|就行|封顶|搞定|完事)", t)
    if m:
        n = _to_int(m.group(1))
        if n:
            return (1, n)
    m = re.search(rf"{_NUM}\s*页", t)
    if m:
        n = _to_int(m.group(1))
        if n:
            return (n, n)
    return None


# ------------------------------------------------------- 页数诉求 / 封面诉求
#
# 用户在预览阶段常常只写「增加页数」「少几页」，不给数字。过去这类指令完全落空：
# `parse_page_budget` 只认「N 页」，解析不到就原样沿用旧预算，于是用户反复改稿、
# 页数却一直停在 1 页（C031 即为此类）。下面把「定性诉求 → 具体页数」补上。
#
# 措辞必须写死在常量里而不是靠模糊匹配：改稿文本里「保留页数」「保留全部名次与
# 页数」这类要求极常见，宽泛匹配会把「保持」误判成「增加」。

#: 「增加/减少页数」每次调整的步长（页）
PAGE_STEP = 2
#: 任何稿件的最小页数。要求封面的平台会把首页做封面，低于 2 页就没有正文页。
MIN_PAGES = 2

_PAGE_UP = re.compile(
    r"(?:增加|加多|多加|增多|扩充|扩展|加长|再加|再多|多分|加到|分成|拆成|拆开|展开)"
    r"\s*(?:成|为|到)?\s*(?:几|两|三|四|多)?\s*(?:页|篇幅)"
    r"|页数\s*(?:多|增加|加多|不够|太少|偏少)"
    r"|篇幅\s*(?:多|增加|加长|扩)"
)
_PAGE_DOWN = re.compile(
    r"(?:减少|缩减|压缩|精简|缩短|删减|去掉)"
    r"\s*(?:成|为|到)?\s*(?:几|两|三|四|多)?\s*(?:页|篇幅)"
    r"|少\s*几\s*页"
    r"|页数\s*(?:少|减少|太多|过多|偏多)"
)
#: 旧稿里由本程序写入的页数句子。改稿时要先删掉再写新的，
#: 否则「全稿恰好1页」会和「增加页数」同时进入提示词，模型只能听信前者。
_PAGE_NOTE = re.compile(r"全稿[^。\n]{0,24}?页[^。\n]*。?")

_COVER_WANT = re.compile(
    r"(?<![不别没无需])(?:增加|加多|加上|加个|加一个|配上|补上|带上|做成|生成|自动|要|需要)"
    r"\s*(?:一个|一张|独立|单独的)?\s*(?:封面|首图)"
)
_COVER_REJECT = re.compile(
    r"(?:不需要|无需|不用|不加|不要|去掉|删除|取消|没有|别加)"
    r"\s*(?:一个|一张|独立|单独的)?\s*(?:封面|首图)"
)


def parse_page_direction(text: str) -> str | None:
    """识别**没有数字**的页数诉求：返回 'up' / 'down'，没提到页数返回 None。

    有数字时先走 `parse_page_budget`（用户给了具体页数就以具体页数为准），
    这里只负责「增加页数 / 少几页」这类定性措辞。两种措辞同时出现时，
    以最后出现的那一个为准——用户改主意时通常写在后面。
    """
    t = text or ""
    up, down = _PAGE_UP.search(t), _PAGE_DOWN.search(t)
    if up and down:
        return "up" if up.start() > down.start() else "down"
    if up:
        return "up"
    if down:
        return "down"
    return None


def adjust_page_budget(
    form_id: str | None, page_min, page_max, direction: str
) -> tuple[int, int] | None:
    """把定性页数诉求换算成具体页数区间；无可调整时返回 None（保持原样）。

    - 增加：以当前上限为基准再加一个步长，**并把下限抬到原上限**。用户说
      「增加页数」是想多看到几页，只放宽上限的话模型仍会按下限出稿，
      改完稿页数一页没变（这是改稿环节最容易被忽略的一处空转）。
    - 减少：不低于 `MIN_PAGES`；本来就没有页数约束、或已经不能再少时不做改动。
    """
    spec = FORMS.get(form_id or "") or FORMS["explainer"]
    cur_min = max(1, int(page_min or 0))
    cur_max = max(cur_min, int(page_max or 0)) if cur_min else 0
    if direction == "up":
        # 没有页数预算时以题型默认下限为基准，避免一次「增加」就冲到十几页。
        base = cur_max or spec.page_min
        high = max(base + PAGE_STEP, spec.page_min)
        low = max(MIN_PAGES, base)
        return (low, max(low, high))
    if not cur_max or cur_max <= MIN_PAGES:
        return None
    return (MIN_PAGES, max(MIN_PAGES, cur_max - PAGE_STEP))


def strip_page_budget_notes(text: str) -> str:
    """删掉旧稿里过期的页数句子，避免和新要求自相矛盾。"""
    cleaned = _PAGE_NOTE.sub("", text or "")
    return "\n".join(line for line in cleaned.splitlines() if line.strip())


def page_budget_note(page_min: int, page_max: int) -> str:
    """写进创作要求的页数句子；措辞与首轮创作保持一致。"""
    if page_min == page_max:
        return f"全稿恰好{page_min}页，封面计入页数。"
    return f"全稿{page_min}～{page_max}页，封面计入页数。"


def parse_cover_request(text: str) -> bool | None:
    """识别用户对封面的明确要求；返回 True（要封面）/ False（不要）/ None（没提到）。

    None 与 False 必须分开：前者是「别拿平台默认去猜用户」，后者是「用户明确说不要」。
    """
    t = text or ""
    reject, want = _COVER_REJECT.search(t), _COVER_WANT.search(t)
    if reject and (not want or reject.start() > want.start()):
        return False
    if want:
        return True
    return None


# ---------------------------------------------------------------- 内容详细程度
#
# 创作阶段由界面选「精简／均衡／详细」，改稿阶段只能靠一句话。过去这句话只是
# 自由文本进提示词，改完稿篇幅往往没变——用户说「更详细」看不到差别。
# 这里把它变成可校验的具体约束：发布文案字数上限（`caption_max`，模型 schema
# 与校验都真的按它拦）+ 一条写给模型的明确指示。
#
# 步进换页数、换密度都必须先删掉旧指示：留着「精简」会和「更详细」同时在提示词里，
# 模型只会挑最具体的那条听——这正是 C031 的成因。

#: 详细程度 → 发布文案字数区间（与创作阶段的界面选项保持一致）
DENSITY_CAPTION = {"short": (None, 300), "balanced": (None, 500), "detailed": (None, 850)}

_DENSITY_DOWN = re.compile(
    r"更短|再短|短一点|短一些|精简|简化|简化一下|去掉重复|删除重复|压缩(?!页|篇幅)|"
    r"少写|写少|减少篇幅|不要(?:那么|太)长|太长了"
)
_DENSITY_UP = re.compile(
    r"更详细|再详细|详细点|详细一些|补充细节|补充说明|展开(?:说明|解释|讲|写)|"
    r"多写|写得更具体|更具体|深入(?:说明|解释)|充分(?:说明|解释)"
)
#: 本程序写入的详细程度句子。改稿时先删旧的再写新的。
_DENSITY_NOTE = re.compile(r"内容详细程度：[^。\n]*。?")


def parse_density_direction(text: str) -> str | None:
    """识别「更短更精简 / 更详细」这类**没有数字**的详细程度诉求。

    返回 'short' / 'detailed'，没提到返回 None。同时出现时以后出现的那一个为准
    ——用户改主意通常写在后面。明确写了字数（「文案控制在 200～260 字」）时不走这里，
    数字优先，见 `studio.revise`。
    """
    t = text or ""
    up, down = _DENSITY_UP.search(t), _DENSITY_DOWN.search(t)
    if up and down:
        return "detailed" if up.start() > down.start() else "short"
    if up:
        return "detailed"
    if down:
        return "short"
    return None


def strip_density_notes(text: str) -> str:
    """删掉旧稿里过期的详细程度句子，避免和新要求自相矛盾。"""
    cleaned = _DENSITY_NOTE.sub("", text or "")
    return "\n".join(line for line in cleaned.splitlines() if line.strip())


def density_note(direction: str) -> str:
    """写进创作要求的详细程度句子。只说篇幅与解释深度，不动页数与对象数量。"""
    if direction == "short":
        return "内容详细程度：精简，每页只保留核心结论与必要说明，删除重复解释、铺垫和客套，但不得删减具体对象、名次、数据与事实。"
    return "内容详细程度：详细，每页补充具体依据、判断理由与可操作细节，不得凑字数、不得编造，页数与对象数量保持不变。"


def parse_caption_budget(text: str) -> tuple[int | None, int] | None:
    """Count all characters, including spaces/punctuation/newlines; never ask the model to guess."""
    t=text or ''
    prefix=r'(?:发布(?:文案|正文)|两版文案|文案)[^。；;\n]{0,18}?'
    matches=list(re.finditer(prefix+r'(\d{1,4})\s*[~～\-—–至到]\s*(\d{1,4})\s*字',t))
    if matches:
        a,b=map(int,matches[-1].groups());lo,hi=min(a,b),max(a,b)
        # 用户自己写多少就按多少，不再用 2200 字之类的外部上限替他决定。
        if not lo:
            return None
        return lo,hi
    matches=list(re.finditer(prefix+r'(?:不超过|最多|至多|限|控制在)\s*(\d{1,4})\s*字',t))
    if matches:
        hi=int(matches[-1].group(1))
        if hi < 1:
            return None
        return None,hi
    return None


def parse_rank_count(text: str, *, explicit_only: bool = False) -> int | None:
    """从自由文本里解析榜单条目数：TOP10 / 前5 / 十大 / 10类 / 8款 …"""
    t = text or ""
    m = re.search(r"[Tt][Oo][Pp]\s*(\d{1,2})", t)
    if m:
        return _clamp_rank(int(m.group(1)))
    m = re.search(rf"(?:前|排名前)\s*{_NUM}", t)
    if m:
        n = _to_int(m.group(1))
        if n:
            return _clamp_rank(n)
    if explicit_only:return None
    t = re.sub(rf"每(?:页|项|条)\s*{_NUM}\s*(?:个|种|款|名|条|项)", "", t)
    m = re.search(rf"{_NUM}\s*(?:大|强|类|个|种|款|名|条|项)", t)
    if m:
        n = _to_int(m.group(1))
        if n:
            return _clamp_rank(n)
    return None

def parse_detail_limit(text):
    matches=list(re.finditer(r'(?:每[条项张]的?)?(?:详情|detail|卡片说明)[^。；;\n]{0,8}?(?:不超过|不得超过|最多|不超|限|控制在)\s*(\d{1,2})\s*(?:字|字符)',text or '',re.I))
    if not matches:return None
    value=int(matches[-1].group(1))
    # 用户写多少就按多少，不再强加 64 字上限。
    return value if value >= 1 else None


def _clamp_rank(n: int) -> int | None:
    """解析原文数量；支持范围由创作简报明确校验，不替用户增删对象。"""
    return n if 1 <= n <= 99 else None


#: 解析优先级：用户显式写的题型词 > 选题里的 > 提纲里的
_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("meme", FORMS["meme"].keywords),
    ("ranking", FORMS["ranking"].keywords),
    ("directory", FORMS["directory"].keywords),
    ("comparison", FORMS["comparison"].keywords),
    ("review", FORMS["review"].keywords),
    ("tutorial", FORMS["tutorial"].keywords),
    ("guide", FORMS["guide"].keywords),
    ("listicle", FORMS["listicle"].keywords),
)


def detect_form(topic: str = "", requirements: str = "", outline=()) -> FormId:
    """判断题材形态。用户显式写的题型词优先级最高。"""
    # Negative constraints name forbidden forms; they must not select those forms.
    req = re.sub(r'(?:不要|不得|不做|不写|不能|禁止|别改成|别写|别做|不是|无需)[^，,。；;\n]*','',(requirements or '')).lower()
    top = (topic or "").lower()
    out = " ".join(outline or []).lower()
    # A descriptive mention of a "source list" must not replace an explicit
    # TOP10 or directory requested in the title. Only a deliberate form change
    # in the requirements can override that strong intent.
    deliberate=bool(re.search(r'(?:改为|改成|换成|不要.*?而是|做成).*?(?:榜单|排行|分类速查|速查表|教程|对比|清单)',req))
    if not deliberate:
        if re.search(r'\btop\s*\d{1,2}',top):return 'ranking'
        if any(k in top for k in FORMS['directory'].keywords):return 'directory'

    # "说明个人差异" is a supporting requirement, not an instruction to turn
    # a guide into a two-column comparison. Honour the explicitly named product.
    named=re.search(r'(?:做成|做一个|制作|写一份|改为|改成|换成)[^，。；\n]{0,16}?(梗指南|上手指南|教程|知识指南|生活指南|赛制指南|图解|对比|排行榜|分类速查)',req)
    if named:
        return {'梗指南':'meme','上手指南':'tutorial','教程':'tutorial','知识指南':'guide','生活指南':'guide','赛制指南':'guide','图解':'explainer','对比':'comparison','排行榜':'ranking','分类速查':'directory'}[named.group(1)]

    for fid, kws in _RULES:
        if any(k in req for k in kws):
            return fid  # type: ignore[return-value]
    if re.search(r'如何看待|怎么看待|怎么看|有何看法|如何评价',top):return 'explainer'
    for fid, kws in _RULES:
        if any(k in top for k in kws):
            return fid  # type: ignore[return-value]
    if re.search(rf'{_NUM}\s*(?:个|道|条|项)\s*(?:问题|面试题|考点|要点|技巧|建议)',top):return 'listicle'
    for fid, kws in _RULES:
        if any(k in out for k in kws):
            return fid  # type: ignore[return-value]
    return "explainer"


def build_brief(*, topic: str, requirements: str = "", outline=(), audience: str = "") -> CreativeBrief:
    """把自由文本要求解析成结构化简报。纯函数，不调用模型。"""
    notes = requirements or ""
    budget = parse_page_budget(notes) or parse_page_budget(topic)
    form_id = detect_form(topic, notes, outline)
    rank_count = parse_rank_count(notes,explicit_only=True) or parse_rank_count(topic,explicit_only=True) or parse_rank_count(notes) or parse_rank_count(topic)
    # 只有用户真的写了数量（TOP10 / 前五 / 15 项）才把条数当作硬约束；
    # 没有写就不替他决定条数，交给资料能完整列出的真实数量。
    period = None
    if '上个月' in topic + notes or '上月' in topic + notes:
        from datetime import datetime, timedelta
        current = datetime.now().astimezone().date().replace(day=1)
        end = current - timedelta(days=1)
        period = f'{end.replace(day=1).isoformat()} 至 {end.isoformat()}（自然月，不等同于滚动30天）'

    caption_budget=parse_caption_budget(notes)
    form = FORMS[form_id]
    page_min, page_max = (budget if budget else (None, None))
    if page_min and page_max:
        page_min, page_max = max(1, page_min), max(page_min, page_max)

    detected = []
    if budget:
        detected.append(f"页数 {page_min}–{page_max}")
    if rank_count and form_id == "ranking":
        detected.append(f"榜单 {rank_count} 条")
    detected.append(f"形态 {form.name}")

    return CreativeBrief(
        form=form_id,
        item_count=rank_count if form_id in {"directory","listicle"} else None,
        form_name=form.name,
        theme=form.theme,
        framework=list(form.structure),
        allowed_kinds=list(form.allowed_kinds),
        required_kinds=list(form.required_kinds),
        page_min=page_min,
        page_max=page_max,
        explicit_pages=bool(budget),
        want_cover=parse_cover_request(notes) is True,
        # 只有榜单/清单形态把条目数当作结构约束，其它形态仅作参考
        rank_count=rank_count if form_id in {"ranking", "listicle"} else None,
        detected_from="；".join(detected),
        original_topic=topic, original_requirements=notes, time_range=period,
        caption_min=caption_budget[0] if caption_budget else None, caption_max=caption_budget[1] if caption_budget else None,
        detail_max=parse_detail_limit(notes),
    )


def planning_pages(brief: "CreativeBrief | dict | None") -> int:
    """规划阶段应规划几页。接受 CreativeBrief 或它的 dict 形式。"""
    if brief is None:
        return 4
    from .token_cost import is_token_cost
    topic=brief.get('original_topic','') if isinstance(brief,dict) else brief.original_topic
    if isinstance(brief, dict):
        form_id = brief.get("form") or "explainer"
        explicit = bool(brief.get("explicit_pages"))
        page_max = brief.get("page_max")
    else:
        form_id = brief.form
        explicit = brief.explicit_pages
        page_max = brief.page_max
    if explicit and page_max:
        return max(1, int(page_max))
    if is_token_cost(topic):return max(1,int(page_max or 2))
    return max(1, FORMS[form_id].page_min)
