"""Cross-platform isolated regression runner used by Windows and Unix users."""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
import os

ROOT = Path(__file__).resolve().parent.parent
STAGES = ("p0_smoke", "p1_e2e", "p2_e2e", "p3_e2e", "p4_e2e", "p5_e2e", "operations", "delivery", "browser", "autonomous_selection", "visual_content", "production_refresh", "direct_creator", "platform_accounts", "douyin_publishing", "studio_updates", "fruit_quality", "motion_studio", "content_skills", "content_recipes", "evidence_grounding", "meme_images", "token_cost")
SUMMARY = re.compile(r"(?:结果：)?(?P<passed>\d+) 通过 / (?P<failed>\d+) 失败")
STAGES=(*STAGES,'template_packages','planning_repair','style_families','completed_output_repair','guidance','planning_policy','research_agent','searxng','hotpush_research','studio_submission','query_startup','acceptance','deliverables','content_evals','creation_flow')
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def parse_summary(output: str) -> tuple[int, int] | None:
    """Parse the final Chinese test summary without depending on console locale."""
    matches = list(SUMMARY.finditer(output))
    if not matches:
        return None
    match = matches[-1]
    return int(match.group("passed")), int(match.group("failed"))


def main(argv: list[str] | None = None) -> int:
    stages = argv if argv is not None else sys.argv[1:]
    if not stages:
        stages = list(STAGES)
    invalid = [stage for stage in stages if stage not in STAGES]
    if invalid:
        print(f"未知测试阶段：{', '.join(invalid)}。允许值：{', '.join(STAGES)}")
        return 2

    total_passed = 0
    total_failed = 0
    failed_stages: list[str] = []
    child_env = os.environ.copy()
    child_env.setdefault("PYTHONUTF8", "1")
    for stage in stages:
        test = ROOT / "backend" / "tests" / f"test_{stage}.py"
        result = subprocess.run(
            [sys.executable, "-B", str(test)],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=child_env,
            check=False,
        )
        summary = parse_summary(result.stdout)
        print(f"===== {stage} =====")
        lines = result.stdout.splitlines()
        visible = lines if result.returncode or (summary and summary[1]) else lines[-6:]
        print("\n".join(visible))
        if summary is None or result.returncode:
            failed_stages.append(stage)
            print("该阶段未能输出有效统计行或异常退出。")
            continue
        passed, failed = summary
        total_passed += passed
        total_failed += failed
        print(f"阶段统计：{passed} 通过 / {failed} 失败\n")
        if failed:
            failed_stages.append(stage)

    print("=" * 62)
    print(f"全量回归合计：{total_passed} 通过 / {total_failed} 失败")
    if failed_stages:
        print("失败阶段：" + ", ".join(failed_stages))
        return 1
    print("全部阶段通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
