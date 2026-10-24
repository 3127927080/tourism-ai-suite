"""测试集读取与校验。

数据集是 CSV，一行一条样本。列含义：

    case_id              样本编号，唯一
    scene                场景标签（policy / knowledge_missing / order / trip / handoff / product ...）
    question             用户本轮问题
    ground_truth         参考回答，RAGAS 的 reference；为空则跳过参考类指标
    expected_reply_type  期望的兜底回复类型（ANSWER / CLARIFY / DECLINE / REQUEST_HANDOFF）
    expected_action_code 期望的服务端页面动作（CANCEL_BOOKING / EDIT_TRAVELER / APPLY_REFUND ...）
    expected_tools       期望必须调用的工具，用 | 分隔；缺任一项即规则层失败
    gold_knowledge_ids   期望召回的知识编号，用 | 分隔；用于 hit_rate / mrr
    forbidden_phrases    禁止出现的短语，用 | 分隔（红线检查）
    must_include         必须出现的要点，用 | 分隔
    history              多轮历史，JSON 数组，形如 [{"role": "user", "content": "..."}]
    user_id              该样本使用的用户身份；为空则用 EVAL_USER_ID
    note                 备注

留空表示不校验该项。
"""

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path

from atguigu.agent.harness.rules.action import ActionCode
from atguigu.agent.harness.tools.catalog import TOOL_CATALOG
from atguigu.agent.llm.output import ReplyType

DATASET_DIR = Path(__file__).resolve().parent / "datasets"
REQUIRED_COLUMNS = {"case_id", "question"}


@dataclass(frozen=True)
class EvalCase:
    """一条可执行的评估样本。"""

    case_id: str
    scene: str
    question: str
    ground_truth: str = ""
    expected_reply_type: str = ""
    expected_action_code: str = ""
    expected_tools: tuple[str, ...] = ()
    gold_knowledge_ids: tuple[str, ...] = ()
    forbidden_phrases: tuple[str, ...] = ()
    must_include: tuple[str, ...] = ()
    history: tuple[dict[str, str], ...] = field(default_factory=tuple)
    user_id: str = ""
    note: str = ""


def _split_phrases(raw: str) -> tuple[str, ...]:
    """把 | 分隔的短语拆成元组，忽略空项。"""
    return tuple(part.strip() for part in (raw or "").split("|") if part.strip())


def _parse_history(raw: str, case_id: str) -> tuple[dict[str, str], ...]:
    """解析多轮历史 JSON，结构不符时直接报错，避免脏数据悄悄进入评估。"""
    text = (raw or "").strip()
    if not text:
        return ()
    try:
        history = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"样本 {case_id} 的 history 不是合法 JSON") from exc
    if not isinstance(history, list):
        raise TypeError(f"样本 {case_id} 的 history 必须是数组")
    for item in history:
        if not isinstance(item, dict) or "role" not in item or "content" not in item:
            raise ValueError(f"样本 {case_id} 的 history 项必须包含 role 与 content")
        if item["role"] not in {"user", "ai", "human"}:
            raise ValueError(f"样本 {case_id} 的 history.role 非法：{item['role']}")
    return tuple(history)


def _validate_enum(raw: str, allowed: set[str], field_name: str, case_id: str) -> str:
    """校验可选项是否为合法枚举值。"""
    value = (raw or "").strip()
    if value and value not in allowed:
        raise ValueError(f"样本 {case_id} 的 {field_name} 非法：{value}")
    return value


def _validate_tools(raw: str, case_id: str) -> tuple[str, ...]:
    """校验期望工具都在工具目录中，避免拼写错误导致凭空失败。"""
    tools = _split_phrases(raw)
    unknown = [tool for tool in tools if tool not in TOOL_CATALOG.definitions]
    if unknown:
        raise ValueError(f"样本 {case_id} 的 expected_tools 不存在：{', '.join(unknown)}")
    return tools


def load_cases(paths: list[Path]) -> list[EvalCase]:
    """读取一个或多个 CSV 并合并成样本列表。"""
    cases: list[EvalCase] = []
    seen: set[str] = set()

    for path in paths:
        if not path.exists():
            raise FileNotFoundError(f"数据文件不存在：{path}")
        with path.open("r", encoding="utf-8-sig", newline="") as file:
            reader = csv.DictReader(file)
            missing = REQUIRED_COLUMNS - set(reader.fieldnames or [])
            if missing:
                raise ValueError(f"{path.name} 缺少必需列：{', '.join(sorted(missing))}")

            for row in reader:
                if not any(value and value.strip() for value in row.values()):
                    continue

                case_id = (row.get("case_id") or "").strip()
                question = (row.get("question") or "").strip()
                if not case_id or not question:
                    raise ValueError(f"{path.name} 存在 case_id 或 question 为空的样本")
                if case_id in seen:
                    raise ValueError(f"case_id 重复：{case_id}")
                seen.add(case_id)

                cases.append(
                    EvalCase(
                        case_id=case_id,
                        scene=(row.get("scene") or "").strip(),
                        question=question,
                        ground_truth=(row.get("ground_truth") or "").strip(),
                        expected_reply_type=_validate_enum(
                            row.get("expected_reply_type", ""),
                            {item.value for item in ReplyType},
                            "expected_reply_type",
                            case_id,
                        ),
                        expected_action_code=_validate_enum(
                            row.get("expected_action_code", ""),
                            {item.value for item in ActionCode},
                            "expected_action_code",
                            case_id,
                        ),
                        expected_tools=_validate_tools(row.get("expected_tools", ""), case_id),
                        gold_knowledge_ids=_split_phrases(row.get("gold_knowledge_ids", "")),
                        forbidden_phrases=_split_phrases(row.get("forbidden_phrases", "")),
                        must_include=_split_phrases(row.get("must_include", "")),
                        history=_parse_history(row.get("history", ""), case_id),
                        user_id=(row.get("user_id") or "").strip(),
                        note=(row.get("note") or "").strip(),
                    )
                )

    if not cases:
        raise ValueError("测试集为空")
    return cases


def default_dataset_paths() -> list[Path]:
    """默认加载 datasets 目录下的全部 CSV，按文件名排序保证顺序稳定。"""
    paths = sorted(DATASET_DIR.glob("*.csv"))
    if not paths:
        raise FileNotFoundError(f"{DATASET_DIR} 下没有 CSV 数据集")
    return paths
