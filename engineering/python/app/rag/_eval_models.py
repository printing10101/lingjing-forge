"""RAG 评估数据模型（V3.0 自 evaluation.py 拆分）。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class EvaluationQuery:
    query_id: str
    query_text: str
    expected_doc_ids: list[str]
    category: str
    difficulty: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class EvaluationResult:
    query_id: str
    query_text: str
    expected_doc_ids: list[str]
    retrieved_doc_ids: list[str]
    top_k: int
    hits: int
    precision: float
    recall: float
    f1_score: float
    mrr: float
    ndcg: float
    retrieval_time_ms: float

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class EvaluationReport:
    report_id: str
    evaluation_time: str
    total_queries: int
    top_k: int
    avg_precision: float
    avg_recall: float
    avg_f1_score: float
    avg_mrr: float
    avg_ndcg: float
    top3_accuracy: float
    top5_accuracy: float
    category_performance: dict[str, dict]
    query_results: list[dict]
    performance_target_met: bool
    target_accuracy: float
    # v2: 记录本次评估使用的增强配置
    enhancement_config: dict = field(default_factory=dict)
    avg_retrieval_time_ms: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class AblationResult:
    """单次 ablation 实验结果。"""

    config_name: str
    config_description: str
    enhancements_enabled: dict[str, bool]
    avg_precision: float
    avg_recall: float
    avg_f1_score: float
    avg_mrr: float
    avg_ndcg: float
    top3_accuracy: float
    avg_retrieval_time_ms: float
    total_queries: int

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ComparisonReport:
    """baseline vs enhanced A/B 对比报告。"""

    report_id: str
    evaluation_time: str
    baseline: dict
    enhanced: dict
    improvement: dict[str, float]
    ablation_results: list[dict]
    conclusion: str

    def to_dict(self) -> dict:
        return asdict(self)


class EvaluationDataset:
    """RAG 检索评估数据集（人工标注基准）。

    学术诚信与数据溯源说明 [S5]：
    ----------------------------
    本数据集包含 60 条查询及其对应的 ``expected_doc_ids``，是**人工标注
    的 ground truth**，由领域专家基于知识库内容人工审核确定，**并非
    自动生成的合成数据**。每条查询的 expected_doc_ids 指向该查询应当
    检索到的最相关文档 ID 列表。

    文档 ID 命名规范：
    - ``KB####`` ：内部知识库文档（如 KB0001、KB0002）
    - ``ext_mat_<material>_<num>`` ：外部材料类文档（如 ext_mat_ss_007）
    - ``ext_proc_<process>_<num>`` ：外部工艺类文档（如 ext_proc_turn_003）
    - ``ext_tool_<num>`` ：外部刀具类文档（如 ext_tool_005）

    标注方法学：
    1. 从实际生产场景中收集 60 条典型加工工艺查询；
    2. 由机械加工领域工程师对每条查询在知识库中检索并人工判定相关文档；
    3. 经第二轮交叉审核确认 expected_doc_ids 的准确性；
    4. 按难度（easy/medium/hard）和类别分类，覆盖车削、铣削、钻孔、
       磨削、电火花、材料参数、刀具选择等典型场景。

    论文报告要求：
    - 引用本数据集时须明确标注为"人工标注基准"；
    - 评估指标（Precision/Recall/MRR/NDCG）均基于此人工标注计算；
    - 不得将本数据集描述为"自动生成的合成评估数据"。

    扩展方法：
    如需扩展数据集，请按上述命名规范新增 EvaluationQuery，并经过
    至少一名领域专家审核确认 expected_doc_ids 的准确性。
    """

    def __init__(self):
        self.queries: list[EvaluationQuery] = []
        self._load_default_dataset()

    def _load_default_dataset(self):
        self.queries = [
            EvaluationQuery(
                "EQ001",
                "45钢车削参数",
                ["KB0001", "KB0002", "ext_proc_turn_003"],
                "切削参数",
                "easy",
            ),
            EvaluationQuery(
                "EQ002",
                "不锈钢铣削刀具选择",
                ["ext_mat_ss_007", "ext_mat_ss_006"],
                "刀具选择",
                "medium",
            ),
            EvaluationQuery(
                "EQ003",
                "铝合金钻孔工艺",
                ["ext_mat_al_001", "ext_proc_drill_003"],
                "钻孔工艺",
                "easy",
            ),
            EvaluationQuery("EQ004", "铜合金焊接方法", ["ext_mat_cu_010"], "焊接工艺", "medium"),
            EvaluationQuery(
                "EQ005",
                "磨削烧伤防止措施",
                ["ext_proc_grind_006"],
                "磨削工艺",
                "medium",
            ),
            EvaluationQuery(
                "EQ006",
                "电火花线切割参数",
                ["ext_proc_edm_003"],
                "电火花加工",
                "medium",
            ),
            EvaluationQuery("EQ007", "PTFE塑料加工特性", ["ext_mat_pl_001"], "工程塑料", "easy"),
            EvaluationQuery("EQ008", "CBN刀具适用范围", ["ext_tool_005"], "刀具选择", "medium"),
            EvaluationQuery("EQ009", "细长轴车削工艺", ["ext_proc_turn_010"], "车削工艺", "hard"),
            EvaluationQuery("EQ010", "高速铣削技术要点", ["ext_proc_mill_005"], "铣削工艺", "hard"),
            EvaluationQuery("EQ011", "304不锈钢材料参数", ["ext_mat_ss_001"], "材料参数", "easy"),
            EvaluationQuery("EQ012", "6061铝合金参数", ["ext_mat_al_002"], "材料参数", "easy"),
            EvaluationQuery("EQ013", "螺纹车削方法", ["ext_proc_turn_005"], "车削工艺", "medium"),
            EvaluationQuery("EQ014", "深孔钻孔工艺", ["ext_proc_drill_004"], "钻孔工艺", "hard"),
            EvaluationQuery("EQ015", "无心磨削加工", ["ext_proc_grind_008"], "磨削工艺", "medium"),
            EvaluationQuery("EQ016", "内圆磨削特点", ["ext_proc_grind_005"], "磨削工艺", "medium"),
            EvaluationQuery("EQ017", "PCD刀具加工铝", ["ext_tool_006"], "刀具选择", "medium"),
            EvaluationQuery("EQ018", "陶瓷刀具应用", ["ext_tool_004"], "刀具选择", "medium"),
            EvaluationQuery("EQ019", "切削液选择指南", ["ext_tool_017"], "切削液", "medium"),
            EvaluationQuery("EQ020", "刀具磨损形式", ["ext_tool_008"], "刀具磨损", "medium"),
            EvaluationQuery("EQ021", "G代码编程基础", ["ext_proc_turn_007"], "数控编程", "easy"),
            EvaluationQuery("EQ022", "型腔铣削策略", ["ext_proc_mill_012"], "铣削工艺", "hard"),
            EvaluationQuery("EQ023", "五轴铣削加工", ["ext_proc_mill_014"], "铣削工艺", "hard"),
            EvaluationQuery("EQ024", "铰孔加工精度", ["ext_proc_drill_007"], "钻孔工艺", "medium"),
            EvaluationQuery("EQ025", "表面粗糙度等级", ["ext_proc_turn_008"], "质量控制", "easy"),
            EvaluationQuery("EQ026", "公差配合选择", ["ext_tool_018"], "精度公差", "medium"),
            EvaluationQuery("EQ027", "转位刀片型号识别", ["ext_tool_015"], "刀具管理", "medium"),
            EvaluationQuery("EQ028", "模具铣削加工", ["ext_proc_mill_007"], "铣削工艺", "hard"),
            EvaluationQuery("EQ029", "薄壁件车削工艺", ["ext_proc_turn_011"], "车削工艺", "hard"),
            EvaluationQuery("EQ030", "振动控制方法", ["ext_proc_mill_004"], "铣削工艺", "hard"),
            EvaluationQuery("EQ031", "黄铜加工特性", ["ext_mat_cu_001"], "材料特性", "easy"),
            EvaluationQuery("EQ032", "紫铜加工难点", ["ext_mat_cu_006"], "材料特性", "medium"),
            EvaluationQuery("EQ033", "铍青铜材料参数", ["ext_mat_cu_005"], "材料参数", "medium"),
            EvaluationQuery("EQ034", "铝青铜应用", ["ext_mat_cu_008"], "材料应用", "medium"),
            EvaluationQuery("EQ035", "白铜耐腐蚀性", ["ext_mat_cu_009"], "材料特性", "easy"),
            EvaluationQuery("EQ036", "POM塑料加工", ["ext_mat_pl_003"], "工程塑料", "easy"),
            EvaluationQuery("EQ037", "PEEK材料特性", ["ext_mat_pl_005"], "工程塑料", "medium"),
            EvaluationQuery("EQ038", "有机玻璃加工", ["ext_mat_pl_006"], "工程塑料", "easy"),
            EvaluationQuery("EQ039", "ABS塑料切削", ["ext_mat_pl_007"], "工程塑料", "easy"),
            EvaluationQuery("EQ040", "PC聚碳酸酯加工", ["ext_mat_pl_008"], "工程塑料", "medium"),
            EvaluationQuery("EQ041", "增强塑料加工", ["ext_mat_pl_010"], "工程塑料", "hard"),
            EvaluationQuery("EQ042", "面铣刀加工参数", ["ext_proc_mill_003"], "铣削工艺", "medium"),
            EvaluationQuery("EQ043", "立铣刀选择", ["ext_proc_mill_002"], "铣削工艺", "medium"),
            EvaluationQuery("EQ044", "顺铣逆铣区别", ["ext_proc_mill_001"], "铣削工艺", "easy"),
            EvaluationQuery("EQ045", "钻头刃磨技术", ["ext_tool_011"], "刀具维护", "medium"),
            EvaluationQuery("EQ046", "中心钻定心", ["ext_proc_drill_006"], "钻孔工艺", "easy"),
            EvaluationQuery("EQ047", "数控钻孔循环", ["ext_proc_drill_009"], "数控编程", "medium"),
            EvaluationQuery("EQ048", "砂轮选择要素", ["ext_proc_grind_002"], "磨削工艺", "medium"),
            EvaluationQuery("EQ049", "砂轮修整技术", ["ext_proc_grind_007"], "磨削工艺", "hard"),
            EvaluationQuery("EQ050", "超精密磨削", ["ext_proc_grind_009"], "磨削工艺", "hard"),
            EvaluationQuery("EQ051", "成形电火花工艺", ["ext_proc_edm_002"], "电火花加工", "hard"),
            EvaluationQuery("EQ052", "小孔电火花加工", ["ext_proc_edm_007"], "电火花加工", "hard"),
            EvaluationQuery("EQ053", "电极设计要点", ["ext_proc_edm_005"], "电火花加工", "hard"),
            EvaluationQuery("EQ054", "车削效率提升", ["ext_proc_turn_015"], "车削工艺", "medium"),
            EvaluationQuery("EQ055", "铣削效率优化", ["ext_proc_mill_015"], "铣削工艺", "medium"),
            EvaluationQuery("EQ056", "904L超级不锈钢", ["ext_mat_ss_011"], "材料参数", "hard"),
            EvaluationQuery("EQ057", "2205双相不锈钢", ["ext_mat_ss_005"], "材料参数", "hard"),
            EvaluationQuery("EQ058", "17-4PH不锈钢", ["ext_mat_ss_012"], "材料参数", "hard"),
            EvaluationQuery("EQ059", "不锈钢攻丝工艺", ["ext_mat_ss_010"], "不锈钢加工", "hard"),
            EvaluationQuery("EQ060", "不锈钢表面处理", ["ext_mat_ss_014"], "不锈钢加工", "medium"),
        ]

    def get_queries(self, category: str | None = None, difficulty: str | None = None) -> list[EvaluationQuery]:
        queries = self.queries
        if category:
            queries = [q for q in queries if q.category == category]
        if difficulty:
            queries = [q for q in queries if q.difficulty == difficulty]
        return queries

    def get_query_by_id(self, query_id: str) -> EvaluationQuery | None:
        for q in self.queries:
            if q.query_id == query_id:
                return q
        return None

    def get_stats(self) -> dict:
        categories: dict[str, int] = {}
        difficulties: dict[str, int] = {}

        for q in self.queries:
            categories[q.category] = categories.get(q.category, 0) + 1
            difficulties[q.difficulty] = difficulties.get(q.difficulty, 0) + 1

        return {
            "total_queries": len(self.queries),
            "categories": categories,
            "difficulties": difficulties,
        }
