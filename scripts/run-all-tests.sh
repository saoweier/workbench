#!/bin/bash
# 全量回归：P0 → P5 + 运维边界。每阶段独立进程 + 临时库，互不污染。
# 用法： scripts/run-all-tests.sh [阶段名...]   例：scripts/run-all-tests.sh p5_e2e
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT"

STAGES=("$@")
if [ ${#STAGES[@]} -eq 0 ]; then
  STAGES=(p0_smoke p1_e2e p2_e2e p3_e2e p4_e2e p5_e2e operations)
fi

TOTAL_PASS=0
TOTAL_FAIL=0
FAILED_STAGES=()

for t in "${STAGES[@]}"; do
  # 不给测试留任何"能碰到生产数据"的机会。
  #
  # 这里曾经有一行 `rm -rf storage/... storage/artifacts/*`，看起来很贴心，
  # 实际很危险：它会把**生产库里的图片和发布包删掉**，而数据库记录还在，
  # 表现成"预览页显示图未生成"，查起来极难。P0–P5 现在**全部**自带临时目录
  # 隔离（各自 mkdtemp + CWB_* 环境变量），根本不需要这个清理，
  # 留着只有副作用。所以改成只警告、不动手。
  for p in storage/cwb.db storage/artifacts storage/provider_configs.json; do
    if [ -e "$p" ]; then
      echo "note: 生产数据仍在 $p（测试不会碰它，全部走临时目录）"
      break
    fi
  done
  echo "===== $t ====="
  OUT=$(.venv/bin/python -B backend/tests/test_$t.py 2>&1)
  echo "$OUT" | tail -6
  # 统一认两种统计行：「结果：N 通过 / M 失败」与「N 通过 / M 失败」。
  # 注意：数字可能出现在行首，所以不能用 `.*[^0-9]([0-9]+)` 这种要求"前面必须有字符"的写法。
  LINE=$(echo "$OUT" | grep -E "(^结果：)?[0-9]+ 通过 / [0-9]+ 失败" | tail -1)
  if [ -n "$LINE" ]; then
    P=$(echo "$LINE" | grep -oE "[0-9]+ 通过" | grep -oE "[0-9]+" | head -1)
    F=$(echo "$LINE" | grep -oE "[0-9]+ 失败" | grep -oE "[0-9]+" | head -1)
    TOTAL_PASS=$((TOTAL_PASS + P))
    TOTAL_FAIL=$((TOTAL_FAIL + F))
    if [ "$F" != "0" ]; then
      FAILED_STAGES+=("$t")
      echo "$OUT" | sed -n '/失败项：/,$p' | head -30
    fi
  else
    echo "!! 无法解析 $t 的统计行，可能异常退出"
    FAILED_STAGES+=("$t")
  fi
  echo
done

echo "=============================================================="
echo "全量回归合计：$TOTAL_PASS 通过 / $TOTAL_FAIL 失败"
if [ ${#FAILED_STAGES[@]} -ne 0 ]; then
  echo "有失败的阶段：${FAILED_STAGES[*]}"
  exit 1
fi
echo "全部阶段通过。"
