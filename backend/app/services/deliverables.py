"""单次任务的交付物清单：把「这次到底产出了什么」写成一份可逐项核对的清单。

参考开源项目的做法：交付物不是"一堆文件"，而是**有名字、有数量、可核对**的
清单——来源快照、筛选记录、逐页卡片文案、视觉提示、成品图、发布文案，外加
程序的专项验收结果。任何一项缺失或对不上都在清单里显式标出，不靠用户自己
翻目录猜。清单只读、可重复计算，不写入内容状态。
"""
from __future__ import annotations

from ..core.errors import NotFound
from ..models.entities import Artifact, ContentItem, ContentRevision, PlatformRevision
from .acceptance import export_contract, rank_contract

#: 单次任务的交付物清单固定包含这几类（key 的稳定后缀）。
#: 路由用例表 `evals.json` 引用同一份键名，避免"清单说有、程序没产"。
DELIVERABLE_KEYS = ("sources", "screening", "copy", "visual", "images", "copy_text", "acceptance")


def _row(key, label, expected, actual, passed, note=""):
    return {"key": key, "label": label, "expected": expected, "actual": actual,
            "passed": bool(passed), "note": note}


def _page_text_ok(page: dict) -> bool:
    """每页必须有一句能读的标题，并且有正文行或图解条目之一。"""
    visual = page.get("visual") or {}
    body = [b for b in (page.get("body") or []) if str(b).strip()]
    items = visual.get("items") or []
    return bool((page.get("heading") or "").strip()) and (bool(body) or bool(items))


def _visual_ok(page: dict) -> bool:
    """没有图解的纯文字页合规；一旦带了图解，就必须有类型、标题和逐条文案。"""
    visual = page.get("visual") or {}
    if not visual.get("kind"):
        return True
    return (bool((visual.get("title") or "").strip())
            and all((i.get("label") or "").strip() and (i.get("detail") or "").strip()
                    for i in (visual.get("items") or [])))


def _page_copy(page: dict) -> dict:
    visual = page.get("visual") or {}
    return {
        "index": page.get("index"),
        "layout": page.get("layout"),
        "heading": page.get("heading"),
        "kicker": page.get("kicker", ""),
        "body": list(page.get("body") or []),
        "footnote": page.get("footnote", ""),
        "claim_ids": list(page.get("claim_ids") or []),
        "visual": {
            "kind": visual.get("kind"),
            "title": visual.get("title"),
            "takeaway": visual.get("takeaway"),
            "presentation": visual.get("presentation"),
            "items": [{"label": i.get("label"), "detail": i.get("detail"),
                       "icon": i.get("icon"), "rank": i.get("rank"),
                       "category": i.get("category", ""), "tags": list(i.get("tags") or []),
                       "metric_text": i.get("metric_text"), "metric_label": i.get("metric_label"),
                       "metric_source_id": i.get("metric_source_id")}
                      for i in (visual.get("items") or [])],
        },
    }


def build(sf, content_id: str, *, platform: str | None = None, revision_id: str | None = None,
          platform_revision_id: str | None = None) -> dict:
    """组装一次任务的交付物清单。找不到内容/版本时抛 NotFound，不返回半个清单。

    `platform_revision_id` 用于精确描述"正在导出的这一版"，而不是该平台最新一版。
    """
    with sf() as s:
        content = s.get(ContentItem, content_id)
        if content is None:
            raise NotFound(f"内容不存在：{content_id}")
        pinned = s.get(PlatformRevision, platform_revision_id) if platform_revision_id else None
        rev_id = revision_id or (pinned.content_revision_id if pinned else content.active_revision_id)
        revision = s.get(ContentRevision, rev_id) if rev_id else None
        if revision is None or revision.content_id != content.id:
            raise NotFound("该内容还没有可交付的版本")

        brief = dict(revision.brief_json or {})
        claims_doc = dict(revision.claims_json or {})
        brief_json = brief.get("creative_brief") or {}
        if pinned:
            candidates = [pinned]
        else:
            candidates = (s.query(PlatformRevision)
                          .filter_by(content_revision_id=revision.id)
                          .order_by(PlatformRevision.platform, PlatformRevision.version.desc()).all())
        rows = [r for r in candidates if not platform or r.platform == platform]
        # 每个平台只取最新版本：清单描述"这次交出来的是什么"
        latest: dict[str, PlatformRevision] = {}
        for pr in rows:
            latest.setdefault(pr.platform, pr)
        arts = {pr.id: s.query(Artifact).filter_by(platform_revision_id=pr.id)
                .order_by(Artifact.page_index).all() for pr in latest.values()}

        content_info = {
            "id": content.id, "display_id": content.display_id, "topic": content.topic,
            "state": content.state, "run_mode": content.run_mode,
            "selected_by": content.selected_by, "selection_reason": content.selection_reason,
            "created_at": content.created_at.isoformat() if content.created_at else None,
        }
        revision_info = {
            "id": revision.id, "version": revision.version,
            "created_at": revision.created_at.isoformat() if revision.created_at else None,
            "user_requirements": brief.get("user_requirements", ""),
            "skill_versions": brief.get("skill_versions", {}),
        }
        brief_info = {
            "audience_problem": brief.get("audience_problem", ""),
            "core_viewpoint": brief.get("core_viewpoint", ""),
            "actions": list(brief.get("actions") or []),
            "form": brief_json.get("form", ""), "form_name": brief_json.get("form_name", ""),
            "rank_count": brief_json.get("rank_count"), "item_count": brief_json.get("item_count"),
            "page_min": brief_json.get("page_min"), "page_max": brief_json.get("page_max"),
            "template_id": brief_json.get("template_id", "illustrated"),
        }
        sources = [{"id": x.get("id"), "kind": x.get("kind"), "locator": x.get("locator"),
                    "url": x.get("url"), "access_state": x.get("access_state"),
                    "excerpt_basis": x.get("excerpt_basis"), "retrieved_at": x.get("retrieved_at"),
                    "limitations": x.get("limitations", "")}
                   for x in (claims_doc.get("sources") or [])]
        claims = [{"id": x.get("id"), "kind": x.get("kind"), "statement": x.get("statement"),
                   "source_ids": list(x.get("source_ids") or [])}
                  for x in (claims_doc.get("claims") or [])]

        platforms = []
        deliverables = []
        all_problems: list[str] = []
        for pr in latest.values():
            pages = (pr.pages_json or {}).get("pages", [])
            page_rows = [_page_copy(p) for p in pages]
            artifacts = arts.get(pr.id, [])
            image_indexes = sorted(a.page_index for a in artifacts)
            missing_pages = [n for n in range(1, len(pages) + 1) if n not in set(image_indexes)]
            export = export_contract(len(pages), [{"page_index": a.page_index} for a in artifacts])
            rank = (rank_contract(pages, (pr.pages_json or {}).get("rank_count"),
                                  sources=claims_doc.get("sources") or [])
                    if (pr.pages_json or {}).get("form") == "ranking" else None)

            draft_ok = bool(pages) and all(_page_text_ok(p) for p in pages)
            visual_ok = bool(pages) and all(_visual_ok(p) for p in pages)
            visual_pages = sum(1 for p in pages if (p.get("visual") or {}).get("kind"))
            bad_copy = [p.get("index") for p in pages if not _page_text_ok(p)]

            row_prefix = pr.platform
            deliverables += [
                _row(f"{row_prefix}.sources", f"[{pr.platform}] 来源快照", "至少 1 份可读资料",
                     f"{len(sources)} 份", bool(sources),
                     "未保存资料时后续事实与数值无从核对"),
                _row(f"{row_prefix}.screening", f"[{pr.platform}] 筛选记录", "1 条选题理由 + 主张",
                     f"{len(claims)} 条主张", bool(claims),
                     "选题理由：" + (content.selection_reason or "未记录")),
                _row(f"{row_prefix}.copy", f"[{pr.platform}] 逐页卡片文案", f"{len(pages)} 页完整",
                     f"{sum(1 for p in page_rows if (p['heading'] or '').strip())} 页有标题",
                     draft_ok, ("缺内容的页：" + ", ".join(map(str, bad_copy))) if bad_copy else
                     "每页都有标题，且有正文行或图解条目"),
                _row(f"{row_prefix}.visual", f"[{pr.platform}] 视觉提示与图解类型", "带图解的页完整",
                     f"{visual_pages}/{len(pages)} 页有图解", visual_ok,
                     "未带图解的页是纯文字版式；带图解的页必须有类型、标题和逐条 label/detail"),
                _row(f"{row_prefix}.images", f"[{pr.platform}] 成品图数量", f"{len(pages)} 张",
                     f"{len(artifacts)} 张", export["passed"],
                     ("缺页：" + ", ".join(map(str, missing_pages))) if missing_pages
                     else ("页序：" + ", ".join(map(str, image_indexes)))),
                _row(f"{row_prefix}.copy_text", f"[{pr.platform}] 发布文案", "标题与正文非空",
                     f"标题 {len(pr.title)} 字 / 正文 {len(pr.caption)} 字",
                     bool(pr.title.strip()) and bool(pr.caption.strip())),
                _row(f"{row_prefix}.acceptance", f"[{pr.platform}] 专项验收",
                     "榜单契约 + 导出契约", "通过" if (export["passed"] and (rank is None or rank["passed"])) else "未通过",
                     export["passed"] and (rank is None or rank["passed"]),
                     "；".join(export["problems"] + (rank["problems"] if rank else []))),
            ]
            all_problems += [f"[{pr.platform}] {p}" for p in export["problems"]]
            if rank:
                all_problems += [f"[{pr.platform}] {p}" for p in rank["problems"]]
            platforms.append({
                "platform": pr.platform, "title": pr.title, "caption": pr.caption,
                "state": pr.state, "platform_revision_id": pr.id,
                "platform_revision_version": pr.version,
                "content_hash": pr.content_hash, "manifest_hash": pr.manifest_hash,
                "form": (pr.pages_json or {}).get("form", ""),
                "pages": page_rows,
                "images": {"expected": len(pages), "actual": len(artifacts),
                           "page_indexes": image_indexes, "missing": missing_pages},
                "acceptance": {"export": export, "rank": rank},
            })

        return {
            "content": content_info, "revision": revision_info, "brief": brief_info,
            "sources": sources, "claims": claims,
            "platforms": platforms, "deliverables": deliverables,
            "acceptance": {"passed": not all_problems, "problems": all_problems},
            "notice": ("清单由程序按当前版本计算，只描述已落库的内容与产物；"
                       "未通过项必须先解决再发布。批准与发布仍由人工完成。"),
        }
