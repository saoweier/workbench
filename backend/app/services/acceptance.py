"""专项验收契约：把「容易出错的成品要求」写成程序可判定的清单。

程序负责判定，不采信模型自述。参考开源项目的做法：把容易被模型糊弄过去、
但一眼就能数出来的成品要求交给本地代码检查，而不是只写进提示词里祈祷。

两个专项：

- **榜单（TOP N）**：名次版面存在、条目数等于要求、名次跨页连续不重复、
  对象互不重复、展示的指标必须与它所在的对象对应且同源。
- **渲染产物**：导出图片数量与页数一致、页序完整无缺。

契约同时返回「结构化的逐项检查」与「可读的问题文案」：
前者供交付物清单逐条展示（10/10 ✓），后者供校验直接抛错。
"""
from __future__ import annotations

import re
from typing import Any

from ..core.errors import ValidationFailed


def _norm_label(label: Any) -> str:
    """去掉程序自己加的名次前缀，只留对象名，用于去重比较。"""
    return re.sub(r"^\s*\d+[.、\s]+", "", str(label or "")).strip().casefold()


def _rank_items(pages) -> list[dict]:
    return [i for p in (pages or []) if (p.get("visual") or {}).get("kind") == "rank"
            for i in (p.get("visual") or {}).get("items", [])]


def _metric_problems(items: list[dict], sources) -> list[str]:
    """指标必须属于它所在的那一条：数值、口径、来源三者齐全，且与对象同源。"""
    problems: list[str] = []
    for item in items:
        metric = item.get("metric_text")
        label = item.get("metric_label")
        sid = item.get("metric_source_id")
        if not metric:
            if label or sid:
                problems.append(f"{item.get('label', '')}没有数值却带了口径或来源")
            continue
        if not label or not sid:
            problems.append(f"{item.get('label', '')}显示了数值但缺少统计口径或来源")
    if sources:
        # 有可读来源时才做同源比对；没来源不误报（宁可漏报也不把"缺来源"混进"对不上"）
        from .content_recipes import validate_metrics
        with_metric = [i for i in items if i.get("metric_text")]
        if with_metric:
            try:
                validate_metrics(with_metric, sources)
            except ValidationFailed as exc:
                problems.append(str(exc))
    return problems


def rank_contract(pages, rank_count, *, sources=None) -> dict:
    """榜单专项验收。返回结构化结果，供平台稿校验与交付物清单共用。"""
    want = int(rank_count or 10)
    rank_pages = [p for p in (pages or []) if (p.get("visual") or {}).get("kind") == "rank"]
    items = _rank_items(pages)
    names = [_norm_label(i.get("label")) for i in items]
    ranks = [i.get("rank") for i in items]
    metric_problems = _metric_problems(items, sources)

    count_ok = len(items) == want
    unique_ok = len(set(names)) == len(names)
    order_ok = ranks == list(range(1, len(items) + 1))

    checks = [
        {"key": "rank_board", "code": "RANK_BOARD_MISSING", "label": "名次版面",
         "expected": "至少一页 visual.kind=rank",
         "actual": f"{len(rank_pages)} 页" if rank_pages else "无",
         "passed": bool(rank_pages),
         "problem": "排行榜题材缺少名次版面（visual.kind=rank）"},
        {"key": "rank_count", "code": "RANK_COUNT_MISMATCH", "label": "榜单项数",
         "expected": want, "actual": len(items), "passed": count_ok,
         "problem": f"排行榜需要 {want} 条名次，实际只有 {len(items)} 条"},
        {"key": "rank_unique", "code": "RANK_DUPLICATE_OBJECT", "label": "对象互不重复",
         "expected": len(items), "actual": len(set(names)), "passed": unique_ok,
         "problem": "排行榜存在重复对象，必须完整列出互不重复的具体对象"},
        {"key": "rank_order", "code": "RANK_ORDER_BROKEN", "label": "名次跨页连续",
         "expected": f"1–{len(items)}", "actual": "连续" if order_ok else f"不连续 {ranks}",
         "passed": order_ok,
         "problem": "排行榜名次必须跨页连续且不重复，从1排列到指定数量"},
        {"key": "rank_metric", "code": "RANK_METRIC_MISMATCH", "label": "指标对应项目",
         "expected": "每个数值都有口径、来源且与对象同源",
         "actual": "通过" if not metric_problems else metric_problems[0],
         "passed": not metric_problems,
         "problem": metric_problems[0] if metric_problems else ""},
    ]
    problems = [c["problem"] for c in checks if not c["passed"] and c["problem"]]
    return {"applicable": True, "passed": not problems, "checks": checks, "problems": problems}


def rank_problem(pages, rank_count, *, sources=None):
    """榜单验收的第一条问题；无问题返回 None（供平台稿校验直接抛错）。"""
    result = rank_contract(pages, rank_count, sources=sources)
    return result["problems"][0] if result["problems"] else None


def export_contract(page_count, images) -> dict:
    """渲染产物验收：导出图片数量与页数一致、页序完整。"""
    indices = sorted(int(i["page_index"]) for i in (images or [])
                     if isinstance(i, dict) and i.get("page_index") is not None)
    expected = list(range(1, int(page_count) + 1))
    count_ok = len(images or []) == int(page_count)
    order_ok = indices == expected
    checks = [
        {"key": "export_count", "code": "IMAGE_COUNT_MISMATCH", "label": "导出图片数量",
         "expected": int(page_count), "actual": len(images or []), "passed": count_ok,
         "problem": f"导出图片数量与页数不一致：应有 {page_count} 张，实际 {len(images or [])} 张"},
        {"key": "export_pages", "code": "PAGE_SEQUENCE_INCOMPLETE", "label": "页序完整",
         "expected": f"1–{int(page_count)}",
         "actual": indices if len(indices) <= 20 else "页序不全",
         "passed": order_ok,
         "problem": f"导出图片页序不完整或重复：期望 1–{int(page_count)}，实际 {indices}"},
    ]
    problems = [c["problem"] for c in checks if not c["passed"]]
    return {"applicable": True, "passed": not problems, "checks": checks, "problems": problems}
