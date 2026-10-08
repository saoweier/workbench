"""Regression coverage for malformed but trivially recoverable model JSON."""
from app.services.adapters.base import BaseAdapter


def check(name, condition):
    assert condition, name
    print("PASS " + name)


text = '{"must_include":["source note", "keep the point"]", "pages":[{"index":1}]}'
parsed, error = BaseAdapter.safe_json(text)
check("extra quote after array is recovered without changing values", error is None and parsed == {
    "must_include": ["source note", "keep the point"], "pages": [{"index": 1}],
})

parsed, error = BaseAdapter.safe_json('{"pages":[,]}')
check("unrecoverable malformed JSON remains BAD_JSON", parsed is None and error == "BAD_JSON")

parsed, error = BaseAdapter.safe_json('{"quote":"]"}')
check("quotes inside legitimate string values are preserved", error is None and parsed == {"quote": "]"})

model_audit = '''{"passed": true, "summary": "审核完成", "requirements_coverage": ['原题已覆盖', '页数已核对'], "issues": [{'severity': 'warning', 'page': 1, 'problem': '正文有一处重复', 'suggestion': '删去重复句'}], "blocking_gaps": []}'''
parsed, error = BaseAdapter.safe_json(model_audit)
check("Python-style nested quotes in model audit are recovered as literals", error is None and parsed == {
    "passed": True,
    "summary": "审核完成",
    "requirements_coverage": ["原题已覆盖", "页数已核对"],
    "issues": [{"severity": "warning", "page": 1, "problem": "正文有一处重复", "suggestion": "删去重复句"}],
    "blocking_gaps": [],
})

parsed, error = BaseAdapter.safe_json('{"passed": true, "summary": __import__("os").system("whoami")}')
check("non-literal expressions are still rejected", parsed is None and error == "BAD_JSON")

print("5 passed / 0 failed")
