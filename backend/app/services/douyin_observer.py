"""Verify actual works and collect only visibly labeled metrics for one post."""
from datetime import datetime, timezone
import json
import re

from .douyin_flow import verify_account
from .import_service import ImportService, parse_number
from .platform_account import write_json

POST_LINK = re.compile(r"^https://(?:www\.)?douyin\.com/(?:video|note)/(\d{15,25})(?:[/?#].*)?$")
LABELS = {"播放量":"views","播放":"views","阅读量":"views","点赞数":"likes","点赞":"likes",
          "评论数":"comments","评论":"comments","收藏数":"collects","收藏":"collects","分享数":"shares","分享":"shares"}


def parse_metrics(text):
    metrics = {}
    for label, name in LABELS.items():
        matches = re.findall(rf"(?:^|\n){re.escape(label)}[ \t]*[：:]?[ \t]*([\d,]+(?:\.\d+)?[ \t]*[万千kKwW]?|--|暂无数据)(?=\s|$)", text)
        if len(matches)==1:
            raw=matches[0]
            value, unit, errors = parse_number(None if raw in {"--","暂无数据"} else raw)
            if not errors:
                metrics[name]={"value":value,"unit":"count","raw_label":label,"raw_value":raw}
    return metrics


def find_card(page, title):
    matches = page.get_by_text(title, exact=True)
    if matches.count()!=1:
        return None
    return matches.evaluate(r"""el => {
      let node=el;
      for(let i=0;i<7&&node&&node!==document.body;i++,node=node.parentElement){
        const text=node.innerText||'';
        const links=[...node.querySelectorAll('a[href]')].map(a=>a.href).filter(h=>/^https:\/\/(www\.)?douyin\.com\/(video|note)\/\d+/.test(h));
        const ids=[...node.querySelectorAll('[data-aweme-id],[data-item-id]')].map(n=>n.getAttribute('data-aweme-id')||n.getAttribute('data-item-id'));
        if(node.hasAttribute('data-aweme-id'))ids.push(node.getAttribute('data-aweme-id'));
        if(node.hasAttribute('data-item-id'))ids.push(node.getAttribute('data-item-id'));
        if(text.length>12000)break;
        if(links.length||ids.length)return {text,links,ids};
      }
      return null;
    }""")


def observe_task(page, root, service, task):
    verify_account(page,root,task["account_id"])
    page.goto("https://creator.douyin.com/creator-micro/content/manage",wait_until="domcontentloaded",timeout=30000)
    page.get_by_placeholder("搜索作品",exact=True).wait_for(timeout=15000)
    # Filter by the exact, user-confirmed title. Ambiguous results remain unknown.
    search=page.get_by_placeholder("搜索作品",exact=True)
    search.fill(task["title"])
    search.press("Enter")
    page.wait_for_timeout(2000)
    card=find_card(page,task["title"])
    evidence_root=root/"tasks"/task["task_id"]
    evidence_root.mkdir(parents=True,exist_ok=True)
    page.screenshot(path=str(evidence_root/"observation.png"),full_page=True)
    observed=datetime.now(timezone.utc).isoformat()
    if not card:
        service.finish(task["task_id"],"unknown",result={"observed_at":observed,"metrics_status":"未读取到可核验的作品编号",
            "message":"已提交的作品需要继续核实，尚未凭猜测登记，系统不会自动重发。"},next_seconds=300)
        return
    links=[x for x in card["links"] if POST_LINK.fullmatch(x)]
    ids={POST_LINK.fullmatch(x).group(1) for x in links}
    ids.update(x for x in card["ids"] if re.fullmatch(r"\d{15,25}",x or ""))
    if len(ids)!=1:
        service.finish(task["task_id"],"unknown",error="作品编号缺失或不唯一，未登记、未重新发布。",next_seconds=300)
        return
    post_id=next(iter(ids))
    audit="审核中" if "审核中" in card["text"] else ("未通过" if "未通过" in card["text"] else "已在作品列表中核实")
    evidence={"title":task["title"],"account_id":task["account_id"],"post_id":post_id,"link":links[0] if links else None,
              "observed_at":observed,"audit_state":audit,"raw_card":card["text"]}
    write_json(evidence_root/("observation-"+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+".json"),evidence)
    publication_id=service.record_observed_publication(task["task_id"],evidence)
    metrics=parse_metrics(card["text"])
    available = {name:info for name,info in metrics.items() if info["value"] is not None}
    if available:
        rows=[{"platform":"douyin","post_id":post_id,"observed_at":observed,"metric_name":name,
               "value":info["value"],"unit":"count","aggregation_kind":"cumulative","traffic_type":"unknown"} for name,info in available.items()]
        ImportService(service.sf,service.settings).import_metrics(json.dumps(rows,ensure_ascii=False),fmt="json",
            file_name=f"douyin-browser-{post_id}.json",created_by="browser",run_mode="real")
    service.finish(task["task_id"],"awaiting_review" if audit=="审核中" else ("rejected" if audit=="未通过" else "succeeded"),result={**evidence,"raw_card":None,
        "metrics":metrics,"metrics_status":"已读取实际可见指标" if available else "待平台更新，尚未读取指标",
        "message":"作品已从官方列表核实。审核状态与流量数据分别记录，缺失不补零。"},next_seconds=3600 if audit!="未通过" else None)
