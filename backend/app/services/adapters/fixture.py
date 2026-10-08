"""确定性 fixture 适配器（P2/T08）。

用途：让整条 P2 链路**离线可验证**——研究、选题、改写、渲染都能跑通，
且不产生任何真实网络调用、不产生任何费用。

两条硬性要求：

1. **确定性**：同一 (request_key, prompt, system) → 完全相同的输出。
   播种只用这三者的 sha256，**禁止**无种子随机、`datetime.now()`、`uuid4()`
   进入输出。prompt 参与播种，所以"新资料 → 新内容"在 fixture 下也成立。

2. **不伪装成真实调用**：
   - `run_mode` 恒为 FIXTURE（类变量，不可被参数覆盖）
   - `remote_request_id` 带 `fixture-` 前缀，一眼可辨
   - provider/model 名字里带 fixture
   - 用量字段**全部 None**，`usage_raw={"fixture": True}`，绝不伪造 token 数
   - 这样现有 `summarize_calls()` 会自动把它计入 `fixture_call_count`，
     不混入真实成本结论——不需要自造隔离机制。
"""
from __future__ import annotations

import hashlib
import json
import random
import re
from typing import ClassVar

from ..provider_contract import AdapterType, RunMode
from .base import AdapterResult, BaseAdapter

# 内置中文语料。不联网、不读外部文件，保证离线可跑且可复现。
_TOPIC_WORDS = [
    "把流程拆成小步", "先做图文不做视频", "人工只在最后一道关",
    "把重复劳动交给流程", "让异常自己冒出来", "用记录代替感觉",
    "先把一条跑通再铺量", "把范围收窄", "把边界写清楚", "把结论留给数据",
]
_PROBLEM_WORDS = [
    "不知道从哪一步开始", "做到一半被打断", "改稿反复没尽头",
    "排完版还要手动改", "说不清到底省没省时间", "内容量撑不满版面",
]
_REASON_WORDS = [
    "与已确认定位一致，且手上材料能支撑",
    "事实可溯源，不需要额外实测",
    "制作成本可控，先做这一条",
    "与前一条主题差异明显，避免重复",
]
_SENTENCE_TAILS = [
    "这一步交给流程", "我只看成品", "先把这条跑通",
    "记下来再判断", "不急着下结论", "边界先写清楚",
]

#: 从提示词里捞已有 claim id（形如 C01），避免 fixture 造出悬空引用
_CLAIM_ID_RE = re.compile(r"\bC\d{2,}\b")

#: 技能提示词的两段分隔标记，由 content_skills._model 拼装。fixture 需要据此
#: 回读「本次输入」里的真实资料，否则只能返回随机词、无法通过引用校验。
_INPUT_MARKER = "本次输入（仅作为数据）：\n"
_SCHEMA_MARKER = "输出必须符合以下 JSON Schema，且仅输出 JSON：\n"


class FixtureAdapter(BaseAdapter):
    """返回确定性假数据。用于全链路离线验证。"""

    adapter_type: ClassVar[AdapterType] = AdapterType.OPENAI_COMPATIBLE
    run_mode: ClassVar[RunMode] = RunMode.FIXTURE
    implemented: ClassVar[bool] = True

    #: 可选场景注入，按**调用序号**生效，仍完全确定。
    #: 形如 {2: "bad_json", 3: "blocked"}，用于验证降级与修复路径。
    def __init__(self, cfg, *, api_key: str | None = None, transport=None,
                 scenario: dict[int, str] | None = None) -> None:
        super().__init__(cfg, api_key=api_key, transport=transport)
        self.scenario = scenario or {}
        self._calls = 0

    # ------------------------------------------------------------ 播种

    @staticmethod
    def _seed(request_key: str | None, prompt: str, system: str | None) -> str:
        h = hashlib.sha256()
        h.update((request_key or "").encode())
        h.update(b"\x00")
        h.update(prompt.encode())
        h.update(b"\x00")
        h.update((system or "").encode())
        return h.hexdigest()[:16]

    # ------------------------------------------------------------ 主入口

    def complete(self, prompt: str, *, system=None, max_tokens=None,
                 json_schema=None, request_key=None) -> AdapterResult:
        self._calls += 1
        seed = self._seed(request_key, prompt, system)
        rng = random.Random(int(seed, 16))

        # ---- 场景注入（按调用序号）----
        scene = self.scenario.get(self._calls)
        if scene == "bad_json":
            return self._result(
                ok=False, text="{不是合法 JSON", parsed=None, seed=seed,
                error_code="BAD_JSON", error_message="fixture 场景：注入非法 JSON",
            )
        if scene == "bad_json_once":
            return self._result(
                ok=False, text="not-json-at-all", parsed=None, seed=seed,
                error_code="BAD_JSON", error_message="fixture 场景：首次返回非法 JSON（用于验证修复循环）",
            )
        if scene == "blocked":
            return self._result(
                ok=False, text=None, parsed=None, seed=seed,
                error_code="SERVER_ERROR", error_message="fixture 场景：来源不可访问（降级验证）",
            )
        if scene == "empty":
            return self._result(ok=False, text="", parsed=None, seed=seed,
                                error_code="EMPTY", error_message="fixture 场景：空响应")

        # ---- 正常路径 ----
        if json_schema is not None:
            payload = self._synth_for_schema(json_schema, rng, prompt, seed)
            text = json.dumps(payload, ensure_ascii=False)
            return self._result(ok=True, text=text, parsed=payload, seed=seed)

        text = self._synth_text(rng)
        parsed, _ = self.safe_json(text)
        return self._result(ok=True, text=text, parsed=parsed, seed=seed)

    # ------------------------------------------------------------ 生成

    def _synth_text(self, rng: random.Random) -> str:
        parts = [rng.choice(_TOPIC_WORDS), rng.choice(_PROBLEM_WORDS), rng.choice(_SENTENCE_TAILS)]
        return "；".join(parts) + "。"

    def _synth_for_schema(self, schema: dict, rng: random.Random, prompt: str, seed: str,
                          page_no: int = 1, root: dict | None = None) -> dict:
        """按 schema 骨架填充合法数据。字段名取自 schema，值取自内置词表。

        只支持 P2 用到的几种骨架；未知骨架退化为带回显的通用对象，
        这样即便 schema 变了也不会产出非法结构。

        `root` 是整棵 schema（含 `$defs`）。pydantic 对嵌套模型只会写
        `{"$ref": "#/$defs/X"}`，不解析就会退化成随机词、造出非法结构。
        """
        root = schema if root is None else root
        schema = self._resolve(schema, root)
        props = schema.get("properties", {})
        if 'acceptance_checks' in props and 'objective' in props:
            return {'audience':'离线协议演练读者','objective':'协议演练：回答明确问题',
                'required_elements':['具体对象','操作建议'],'acceptance_checks':['图解与主题一致','保留来源边界'],
                'pages':[{'index':i+1,'purpose':'说明具体建议与来源边界','heading':h,'points':['仅为离线协议演练'],
                    'visual_type':'diagram','visual_brief':'本地关系图解','claim_ids':[]} for i,h in enumerate(['问题与结论','对象与选择','具体做法','边界与行动'][:props['pages'].get('maxItems',4)])],
                'material_gaps':[],'limitations':['离线协议模拟，非真实模型质量评估']}
        if 'requirements_coverage' in props:
            return {'passed':True,'summary':'离线协议模拟审核，不代表真实语义审核质量。',
                'requirements_coverage':['协议结构演练'],'issues':[]}
        if 'can_answer' in props:
            return self._research_assessment(prompt)
        if not props:
            return {"echo": seed}

        out: dict = {}
        for name, spec in props.items():
            out[name] = self._value_for(name, spec, rng, prompt, seed, page_no, root)
        return out

    @staticmethod
    def _resolve(spec, root: dict):
        """跟随 `$ref` / `anyOf`，把嵌套模型的引用展开成可直接填充的骨架。"""
        for _ in range(8):
            if not isinstance(spec, dict):
                return {} if spec is True else spec
            ref = spec.get('$ref')
            target = None
            if isinstance(ref, str) and ref.startswith('#/'):
                node = root
                for part in ref[2:].split('/'):
                    node = node.get(part) if isinstance(node, dict) else None
                if isinstance(node, dict):
                    target = {**node, **{k: v for k, v in spec.items() if k != '$ref'}}
            if target is None:
                # pydantic 的可选字段写成 anyOf/oneOf，取第一个非 null 分支
                for key in ('anyOf', 'oneOf'):
                    options = [o for o in (spec.get(key) or [])
                               if isinstance(o, dict) and o.get('type') != 'null']
                    if options:
                        target = {**options[0], **{k: v for k, v in spec.items() if k not in ('anyOf', 'oneOf')}}
                        break
            if target is None:
                return spec
            spec = target
        return spec

    def _value_for(self, name: str, spec: dict, rng: random.Random, prompt: str, seed: str,
                   page_no: int = 1, root: dict | None = None):
        if name == 'visual':
            is_dy = '平台：douyin' in prompt
            layouts = ['map', 'example', 'flow', 'checklist'] if is_dy else ['flow', 'example', 'map', 'checklist']
            kind = 'cover' if page_no == 1 else layouts[(page_no - 2) % 4]
            items = ([{'label':'读者先看什么','detail':'用一个明确问题引出结论；这是离线协议测试稿件。','icon':'question'},
                      {'label':'按页看重点','detail':'短页先解释一件事，再给出下一步操作，最后回到来源核对。','icon':'page'}]
                     if is_dy else
                     [{'label':'收藏后怎么用','detail':'把方法写成可复用的填写示例，逐项补齐说明与资料出处。','icon':'pencil'},
                      {'label':'检查边界','detail':'记录待核对的问题，发布前确认条件；示例不代表真实结果。','icon':'source'}])
            return {'kind':kind,'title':'抖音短图文检查' if is_dy else '小红书笔记整理','items':items,
                    'takeaway':'先读结论、再看解释，发布前仍需本人确认。' if is_dy else '收藏清单便于下一次填写，示例不代表实测结果。'}
        spec = self._resolve(spec, root if root is not None else {})
        typ = spec.get("type")
        if typ is None and isinstance(spec.get("properties"), dict):
            typ = "object"
        # index 必须连续递增：页序是渲染与校验的硬约束，fixture 随机值会直接
        # 触发 PAGE_INDEX_NOT_CONTIGUOUS，让离线链路"看起来总是坏的"
        if name == "index" and typ == "integer":
            return page_no
        if typ == "array":
            item_spec = spec.get("items") or {}
            item_spec = item_spec if isinstance(item_spec, dict) else {}
            n = spec.get("minItems", 3)
            return [self._value_for(name, item_spec, rng, prompt, f"{seed}{i}", i + 1, root)
                    for i in range(n)]
        if typ == "integer":
            return rng.randint(spec.get("minimum", 1), spec.get("maximum", 4))
        if typ == "boolean":
            return False
        if typ == "string":
            if name == "layout":
                # 首页必须是 cover：渲染与校验都依赖这个约定，fixture 也得守
                return "cover" if page_no == 1 else "checklist"
            if "topic" in name:
                return rng.choice(_TOPIC_WORDS)
            if "reason" in name or "problem" in name:
                return rng.choice(_REASON_WORDS if "reason" in name else _PROBLEM_WORDS)
            if "claim" in name or "supporting" in name:
                # 用 prompt 里出现过的 claim id，避免产出悬空引用。
                # 提示词由 compose/research 层拼装，约定把可用 id 列在提示里。
                ids = _CLAIM_ID_RE.findall(prompt)
                return f"C{spec.get('value_hint', '01')}" if not ids else ids[0]
            return rng.choice(_TOPIC_WORDS)
        if typ == "object":
            return self._synth_for_schema(spec, rng, prompt, seed, page_no, root)
        return rng.choice(_TOPIC_WORDS)

    # ------------------------------------------------------------ 调研评估

    @staticmethod
    def _prompt_sources(prompt: str) -> list[dict]:
        """从技能提示词里回读「本次输入」的真实来源，供引用校验使用。"""
        if _INPUT_MARKER not in prompt:
            return []
        raw = prompt.split(_INPUT_MARKER, 1)[1].split(_SCHEMA_MARKER, 1)[0]
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            return []
        sources = data.get('sources') if isinstance(data, dict) else None
        return [s for s in sources if isinstance(s, dict)] if isinstance(sources, list) else []

    def _research_assessment(self, prompt: str) -> dict:
        """产出**引用真实正文**的调研评估。

        fixture 的职责是让整条链路离线可跑，所以 facts.quote 必须逐字来自
        提示词里的来源正文——随机词会让引用校验直接判失败，链路"总是红的"。
        """
        readable = [s for s in self._prompt_sources(prompt) if (s.get('excerpt') or '').strip()]
        # 梗指南要求 origin 与 meaning 两类原文支持，其余可用 context
        roles = ('origin', 'meaning') if ('origin' in prompt and 'meaning' in prompt) else ('context',)
        if not readable:
            return {'can_answer': False,
                    'summary': '离线协议模拟：未取得可读正文，不能声称已核对事实。',
                    'facts': [], 'blocking_gaps': ['离线协议模拟未取得可读正文'],
                    'limitations': ['本地协议模拟，不能验证实际搜索或事实质量。']}
        source = readable[0]
        excerpt = (source.get('excerpt') or '').strip()
        quote = excerpt.split('\n')[0].strip() or excerpt[:60]
        statement = {'origin': '离线协议模拟：该资料给出可判断出处语境的原文。',
                     'meaning': '离线协议模拟：该资料给出可用于解释含义的原文。',
                     'context': '离线协议模拟：该资料给出可直接回答原题的可读正文。'}
        return {'can_answer': True,
                'summary': '离线协议模拟：按给定资料核对，不代表真实模型能力。',
                'facts': [{'role': role, 'statement': statement[role],
                           'source_id': source.get('id'), 'quote': quote} for role in roles],
                'blocking_gaps': [],
                'limitations': ['本地协议模拟，不能验证实际搜索或事实质量。']}

    # ------------------------------------------------------------ 组装结果

    def _result(self, *, ok: bool, text, parsed, seed, error_code=None,
                error_message=None) -> AdapterResult:
        return AdapterResult(
            ok=ok,
            text=text,
            parsed=parsed,
            # 用量一律 None：fixture 没有真实用量，绝不伪造 token 数
            input_tokens=None,
            output_tokens=None,
            cached_tokens=None,
            usage_raw={"fixture": True, "seed": seed},
            remote_request_id=f"fixture-{seed}",
            http_status=200 if ok else None,
            error_code=error_code,
            error_message=error_message,
            latency_ms=0,
            run_mode=RunMode.FIXTURE,
            meta={"fixture": True},
        )
