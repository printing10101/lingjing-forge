"""仓内 NC 语料卫生检查：`data/outputs/` 里不许存在零切削空壳。

背景（2026-10-07 全库定级实测）：归档在
`docs/archive/zero_cutting_evidence_20260618/` 的旧语料 160 个 `.nc`
（8 控制器 × 20 fixture）**一条切削指令都没有**，只有程序头 + `M03/M08` + 程序尾，
而同目录的 `e2e_v2_summary.json` 给每个案例都记了 `gcode_ok: true`。
那是阶段 6 的 `_run_gcode` 还只写 header/footer 冒烟占位时期的产物——
当时「端到端成功」只等于「没抛异常」。生成脚本后来被删/改名，
语料就以"看起来通过"的形态一直躺在仓库里，谁拿它当 E2E 证据谁就被骗。

本文件把这条教训变成常驻门禁：仓内每一个提交进来的 `.nc` 都必须含切削运动。
判定按方言分支（Heidenhain 的 `L ... F137.5`、Siemens 的 `CYCLE83` 都算），
避免用单一正则把正确方言误判成空壳——那会造成另一种假信号。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# tests/unit → tests → python → engineering → 仓库根
REPO_ROOT = Path(__file__).resolve().parents[4]
OUTPUT_ROOT = REPO_ROOT / "data" / "outputs"

# Fanuc 族 / Siemens / 国产控制器：G01/G02/G03 带轴地址
_G_FAMILY_CUT = re.compile(r"^\s*G0?[123]\s+[XYZ]", re.M)
# Heidenhain：`L  X+95.000 Y+5.000 Z+48.000 R0 F137.500`（快移同为 L 但写作 FMAX）
_HEIDEN_CUT = re.compile(r"^\s*(?:\d+\s+)?(?:L|CC|CR)\s[^\n]*\bF\d", re.M)


def cutting_moves_in(text: str) -> int:
    """按方言口径统计切削运动行数（Fanuc 族与 Heidenhain 都能正确计数）。"""
    return len(_G_FAMILY_CUT.findall(text)) + len(_HEIDEN_CUT.findall(text))


def find_empty_shells(root: Path) -> list[Path]:
    """返回根目录下所有零切削的 .nc 文件。"""
    offenders: list[Path] = []
    for nc in sorted(root.rglob("*.nc")):
        try:
            text = nc.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if cutting_moves_in(text) == 0:
            offenders.append(nc)
    return offenders


class TestOutputCorpusHygiene:
    @pytest.mark.unit
    def test_no_zero_cutting_nc_committed_under_data_outputs(self):
        if not OUTPUT_ROOT.exists():
            pytest.skip(f"{OUTPUT_ROOT} 不存在（尚未生成语料）")
        files = list(OUTPUT_ROOT.rglob("*.nc"))
        if not files:
            pytest.skip("data/outputs 下没有 .nc（用 scripts/regenerate_e2e_nc_corpus.py 生成）")

        offenders = find_empty_shells(OUTPUT_ROOT)
        assert not offenders, (
            f"data/outputs 下存在 {len(offenders)} 个零切削空壳 NC："
            f"{[str(p.relative_to(REPO_ROOT)) for p in offenders[:5]]}"
            "——空壳不能当端到端证据；请用 scripts/regenerate_e2e_nc_corpus.py 重新生成"
        )

    @pytest.mark.unit
    def test_corpus_has_summary_with_per_file_metrics(self):
        """语料必须带清单，且清单里每个 gcode_ok=true 都得有切削数（旧 summary 的病）。"""
        import json

        summary = OUTPUT_ROOT / "e2e_v2" / "e2e_v2_summary.json"
        if not summary.exists():
            pytest.skip("无 e2e_v2_summary.json")
        data = json.loads(summary.read_text(encoding="utf-8"))
        bad: list[str] = []
        for fixture_name, entry in data.get("fixtures", {}).items():
            for controller, r in entry.get("results_by_controller", {}).items():
                if r.get("gcode_ok") and not r.get("cutting_moves"):
                    bad.append(f"{controller}/{fixture_name}")
        assert not bad, f"summary 把零切削文件记成 gcode_ok=true: {bad[:5]}"

    @pytest.mark.unit
    def test_checker_itself_catches_an_empty_shell(self, tmp_path: Path):
        """自证：判据真有牙齿——手写一个只有头尾的空壳必须被抓出来。"""
        shell = tmp_path / "empty.nc"
        shell.write_text(
            "%\nO0001 (POST: Fanuc 0i-MF)\nG21 G17 G40 G49 G80 G90\nM03 S8000\nM08\nM09\nM05\nM30\n%\n",
            encoding="utf-8",
        )
        real = tmp_path / "real.nc"
        real.write_text("%\nG00 X0 Y0 Z80.\nG01 X95. Y5. Z48. F275.\nM30\n%\n", encoding="utf-8")
        heiden = tmp_path / "heiden.nc"
        heiden.write_text("%\nL  X+95.000 Y+5.000 Z+48.000 R0 F137.500\nM30\n%\n", encoding="utf-8")

        assert cutting_moves_in(shell.read_text(encoding="utf-8")) == 0
        assert find_empty_shells(tmp_path) == [shell]
        assert cutting_moves_in(real.read_text(encoding="utf-8")) == 1
        # Heidenhain 的 L 行不能被误判成空壳（否则门禁自己就是假信号）
        assert cutting_moves_in(heiden.read_text(encoding="utf-8")) == 1
