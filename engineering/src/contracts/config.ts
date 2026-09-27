/**
 * 配置契约（ConfigSpec Contract）
 *
 * 对应后端 app/contracts/config.py。
 * 详见 docs/development/core-contracts-design.md 第 6 章。
 *
 * 稳定性承诺：Stable v1.0.0，向后兼容扩展，breaking change 需新开 ADR。
 */

/** 支持的字段类型（与后端 VALID_FIELD_TYPES 对齐）。 */
export type ConfigFieldType = 'int' | 'float' | 'str' | 'bool' | 'list' | 'dict';

export const VALID_FIELD_TYPES: ConfigFieldType[] = [
  'int',
  'float',
  'str',
  'bool',
  'list',
  'dict',
];

/** 支持的超参搜索策略。 */
export type SweepKind = 'grid' | 'random' | 'bayesian';

export const VALID_SWEEP_KINDS: SweepKind[] = ['grid', 'random', 'bayesian'];

/** sweep 规格。 */
export interface SweepSpec {
  kind: SweepKind;
  /** 候选值列表（非空）。 */
  values: unknown[];
  /** bayesian 策略可选的优化方向。 */
  optimize?: 'minimize' | 'maximize';
  /** random/bayesian 的采样数。 */
  num_samples?: number;
}

/** 配置字段规格。 */
export interface ConfigField {
  /** 字段名（点分路径，如 "model.hidden_size"）。 */
  name: string;
  type: ConfigFieldType;
  /** 默认值（required=false 时使用）。 */
  default: unknown;
  /** 字段说明（用于自动生成文档）。 */
  description?: string;
  /** 是否必填（required=true 时 default 不生效）。 */
  required?: boolean;
  /** 枚举可选值列表。 */
  choices?: unknown[];
  /** 数值字段最小值（含）。 */
  min?: number;
  /** 数值字段最大值（含）。 */
  max?: number;
  /** 超参搜索规格。 */
  sweep?: SweepSpec;
}

/** 配置规格契约（对应一个 YAML 文件）。 */
export interface ConfigSpec {
  /** 规格 ID（如 "ltc_chatter"）。 */
  name: string;
  /** semver 版本号（如 "3.0" 或 "3.0.0"）。 */
  version: string;
  description: string;
  fields: ConfigField[];
  /** 父配置 spec 名（继承）。 */
  parent?: string;
  metadata?: Record<string, unknown>;
}

// 抽象接口

/**
 * 配置存储契约（前端通过 IPC 调用后端实现）。
 *
 * 负责：
 * - 注册/查询 ConfigSpec
 * - 加载 YAML 配置文件（应用继承/覆盖）
 * - 合并 spec 默认值 + YAML + overrides
 * - 展开超参搜索（grid/random/bayesian）
 */
export interface IConfigStore {
  /** 注册一个配置规格。 */
  register(spec: ConfigSpec): void | Promise<void>;

  /** 按名取规格，不存在抛 Error。 */
  get_spec(name: string): ConfigSpec | Promise<ConfigSpec>;

  /**
   * 加载 YAML 配置文件，应用继承/覆盖。
   *
   * @param path YAML 文件路径
   * @returns 合并后的配置字典（已应用 parent 继承 + overrides）
   */
  load_yaml(path: string): Record<string, unknown> | Promise<Record<string, unknown>>;

  /**
   * 合并 spec 默认值 + YAML + overrides，返回最终配置。
   *
   * @param spec_name ConfigSpec 名
   * @param overrides 顶层覆盖（最高优先级）
   * @returns 完整配置字典（已通过 spec.validate 校验）
   */
  resolve(
    spec_name: string,
    overrides?: Record<string, unknown>,
  ): Record<string, unknown> | Promise<Record<string, unknown>>;

  /**
   * 展开超参搜索，返回配置列表。
   *
   * @param spec_name ConfigSpec 名
   * @param sweep_config 字段名 → 候选值列表（或 sweep 规格 dict）
   * @returns 配置字典列表，每个元素是一个完整的实验配置
   */
  expand_sweep(
    spec_name: string,
    sweep_config: Record<string, unknown>,
  ): Record<string, unknown>[] | Promise<Record<string, unknown>[]>;
}

/**
 * 配置源契约（多源合并：env / yaml / db / user_input）。
 *
 * 多个 IConfigSource 按 priority() 升序合并，priority 越大优先级越高。
 */
export interface IConfigSource {
  /** 返回优先级（数字越大优先级越高）。 */
  priority(): number;
  /** 取值，不存在抛 Error。 */
  get(key: string): unknown;
  /** 返回此配置源所有可用的 key。 */
  keys(): string[];
}

export const CONTRACTS_CONFIG_VERSION = '1.0.0';
