"""Regression coverage for audit replies carrying the team's blocking_gaps field."""
from app.services.content_skills import (
    ContentAudit,
    finalize_audit_result,
    normalize_audit_response,
)


def test_audit_blocking_gaps_are_preserved_and_fail_review():
    raw = {
        "passed": True,
        "summary": "两平台内容已完成审核。",
        "requirements_coverage": ["原题核心内容已覆盖"],
        "issues": [],
        "blocking_gaps": ["豆瓣条目正文未提供电影剧情，不能据片名推断剧情"],
    }

    result = ContentAudit.model_validate(normalize_audit_response(raw))
    result = finalize_audit_result(result)

    assert result.blocking_gaps == raw["blocking_gaps"]
    assert result.passed is False
    assert any("不能据片名推断剧情" in issue.problem for issue in result.issues)
    assert any(issue.severity == "error" for issue in result.issues)


def test_audit_without_blocking_gaps_keeps_existing_decision():
    raw = {
        "passed": True,
        "summary": "内容审核完成。",
        "requirements_coverage": ["原题核心内容已覆盖"],
        "issues": [],
    }

    result = finalize_audit_result(ContentAudit.model_validate(normalize_audit_response(raw)))

    assert result.passed is True
    assert result.blocking_gaps == []
    assert result.issues == []


def test_audit_accepts_all_covered_requirements_without_arbitrary_twelve_item_cap():
    coverage = [f"用户要求 {index} 已覆盖" for index in range(1, 15)]
    raw = {
        "passed": True,
        "summary": "逐项核对了全部要求。",
        "requirements_coverage": coverage,
        "issues": [],
    }

    result = ContentAudit.model_validate(normalize_audit_response(raw))

    assert result.requirements_coverage == coverage


if __name__ == "__main__":
    test_audit_blocking_gaps_are_preserved_and_fail_review()
    print("PASS audit blocking gaps are preserved and block review")
    test_audit_without_blocking_gaps_keeps_existing_decision()
    print("PASS audit without gaps preserves the original decision")
    test_audit_accepts_all_covered_requirements_without_arbitrary_twelve_item_cap()
    print("PASS audit accepts more than twelve covered requirements")
    print("3 passed / 0 failed")
