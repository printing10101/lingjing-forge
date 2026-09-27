# 后处理器验证矩阵

> 定位：对外承诺边界与对内质量台账。回答「每种控制器的 NC 输出，我们到底验证到什么程度」。
> 维护约定：验证状态变化时更新本文件并登记证据（测试文件 / 编程站报告 / 试切记录）。
> 创建：2026-09（仿真强制闭环 + 手动锚定合规层批次）

---

## 1. 验证层级定义

| 层级 | 名称 | 含义 | 证据形态 |
|---|---|---|---|
| L1 | **语法合规验证** | 独立校验器（不复用生成器代码）按控制器方言规则判定输出语法合规 | `tests/regression/test_postprocessor_manual_compliance.py` |
| L2 | **黄金回归** | 输出与 golden 基线逐字节一致，防重构漂移 | `tests/regression/test_postprocessor_golden.py` |
| L3 | **编程站验证** | 控制器厂商编程站/仿真器（SinuTrain、Heidenhain 编程站、FANUC NCGuide）加载并运行通过 | 编程站会话记录 |
| L4 | **实机试切** | 真实机床上试切，单段/空运行 + 实切验证 | 试切记录（视频/首件） |

> 当前无自有实机。L3/L4 通过「借」（工程训练中心/实训车间）、「租」（外发试切件）、
> 「买二手教学机」（GSK/HNC 教学机直接点亮对应机型）获取，见优化升级路线图 A 线。

---

## 2. 机型 × 验证状态（2026-09）

| 控制器 | 标识符 | L1 语法 | L2 黄金 | L3 编程站 | L4 实机 | 备注 |
|---|---|---|---|---|---|---|
| Fanuc 0i-MF | `fanuc_0i` | strict | 标准+扩展 | | | 国内主流三轴铣，优先争取实机 |
| Siemens 840D | `siemens_840d` | strict | 标准+扩展 | SinuTrain | | |
| Siemens 840D（声明式） | `siemens_840d_declared` | strict | 标准+扩展 | | | 2026-09 修复 hooks 加载后激活 |
| Heidenhain TNC | `heidenhain_tnc` | strict | 标准+扩展 | 编程站 | | 2026-09 修复 G00/G01 泄漏 |
| Heidenhain TNC640（声明式） | `heidenhain_tnc640_declared` | strict | 标准+扩展 | | | 同上 |
| Fagor 8055 | `fagor_8055` | strict | 标准+扩展 | | | |
| GSK 980/25i | `gsk_980_25i` | strict | 标准+扩展 | | | **二手教学机优先候选** |
| 华中 HNC-848/22 | `hnc_848_22` | strict | 标准+扩展 | | | **二手教学机优先候选** |
| KND 1000/2000/3000 | `knd_1000_2000_3000` | strict | 标准+扩展 | | | |
| Mitsubishi M70/M80 | `mitsubishi_m70_m80` | strict | 标准+扩展 | | | |
| xMachine XM100 | `xmachine_xm100` | strict | 标准+扩展 | | | 自研桌面五轴，M101/M201 为厂商自定义 |

扩展序列（`*_extended.nc`）只做 structural 级合规（结构 + 词法），不做型号白名单判定：
扩展序列是**能力探针**（攻丝/镗孔/五轴/RTCP/探针等），可能包含超出该型号手册的
基类兜底行，型号级复核归属 L3/L4。

---

## 3. 存疑项登记（待编程站/手册复核）

以下用法在 L1 白名单中放行或仅登记，**不构成对外承诺**，L3/L4 时优先核实：

| 控制器 | 存疑用法 | 现状 | 疑点 |
|---|---|---|---|
| HNC-848/22 | `G91 G74 Z0.` 作参考点返回 | 内置方言一贯输出 | 华中手册中 G74 为攻丝循环语义，参考点返回惯例是 G28 |
| Fagor 8055 | `G75 X0. Y0. Z0.` 作零点返回 | 内置方言一贯输出 | 需对照 8055 手册确认 G75 语义 |
| Heidenhain TNC | 螺纹车削循环中 `S1000 M03` 行 | `_heidenhain_cycles_mixin.py` | 对话式模式中 S 字通常仅出现在 TOOL CALL |
| 全部（扩展序列） | 基类兜底行（如 G84/G86 ISO 循环） | 能力探针 | 逐型号核实哪些固定循环真实存在 |

---

## 4. 本轮（2026-09）合规层发现并修复的缺陷

合规校验层设计出来第一件事就是抓 bug，以下均为真实缺陷（非理论问题）：

1. **Heidenhain 会话式程序泄漏 G00/G01 行**（严重）：内置
   `HeidenhainPostProcessor` 未覆写 `format_rapid_move` / `format_linear_move`，
   Fanuc 风格行混入 BEGIN PGM 对话式程序——真机会拒收。已补
   `L  X+.. Y+.. Z+.. R0 FMAX/F..` 覆写（`_heidenhain_core_mixin.py`）。
2. **两个 hooks 声明方言静默加载失败**（严重）：`heidenhain_tnc640_declared` /
   `siemens_840d_declared` 的 hooks 为列表格式（Phase E），`declaration.py` 只支持
   单字符串——两个方言从未注册成功，此前测试全绿是假象。已支持两种格式 +
   多 hooks 类合并 + 辅助方法注入（`declaration.py` / `compiler.py` / `registry.py`）。
3. **4 个国产方言注释嵌套括号**（中）：GSK/HNC/Fagor/KND 的 CONTROLLER_NAME
   含括号，插入 `( ... )` 注释后内层 `)` 提前终止注释，剩余文本被当作代码。
   已加 `_paren_comment` 净化（`_format_mixin.py`），镜像模板同步。
4. **Siemens 声明方言 PATH 行格式错误**（轻）：`:PATH=/_N_MPF_DIR` 应为注释
   `;$PATH=/_N_MPF_DIR`。已修（`postprocessor-plugins/siemens_840d/hooks.py`）。
5. **Heidenhain RTCP 输出 Fanuc 指令**（中）：`format_rtcp_on/off` 落到基类
   G43.4/G49 实现。已覆盖为 M128/M129（`_heidenhain_core_mixin.py`）。

> 教训：golden 是「自生成自比对」，会把生成器错误同步固化成基线；
> 没有 L1 这一层独立校验，以上缺陷全部无法被发现。

---

## 5. 框架使用

### 黄金文件再生

```bash
unset PYTHONPATH
UPDATE_GOLDEN=1 python -m pytest tests/regression/test_postprocessor_golden.py
# 审阅 git diff 黄金文件变更后提交；不可无审阅直接再生
```

### 合规校验器（独立于生成器）

```python
import sys; sys.path.insert(0, "engineering/python/tests")
from utils.nc_dialect_checker import NcDialectChecker

issues = NcDialectChecker("fanuc_0i").check(nc_text, tier="strict")   # 或 "structural"
# issues 为空 = 合规；非空列出 [rule] 行号 + 说明
```

- 新增控制器：在 `_PROFILES` 登记 profile（family + 白名单 + 结构形式），
  并生成对应 golden 基线。
- 白名单依据须为厂商公开手册/编程约定；型号个别差异先进 §3 存疑项，不直接放行。
