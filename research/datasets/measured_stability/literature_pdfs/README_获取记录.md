# 文献 PDF 获取记录（2026-09-16 凌晨，自动获取）

## 已获取（4 篇，全部为合法来源：作者自存档 / 机构仓库）

| 文件 | 论文 | 来源 | 说明 |
|---|---|---|---|
| `Insperger_2003_Part1_analytical.pdf` (708KB, 17页) | Insperger, Mann, Stépán, Bayly. *Stability of up-milling and down-milling, Part 1: Alternative analytical methods*. IJMTM 43(1):25-34. DOI: 10.1016/S0890-6955(02)00159-1 | 作者主页自存档 `mm.bme.hu/~insperger/j2003_IJMTM_1.pdf`（经 MTMT 官方记录确认） | 扫描/CID 字体版：**pypdf 文本抽取乱码，但图形数字化只用图像，无影响** |
| `Insperger_2003_Part2_experimental.pdf` (612KB, 15页) | Mann, Insperger, Bayly, Stépán. *Part 2: Experimental verification*. IJMTM 43(1):35-40. DOI: 10.1016/S0890-6955(02)00160-6 | 同上（`j2003_IJMTM_2.pdf`） | **本篇是实验验证篇（含实测稳定/颤振点），优先提取** |
| `Faassen_2003_high_speed_milling.pdf` (206KB, 10页) | Faassen, van de Wouw, Oosterling, Nijmeijer. *Prediction of regenerative chatter by modelling and analysis of high-speed milling*. IJMTM 43(14):1437-1446. DOI: 10.1016/S0890-6955(03)00171-8 | 合作者 van de Wouw 的 TU/e 个人页 `vandewouw.dc.tue.nl/IJMTM2003.pdf` | 出版商排版版，文本完美可抽取 |
| `Gradisek_2005_stability_prediction.pdf` (852KB, 13页) | Gradišek, Kalveram, Insperger, Weinert, Stépán, Govekar, Grabec. *On stability prediction for milling*. IJMTM 45(7-8):769-781. DOI: 10.1016/j.ijmachtools.2004.11.015 | 作者主页自存档 `mm.bme.hu/~insperger/j2005_IJMTM.pdf`（经 MTMT 确认） | 更正清单错误：正确卷期为 **45(7-8)，pp.769-781**（清单写的 45(12-13) 有误）；文本可抽取 |

## 未获取（2 项，付费墙，需校园网人工下载）

| 论文 | DOI | 备注 |
|---|---|---|
| Altintas & Budak 1995, *Analytical prediction of stability lobes in milling*, CIRP Annals 44(1):357-362 | 10.1016/S0007-8506(07)62342-7 | ScienceDirect 付费墙 + 403；academia.edu 需登录。**注意：该文的"实验验证"是引用他人发表数据（Opitz 1968 / Minis 1993 / Smith & Tlusty 1990），自身无新实验——提取点前先核实清单预期** |
| Budak & Altintas 1998 Part I/II, ASME J. Dyn. Sys. Meas. Control 120(1):22-30 / 31-36 | 10.1115/1.2801317 / 10.1115/1.2801318 | ASME 付费墙；ResearchGate 有 PDF 但脚本无法访问（需登录）。**Part II 含真实实验（2-DOF 铣削系统），是提取目标** |

## 检索范围声明

- OpenAlex API（6/6 DOI 全部核实，含被引数：Altintas95=1920 引，Insperger03=300，Faassen03=301，Gradišek05=213）
- Semantic Scholar API（限速 429 未成）
- Unpaywall（未触发：OpenAlex 已给出 OA 状态）
- WebSearch × 5 轮（命中 Insperger/Gradišek 自存档与 Faassen 自存档）
- **未用任何影子库（Sci-Hub/LibGen 等），只走作者自存档与机构仓库**

## 数字化建议顺序

1. **Insperger Part 2**（实验验证篇，预期 20-30 点，含 up/down-milling 对比）
2. **Faassen 2003**（10-20 点，高速铣 + 主轴转速相关机床动力学——对"参数校准是第一主导因素"是天然的第二机床验证）
3. **Gradišek 2005**（10-20 点，ZOA vs SD 对比 + 实验）
4. 校园网补：Budak 1998 Part II → Altintas 1995（如核实确有可用实测点）
