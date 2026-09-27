const fs = require("fs");
const {
  Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell,
  AlignmentType, LevelFormat, HeadingLevel, BorderStyle, WidthType,
  ShadingType, PageBreak, Header, Footer, PageNumber, TabStopType, TabStopPosition,
} = require("docx");

// 中文字体
const CN = "宋体";   // 正文
const HEI = "黑体";  // 标题

const CONTENT_W = 9026; // A4 内容宽度 DXA (11906 - 1440*2)

// ---- 通用组件 ----
function p(text, opts = {}) {
  return new Paragraph({
    spacing: { after: opts.after != null ? opts.after : 120, before: opts.before || 0 },
    alignment: opts.align || AlignmentType.LEFT,
    children: [new TextRun({
      text,
      font: opts.font || CN,
      size: opts.size || 21,
      bold: opts.bold || false,
      color: opts.color || "000000",
    })],
  });
}

function rich(runs, opts = {}) {
  return new Paragraph({
    spacing: { after: opts.after != null ? opts.after : 120, before: opts.before || 0 },
    alignment: opts.align || AlignmentType.LEFT,
    children: runs,
  });
}

function h1(text) {
  return new Paragraph({ heading: HeadingLevel.HEADING_1, children: [new TextRun({ text, font: HEI })] });
}
function h2(text) {
  return new Paragraph({ heading: HeadingLevel.HEADING_2, children: [new TextRun({ text, font: HEI })] });
}

// 边框
const border = { style: BorderStyle.SINGLE, size: 4, color: "9AA7B5" };
const borders = { top: border, bottom: border, left: border, right: border };
const headerFill = { fill: "2E5C8A", type: ShadingType.CLEAR };
const zebraFill = { fill: "EEF3F8", type: ShadingType.CLEAR };

function cell(text, opts = {}) {
  const isHead = opts.head;
  return new TableCell({
    borders,
    width: { size: opts.w, type: WidthType.DXA },
    shading: isHead ? headerFill : (opts.fill || null),
    margins: { top: 60, bottom: 60, left: 100, right: 100 },
    verticalAlign: AlignmentType.CENTER,
    children: [new Paragraph({
      alignment: opts.align || (isHead ? AlignmentType.CENTER : AlignmentType.LEFT),
      children: [new TextRun({
        text: String(text),
        font: CN,
        size: opts.size || 18,
        bold: isHead || opts.bold || false,
        color: isHead ? "FFFFFF" : (opts.color || "000000"),
      })],
    })],
  });
}

function makeTable(widths, headerRow, dataRows, opts = {}) {
  const head = new TableRow({
    tableHeader: true,
    children: headerRow.map((t, i) => cell(t, { head: true, w: widths[i] })),
  });
  const rows = dataRows.map((r, ri) => new TableRow({
    children: r.map((t, i) => cell(t, { w: widths[i], fill: (opts.zebra && ri % 2 === 1) ? zebraFill : null })),
  }));
  return new Table({
    width: { size: widths.reduce((a, b) => a + b, 0), type: WidthType.DXA },
    columnWidths: widths,
    rows: [head, ...rows],
  });
}

// ===== 文档内容 =====
const children = [];

// 抬头信息行（公司 + 报告类型）
children.push(new Paragraph({
  alignment: AlignmentType.CENTER,
  spacing: { after: 60 },
  children: [new TextRun({ text: "灵境制造 · 数控车床加工工艺报告", font: HEI, size: 24, bold: true, color: "2E5C8A" })],
}));
children.push(new Paragraph({
  alignment: AlignmentType.CENTER,
  spacing: { after: 200 },
  border: { bottom: { style: BorderStyle.SINGLE, size: 12, color: "2E5C8A", space: 2 } },
  children: [new TextRun({ text: "PROCESS PLANNING REPORT", font: "Arial", size: 16, color: "7A8AA0" })],
}));

// 标题
children.push(new Paragraph({
  alignment: AlignmentType.CENTER,
  spacing: { after: 80 },
  children: [new TextRun({ text: "后端盖轴承钢套", font: HEI, size: 40, bold: true })],
}));
children.push(new Paragraph({
  alignment: AlignmentType.CENTER,
  spacing: { after: 240 },
  children: [new TextRun({ text: "图号 M0033  |  材料 45#钢（调质 20-24HRC）  |  订单 70,000 件  |  2026-08-01", font: CN, size: 20, color: "555555" })],
}));

// 1. 零件信息
children.push(h1("一、零件信息"));
children.push(makeTable(
  [2600, 3200, 1626, 1600],
  ["项目", "内容", "项目", "内容"],
  [
    ["零件名称", "后端盖轴承钢套", "图号", "M0033"],
    ["材料", "45#钢（调质 20-24HRC）", "订单数量", "70,000 件"],
    ["包工包料单价", "9.0 元/件", "未注公差", "GB/T 1804-M"],
    ["特殊公差要求", "GB1004-B（建议按 m 级）", "表面要求", "Ra3.2"],
  ],
  { zebra: true }
));
children.push(p(""));
children.push(h2("1.1 关键尺寸（图纸可辨识 + 工程假设）"));
children.push(makeTable(
  [3000, 4026, 2000],
  ["要素", "尺寸", "备注"],
  [
    ["外圆", "Ø76.0 × 80.0 mm", "外径"],
    ["内孔 1", "Ø46.0H8 × 26.0 mm", "Ra3.2"],
    ["内孔 2", "Ø26.0H8 × 54.0 mm", "Ra3.2"],
    ["倒角", "C0.5", "锐边倒钝"],
  ],
  { zebra: true }
));
children.push(rich([
  new TextRun({ text: "图纸辨识说明：", font: CN, size: 19, bold: true, color: "B00020" }),
  new TextRun({ text: "图纸左侧部分小尺寸（20.6 / 6.6 等）分辨率不足，已按套筒常规结构假设为 Ø46×26 + Ø26×54 两段内孔。若实际尺寸不同，可修改参数后重新生成。", font: CN, size: 19 }),
], { after: 60 }));

// 2. 工艺路线
children.push(h1("二、工艺路线（由灵境制造自动生成）"));
children.push(makeTable(
  [620, 2100, 1500, 1500, 700, 800, 1006],
  ["序号", "工序名称", "加工方法", "刀具", "表面", "公差", "预估工时(min)"],
  [
    ["01", "OP01-端面粗车", "粗车端面", "外圆车刀(粗)", "A", "IT10", "4.8"],
    ["02", "OP02-端面精车", "精车端面", "外圆车刀(精)", "A", "IT7", "3.6"],
    ["03", "OP03-外圆粗车", "粗车外圆", "外圆车刀(粗)", "A", "IT10", "4.8"],
    ["04", "OP04-外圆精车", "精车外圆", "外圆车刀(精)", "A", "IT7", "3.6"],
    ["05", "OP05-内孔1-粗镗-Ø46", "粗镗内孔", "镗刀", "A", "IT10", "4.0"],
    ["06", "OP06-内孔1-精镗-Ø46", "精镗内孔", "镗刀", "A", "IT7", "3.0"],
    ["07", "OP07-内孔2-粗镗-Ø26", "粗镗内孔", "镗刀", "A", "IT10", "4.0"],
    ["08", "OP08-内孔2-精镗-Ø26", "精镗内孔", "镗刀", "A", "IT7", "3.0"],
    ["09", "OP09-倒角C0.5", "倒角", "倒角刀", "A", "IT10", "2.0"],
  ],
  { zebra: true }
));
children.push(p(""));
children.push(h2("2.1 最快加工工艺流程说明（针对 70,000 件大批量）"));
const flow = [
  "下料：Ø78 圆钢按 81 mm 长度锯切（或棒料送料机直接送料）。",
  "调质：外协热处理 20-24HRC（不计入机加工时间）。",
  "装夹：三爪卡盘夹持毛坯，伸出 80 mm；若用棒料送料机则自动夹紧。",
  "T01 外圆粗车刀：车端面见平 → 粗车外圆至 Ø76.5（留 0.5 mm 精车余量）。",
  "T02 外圆精车刀：精车端面保证总长 80 → 精车外圆至 Ø76。",
  "T03 镗刀：钻/镗中心底孔 Ø20 → 粗镗 Ø46 台阶 → 精镗 Ø46 H8；粗镗 Ø26 台阶 → 精镗 Ø26 H8。",
  "T04 倒角刀：所有锐边倒钝 C0.5。",
  "换件：松开卡盘换件，循环下一工件。",
  "后处理：去毛刺、清洗、终检。",
];
const flowNum = [
  { reference: "flow", levels: [{ level: 0, format: LevelFormat.DECIMAL, text: "%1.", alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 720, hanging: 360 } } } }] },
];
flow.forEach((t) => children.push(new Paragraph({
  numbering: { reference: "flow", level: 0 },
  spacing: { after: 60 },
  children: [new TextRun({ text: t, font: CN, size: 20 })],
})));

// 3. 真实切削时间
children.push(h1("三、真实切削时间测算"));
children.push(makeTable(
  [1720, 700, 700, 760, 800, 720, 720, 820, 1086],
  ["工序", "直径(mm)", "行程(mm)", "转速(rpm)", "进给(mm/rev)", "切深(mm)", "余量(mm)", "走刀", "切削时间(min)"],
  [
    ["OP01-端面粗车", "76.0", "39.0", "460", "0.200", "2.00", "2.50", "2", "0.85"],
    ["OP02-端面精车", "76.0", "39.0", "544", "0.080", "0.30", "0.30", "1", "0.90"],
    ["OP03-外圆粗车", "76.0", "80.0", "460", "0.200", "2.00", "1.00", "1", "0.87"],
    ["OP04-外圆精车", "76.0", "80.0", "544", "0.080", "0.30", "0.30", "1", "1.84"],
    ["OP05-内孔1-粗镗-Ø46", "46.0", "26.0", "761", "0.200", "2.00", "13.00", "7", "1.20"],
    ["OP06-内孔1-精镗-Ø46", "46.0", "26.0", "899", "0.080", "0.30", "0.30", "1", "0.36"],
    ["OP07-内孔2-粗镗-Ø26", "26.0", "54.0", "1346", "0.200", "2.00", "3.00", "2", "0.40"],
    ["OP08-内孔2-精镗-Ø26", "26.0", "54.0", "1591", "0.080", "0.30", "0.30", "1", "0.42"],
    ["OP09-倒角C0.5", "76.0", "0.5", "544", "0.080", "0.30", "0.30", "1", "0.01"],
  ],
  { zebra: true }
));
children.push(rich([
  new TextRun({ text: "切削时间合计：", font: CN, size: 21, bold: true }),
  new TextRun({ text: "6.85 min    ", font: CN, size: 21 }),
  new TextRun({ text: "换刀/刀补时间合计：", font: CN, size: 21, bold: true }),
  new TextRun({ text: "3.00 min", font: CN, size: 21 }),
], { after: 60 }));

// 4. 辅助时间
children.push(h1("四、辅助时间与单件总时间"));
children.push(makeTable(
  [4600, 4426],
  ["辅助时间项", "数值"],
  [
    ["夹准时间（装夹 + 找正 + 关防护门）", "2.0 min/件"],
    ["换件时间（松卡盘、取件、放新毛坯）", "1.0 min/件"],
    ["批量准备分摊（对刀、程序调用、首检）", "0.015 min/件"],
  ],
  { zebra: true }
));
children.push(rich([
  new TextRun({ text: "单件总时间：", font: CN, size: 24, bold: true, color: "B00020" }),
  new TextRun({ text: "12.87 min", font: CN, size: 24, bold: true, color: "B00020" }),
], { before: 80, after: 60 }));

// 5. 日产量
children.push(h1("五、日产量与总工期"));
children.push(makeTable(
  [3000, 2000, 2000, 2026],
  ["参数", "数值", "参数", "数值"],
  [
    ["班制", "8 h/班", "设备利用率", "85%"],
    ["每班可用加工时间", "408 min", "日产量(单班)", "约 31 件/天"],
    ["70,000 件总机加工时间", "约 15009 h", "70,000 件总工期", "2259 个工作日"],
  ],
  { zebra: true }
));
children.push(p(""));
children.push(h2("5.1 批量生产优化方案"));
children.push(makeTable(
  [3400, 1300, 1100, 1626, 1600],
  ["场景", "换件时间(min)", "利用率", "日产量(件/天)", "70,000件工期(天)"],
  [
    ["自动棒料送料机 + 三爪卡盘", "0.3", "85%", "33", "2122"],
    ["双主轴数控车床", "0.2", "90%", "35", "2000"],
    ["双机并行（两台数控车）", "1.0", "85%", "62", "1130"],
  ],
  { zebra: true }
));

// 6. 毛坯
children.push(h1("六、毛坯与下料建议"));
children.push(makeTable(
  [3600, 5426],
  ["项目", "规格"],
  [
    ["毛坯规格", "热轧 45# 圆钢 Ø78 × 81 mm"],
    ["棒料长度", "6000 mm（标准 6m）"],
    ["每棒可切件数", "约 74 件"],
    ["每棒料头损耗", "约 56 mm（含 50 mm 两端锯口；净余料约 6 mm）"],
  ],
  { zebra: true }
));
children.push(rich([
  new TextRun({ text: "按舅舅要求：", font: CN, size: 20, bold: true }),
  new TextRun({ text: "角料（料头）直径与毛坯一致 Ø78 mm；通过 Ø78×81 mm 下料，6m 棒料可切 74 件，净余料仅 6 mm，满足料头 ≤60 mm 的控制要求。", font: CN, size: 20 }),
], { after: 60 }));

// 7. 成本
children.push(h1("七、成本测算（敏感性分析）"));
children.push(makeTable(
  [4200, 4826],
  ["项目", "数值"],
  [
    ["单件毛坯重量", "约 3.04 kg"],
    ["材料单价（参考）", "3.5 元/kg"],
    ["单件材料成本", "约 10.63 元/件"],
    ["包工包料单价", "9.0 元/件"],
    ["单件毛利（未计刀具/电费/人工/场地）", "约 -1.63 元/件"],
    ["70,000 件合同总额", "630,000 元"],
    ["70,000 件材料总成本（参考）", "约 744,389 元"],
    ["剩余空间（人工/刀具/能耗/利润）", "约 -114,389 元"],
    ["材料盈亏平衡价", "≤ 2.96 元/kg（无加工利润）"],
  ],
  { zebra: true }
));
children.push(rich([
  new TextRun({ text: "重要提示：", font: CN, size: 20, bold: true, color: "B00020" }),
  new TextRun({ text: "9.0 元/件包工包料价格偏低，单件毛利约 -1.63 元。能否盈利取决于 45# 钢批量采购价、刀具寿命、设备利用率。建议先小批量试制验证真实刀耗和工时；若材料价无法压至 2.96 元/kg 以下，需重新评估报价。", font: CN, size: 20 }),
], { after: 60 }));

// 8. 刀具转速
children.push(h1("八、刀具与转速建议"));
children.push(makeTable(
  [4600, 4426],
  ["工序", "转速 / 进给"],
  [
    ["OP01-端面粗车", "S460 rpm，F0.200 mm/rev"],
    ["OP02-端面精车", "S544 rpm，F0.080 mm/rev"],
    ["OP03-外圆粗车", "S460 rpm，F0.200 mm/rev"],
    ["OP04-外圆精车", "S544 rpm，F0.080 mm/rev"],
    ["OP05-内孔1-粗镗-Ø46", "S761 rpm，F0.200 mm/rev"],
    ["OP06-内孔1-精镗-Ø46", "S899 rpm，F0.080 mm/rev"],
    ["OP07-内孔2-粗镗-Ø26", "S1346 rpm，F0.200 mm/rev"],
    ["OP08-内孔2-精镗-Ø26", "S1591 rpm，F0.080 mm/rev"],
    ["OP09-倒角C0.5", "S544 rpm，F0.080 mm/rev"],
  ],
  { zebra: true }
));

// 9. 风险
children.push(h1("九、风险提示与假设说明"));
const risks = [
  "图纸尺寸不确定性：图纸左侧部分小尺寸（20.6 / 6.6 等）分辨率不足，内孔结构按 Ø46×26 + Ø26×54 假设。实际投产前请用高清图纸或 STEP 模型复核。",
  "预钻孔假设：内孔镗削按已预钻 Ø20 mm 底孔计算。若毛坯中心未预钻孔，需增加中心钻 + 钻孔工序（约 +0.5~1.5 min/件）。",
  "调质处理：20-24HRC 建议外协，不计入机加工时间；调质后硬度会显著影响刀具寿命和切削参数。",
  "G 代码说明：本报告 G 代码由通用后处理器生成，实际使用前需按具体机床（Fanuc/Siemens/广数等）和车削循环（G71/G72/G70 等）调整。",
  "产能瓶颈：单台单班约 31 件/天，70000 件约需 2259 个工作日。大批量交付必须采用自动送料、双主轴或多机并行。",
];
const riskNum = [
  { reference: "risks", levels: [{ level: 0, format: LevelFormat.DECIMAL, text: "%1.", alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 720, hanging: 360 } } } }] },
];
risks.forEach((t) => children.push(new Paragraph({
  numbering: { reference: "risks", level: 0 },
  spacing: { after: 60 },
  children: [new TextRun({ text: t, font: CN, size: 20 })],
})));

// 附录 G代码
children.push(new Paragraph({ children: [new PageBreak()] }));
children.push(h1("附录：生成的数控程序（节选，Fanuc 0i-MF）"));
const gcode = [
  "%",
  "O1001 (PROGRAM 1001 - 2026-08-01)",
  "(POST: Fanuc 0i-MF)",
  "G21 G17 G40 G49 G80 G90 G94",
  "G00 G91 G28 Z0.",
  "G00 G91 G28 X0. Y0.",
  "G00 G90 G54 X0. Y0.",
  "G00 G43 Z80.000 H00",
  "M03 S1000",
  "M08",
  "",
  "; 材料: 45#钢",
  "; 工序数: 9 | 装夹次数: 1",
  "; 控制器: fanuc_0i | 生成日期: 2026-08-01",
  "",
  "; 安全设置",
  "G17 G21 G40 G49 G80 G90",
  "G00 Z80.000",
  "; 启用AI高精度轮廓控制",
  "G05.1 Q1",
  "; ==================================================",
  "; 刀具清单汇总表 (TOOL LIST SUMMARY)",
  "; ==================================================",
  "; T01 | 外圆车刀(粗) | 粗车外圆/粗车端面 | 2次",
  "; T02 | 外圆车刀(精) | 精车外圆/精车端面 | 2次",
  "; T03 | 镗刀       | 粗镗内孔/精镗内孔 | 4次",
  "; T04 | 倒角刀     | 倒角           | 1次",
  "; 总计: 4 把刀具",
  "; ==================================================",
  "",
  "; ---- OP01 OP01-端面粗车 - 粗车端面 ----",
  "G00 G91 G28 Z0.",
  "T01 M06",
  "G00 G90 G54 X0. Y0.",
  "G43 Z80.000 H01",
  "S1500 M03",
  "; 启用刀具半径补偿: G41",
  "G41 D10",
  "G01 X50.000 Z-20.000 F0.17",
  "G40",
  "G00 Z80.000",
  "M09",
];
gcode.forEach((line) => children.push(new Paragraph({
  spacing: { after: 0, line: 240 },
  children: [new TextRun({ text: line || " ", font: "Consolas", size: 17, color: "1A1A1A" })],
})));

// ===== 构建文档 =====
const doc = new Document({
  numbering: { config: [...flowNum, ...riskNum] },
  styles: {
    default: { document: { run: { font: CN, size: 21 }, paragraph: { spacing: { line: 276 } } } },
    paragraphStyles: [
      { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 30, bold: true, font: HEI, color: "1F3D5C" },
        paragraph: { spacing: { before: 280, after: 160 }, outlineLevel: 0,
          border: { bottom: { style: BorderStyle.SINGLE, size: 6, color: "2E5C8A", space: 4 } } } },
      { id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 24, bold: true, font: HEI, color: "2E5C8A" },
        paragraph: { spacing: { before: 180, after: 100 }, outlineLevel: 1 } },
    ],
  },
  sections: [{
    properties: {
      page: {
        size: { width: 11906, height: 16838 },
        margin: { top: 1440, right: 1440, bottom: 1440, left: 1440 },
      },
    },
    headers: {
      default: new Header({ children: [new Paragraph({
        alignment: AlignmentType.RIGHT,
        border: { bottom: { style: BorderStyle.SINGLE, size: 4, color: "B0B0B0", space: 2 } },
        children: [new TextRun({ text: "灵境制造 · 后端盖轴承钢套 车床加工工艺报告  |  图号 M0033", font: CN, size: 16, color: "888888" })],
      })] }),
    },
    footers: {
      default: new Footer({ children: [new Paragraph({
        alignment: AlignmentType.CENTER,
        children: [
          new TextRun({ text: "第 ", font: CN, size: 16, color: "888888" }),
          new TextRun({ children: [PageNumber.CURRENT], font: CN, size: 16, color: "888888" }),
          new TextRun({ text: " 页 / 共 ", font: CN, size: 16, color: "888888" }),
          new TextRun({ children: [PageNumber.TOTAL_PAGES], font: CN, size: 16, color: "888888" }),
          new TextRun({ text: " 页", font: CN, size: 16, color: "888888" }),
        ],
      })] }),
    },
    children,
  }],
});

const out = "C:\\Users\\Lenovo\\Desktop\\灵境制造（上线版）\\output\\后端盖轴承钢套_车床加工工艺报告.docx";
Packer.toBuffer(doc).then((buffer) => {
  fs.writeFileSync(out, buffer);
  console.log("生成成功:", out, "大小:", buffer.length, "字节");
});
