import type {
  ProcessContent,
  ProcessRecord,
  ReviewEvidence,
  DrillingLimits,
} from "@/api/processKnowledge";

export interface ParameterRow {
  name: string;
  value: string;
  unit: string;
}
export interface ReviewForm {
  feature: string;
  process: string;
  tool: string;
  material: string;
  source: string;
  parameters: ParameterRow[];
  sourceUrl: string;
  documentHash: string;
  page: string;
  applicability: string;
  verificationMethod: string;
  unresolved: string;
  resolvedConfirmed: boolean;
  approvalConfirmed: boolean;
  note: string;
  hardnessMin: string;
  hardnessMax: string;
  hardnessMaxInclusive: string;
  diameterMin: string;
  diameterMax: string;
  depthRatio: string;
  coolants: string[];
  workholdings: string[];
}
export function newReviewForm(record?: ProcessRecord): ReviewForm {
  const evidence = record?.review.evidence?.source_url
    ? record.review.evidence
    : record?.candidate_context;
  return {
    feature: record?.feature ?? "",
    process: record?.process ?? "",
    tool: record?.tool ?? "",
    material: record?.material ?? "",
    source: record?.source ?? "unverified",
    parameters: record
      ? Object.entries(record.parameters).map(([name, value]) => ({
          name,
          value: String(value),
          unit: evidence?.parameter_units?.[name] ?? "",
        }))
      : [{ name: "", value: "", unit: "" }],
    sourceUrl: evidence?.source_url ?? "",
    documentHash: evidence?.document_sha256 ?? "",
    page: String(evidence?.page ?? ""),
    applicability: evidence?.applicability ?? "",
    verificationMethod: evidence?.verification_method ?? "",
    unresolved: evidence?.unresolved?.join("\n") ?? "",
    resolvedConfirmed: false,
    approvalConfirmed: false,
    note: "",
    hardnessMin: String(evidence?.constraints?.hardness_min_hrc ?? ""),
    hardnessMax: String(evidence?.constraints?.hardness_max_hrc ?? ""),
    hardnessMaxInclusive:
      evidence?.constraints?.hardness_max_inclusive === undefined
        ? ""
        : evidence.constraints.hardness_max_inclusive
          ? "inclusive"
          : "exclusive",
    diameterMin: String(evidence?.constraints?.diameter_min_mm ?? ""),
    diameterMax: String(evidence?.constraints?.diameter_max_mm ?? ""),
    depthRatio: String(evidence?.constraints?.max_depth_to_diameter ?? ""),
    coolants: [...(evidence?.constraints?.coolants ?? [])],
    workholdings: [...(evidence?.constraints?.workholdings ?? [])],
  };
}
export function formContent(
  form: ReviewForm,
  original?: ProcessRecord,
): ProcessContent {
  if (
    [form.feature, form.process, form.tool, form.material, form.source].some(
      (value) => !value.trim(),
    )
  )
    throw new Error("请填写特征、工艺、刀具、材料和来源。");
  const parameters: Record<string, number> = Object.create(null);
  if (!form.parameters.length) throw new Error("至少填写一项参数。");
  for (const row of form.parameters) {
    const name = row.name.trim();
    const value = Number(row.value);
    if (
      !name ||
      Object.prototype.hasOwnProperty.call(parameters, name) ||
      !row.value.trim() ||
      !Number.isFinite(value) ||
      value <= 0
    )
      throw new Error("参数名不能重复，参数值必须为有限正数。");
    parameters[name] = value;
  }
  const page = form.page.trim() ? Number(form.page) : undefined;
  if (page !== undefined && (!Number.isInteger(page) || page < 1))
    throw new Error("来源页码需为正整数，也可暂留空待补充。");
  return {
    feature: form.feature.trim(),
    process: form.process.trim(),
    tool: form.tool.trim(),
    material: form.material.trim(),
    source: form.source.trim(),
    parameters,
    confidence: original?.confidence ?? 0.5,
    chunk_ids: original?.chunk_ids ?? [],
    tags: original?.tags ?? [],
    candidate_context: {
      source_url: form.sourceUrl.trim(),
      document_sha256: form.documentHash.trim(),
      page,
      parameter_units: Object.fromEntries(
        form.parameters.map((row) => [row.name.trim(), row.unit.trim()]),
      ),
      applicability: form.applicability.trim(),
      verification_method: form.verificationMethod.trim(),
      unresolved: form.unresolved
        .split("\n")
        .map((value) => value.trim())
        .filter(Boolean),
      note: form.note.trim(),
      constraints: limitsFromForm(form),
    },
  };
}
export function approvalEvidence(
  form: ReviewForm,
  record: ProcessRecord,
): ReviewEvidence {
  if (!form.note.trim()) throw new Error("请填写审核说明。");
  if (
    !form.resolvedConfirmed ||
    !form.approvalConfirmed ||
    form.unresolved.trim()
  )
    throw new Error("仍有未解决事项，或尚未确认来源与适用条件。");
  let url: URL;
  try {
    url = new URL(form.sourceUrl);
  } catch {
    throw new Error("请填写有效的 HTTPS 来源地址。");
  }
  if (url.protocol !== "https:" || url.username || url.password)
    throw new Error("来源地址需要 HTTPS 且不能含登录凭据。");
  if (!/^[a-f0-9]{64}$/.test(form.documentHash.trim()))
    throw new Error("请填写来源文档的 64 位 SHA256 校验值。");
  const page = Number(form.page);
  if (!Number.isInteger(page) || page < 1)
    throw new Error("请填写有效的来源页码。");
  if (!form.applicability.trim() || !form.verificationMethod.trim())
    throw new Error("请填写适用条件和核验方法。");
  if (record.material === "general")
    throw new Error("请先修订为明确材料，再提交审核。");
  if (
    (["feature", "process", "tool", "material", "source"] as const).some(
      (key) => form[key].trim().toLowerCase() !== record[key],
    ) ||
    form.parameters.some(
      (row) => Number(row.value) !== record.parameters[row.name],
    )
  )
    throw new Error("内容已变更，请先保存修订后再审核。");
  const units: Record<string, string> = Object.create(null);
  for (const row of form.parameters) {
    if (!row.unit.trim()) throw new Error("每个参数都需要明确单位。");
    units[row.name] = row.unit.trim();
  }
  if (
    Object.keys(record.parameters).some((key) => !units[key]) ||
    Object.keys(units).length !== Object.keys(record.parameters).length
  )
    throw new Error("参数已变更，请先保存修订后再审核。");
  return {
    source_url: url.href,
    document_sha256: form.documentHash.trim(),
    page,
    material: record.material,
    tool: record.tool,
    parameter_units: units,
    applicability: form.applicability.trim(),
    verification_method: form.verificationMethod.trim(),
    unresolved: [],
    constraints: limitsFromForm(form, true) as DrillingLimits,
  };
}

export function limitsFromForm(
  form: ReviewForm,
  complete = false,
): Partial<DrillingLimits> {
  const limits: Partial<DrillingLimits> = { schema_version: 1 };
  const fields = {
    hardnessMin: "hardness_min_hrc",
    hardnessMax: "hardness_max_hrc",
    diameterMin: "diameter_min_mm",
    diameterMax: "diameter_max_mm",
    depthRatio: "max_depth_to_diameter",
  } as const;
  for (const [input, key] of Object.entries(fields) as [
    keyof typeof fields,
    (typeof fields)[keyof typeof fields],
  ][]) {
    if (!form[input].trim()) {
      if (complete)
        throw new Error("请完整填写钻孔适用范围：硬度、孔径与最大深径比。");
      continue;
    }
    const value = Number(form[input]);
    const hardness = input === "hardnessMin" || input === "hardnessMax";
    if (
      !Number.isFinite(value) ||
      (hardness ? value < 0 || value > 100 : value <= 0)
    )
      throw new Error(
        "适用范围需要有效数值；硬度为0至100 HRC，孔径与深径比为正数。",
      );
    limits[key] = value;
  }
  if (
    (limits.hardness_min_hrc !== undefined &&
      limits.hardness_max_hrc !== undefined &&
      limits.hardness_min_hrc > limits.hardness_max_hrc) ||
    (limits.diameter_min_mm !== undefined &&
      limits.diameter_max_mm !== undefined &&
      limits.diameter_min_mm > limits.diameter_max_mm)
  )
    throw new Error("适用范围的下界不能超过上界。");
  for (const key of ["coolants", "workholdings"] as const) {
    const allowed =
      key === "coolants"
        ? ["dry", "flood", "mist", "through_tool"]
        : ["vise", "chuck", "fixture", "collet"];
    if (form[key].some((value) => !allowed.includes(value)))
      throw new Error("请选择允许的冷却或装夹方式。");
    if (form[key].length) limits[key] = [...form[key]];
    else if (complete) throw new Error("请选择已核验的冷却与装夹方式。");
  }
  if (form.hardnessMaxInclusive) {
    if (!["inclusive", "exclusive"].includes(form.hardnessMaxInclusive))
      throw new Error("请选择硬度上界是否包含边界。");
    limits.hardness_max_inclusive = form.hardnessMaxInclusive === "inclusive";
  } else if (complete) throw new Error("请明确硬度上界是否包含边界。");
  if (
    complete &&
    limits.hardness_min_hrc === limits.hardness_max_hrc &&
    limits.hardness_max_inclusive === false
  )
    throw new Error("硬度范围为空，请核对上下界和包含关系。");
  return limits;
}

export function describeDrillingLimits(
  limits: Partial<DrillingLimits> | null | undefined,
): string {
  if (
    !limits ||
    !Array.isArray(limits.coolants) ||
    !Array.isArray(limits.workholdings)
  )
    return "适用范围不完整，需要修订并重新审核。";
  if (
    limits.schema_version !== 1 ||
    typeof limits.hardness_max_inclusive !== "boolean" ||
    [
      limits.hardness_min_hrc,
      limits.hardness_max_hrc,
      limits.diameter_min_mm,
      limits.diameter_max_mm,
      limits.max_depth_to_diameter,
    ].some((value) => typeof value !== "number" || !Number.isFinite(value))
  )
    return "适用范围不完整，需要修订并重新审核。";
  const coolants: Record<string, string> = {
    dry: "干式",
    flood: "浇注冷却",
    mist: "喷雾冷却",
    through_tool: "内冷",
  };
  const workholdings: Record<string, string> = {
    vise: "台钳",
    chuck: "卡盘",
    fixture: "专用夹具",
    collet: "夹头",
  };
  return `${limits.hardness_min_hrc} ≤ 硬度 ${limits.hardness_max_inclusive ? "≤" : "＜"} ${limits.hardness_max_hrc} HRC；孔径 ${limits.diameter_min_mm}–${limits.diameter_max_mm} mm（包含上下界）；最大深径比 ${limits.max_depth_to_diameter}；冷却：${limits.coolants.map((value) => coolants[value] ?? value).join("、")}；装夹：${limits.workholdings.map((value) => workholdings[value] ?? value).join("、")}。`;
}
