"""使用 Ragas 评估知识库问答流程，逐题写入同目录的 result.csv。

安装依赖后运行：
    python -m pip install -r eval/requirements.txt
    python eval/eval.py --limit 2

评估会完整执行 atguigu 查询流程（向量检索、HyDE、图检索、网络搜索），
因此依赖项目 .env 中的 LLM、Milvus、Neo4j、MongoDB 和 MCP 配置，
并产生 LLM 调用费用。默认在每题结束后删除该题的临时会话历史，
使用 --keep-session 可以保留，便于排查问题。
"""

import argparse
import csv
import json
import logging
import math
import os
import re
import sys
from pathlib import Path
from threading import Lock
from uuid import uuid4


EVAL_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = EVAL_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# 关闭 Ragas 的匿名统计，不影响评估使用的模型客户端。
os.environ.setdefault("RAGAS_DO_NOT_TRACK", "true")
LOGGER = logging.getLogger(__name__)
METRIC_COLUMNS = (
    "faithfulness",
    "answer_relevancy",
    "context_precision",
    "context_recall",
    "answer_correctness",
)
OUTPUT_COLUMNS = (
    "sample_id", "question", "ground_truth", "answer", "contexts",
    *METRIC_COLUMNS, "status", "error",
)


def step_1_load_test_cases(input_path: Path) -> list[dict]:
    """读取 UTF-8 测试集，检查问题和标准答案，避免无效数据进入评估。"""
    with input_path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        required = {"question", "ground_truth"}
        if not required.issubset(reader.fieldnames or []):
            raise ValueError("测试集必须包含 question、ground_truth 两列")
        cases = []
        for row in reader:
            # CSV 允许答案包含换行；使用逻辑样本编号而非物理行号。
            if not any(value for value in row.values()):
                continue
            question = (row.get("question") or "").strip()
            reference = (row.get("ground_truth") or "").strip()
            if None in row or not question or not reference:
                raise ValueError(f"第 {len(cases) + 1} 条样本列数异常或问题/标准答案为空")
            cases.append({"question": question, "ground_truth": reference})
    if not cases:
        raise ValueError("测试集为空")
    return cases


def step_2_create_evaluators(model_name: str | None, timeout: int):
    """复用项目 LLM 与 BGE-M3，构建五项 Ragas 指标。"""
    from langchain.chat_models import init_chat_model
    from langchain_core.embeddings import Embeddings
    from openai import AsyncOpenAI
    from ragas.embeddings import LangchainEmbeddingsWrapper
    from ragas.llms import llm_factory
    from ragas.metrics import (
        AnswerCorrectness, AnswerRelevancy, ContextPrecision,
        ContextRecall, Faithfulness,
    )
    from ragas.run_config import RunConfig

    from atguigu.config.config import LLMConfig
    from atguigu.tool.embedding_utils import get_bge_embedding_model

    eval_model = model_name or LLMConfig.item_model or LLMConfig.llm_default_model
    if not eval_model:
        raise RuntimeError("未配置评估模型，请检查 ITEM_MODEL 或 LLM_DEFAULT_MODEL")

    # 百炼 Qwen3.7 默认开启 thinking，而 Ragas 的部分指标需要 n>1；
    # thinking 模式下 n 必须为 1，因此评估模型显式关闭 thinking。
    model_kwargs = {}
    if "aliyuncs.com" in (LLMConfig.aliyun_api_base or ""):
        model_kwargs["extra_body"] = {"enable_thinking": False}

    # 评估模型温度固定为 0，降低同一份答案的评分波动。
    llm_client = init_chat_model(
        model=eval_model,
        temperature=0.0,
        api_key=LLMConfig.aliyun_api_key,
        base_url=LLMConfig.aliyun_api_base,
        model_provider="openai",
        **model_kwargs,
    )
    embedding_model = get_bge_embedding_model()
    if embedding_model is None:
        raise RuntimeError("BGE-M3 模型初始化失败，请检查 BGE_M3_PATH 配置")

    # 项目中的 BGE-M3 是共享单例，评估线程必须串行访问。
    embedding_lock = Lock()

    class BGEM3EmbeddingsAdapter(Embeddings):
        """把项目的 BGE-M3 混合嵌入模型适配为 LangChain 稠密向量接口。"""

        # 以下两个方法名由 LangChain 接口规定。
        def embed_documents(self, texts: list[str]) -> list[list[float]]:
            """文档编码：使用文档侧编码方法，返回 dense 向量。"""
            return self._encode(texts, is_query=False)

        def embed_query(self, text: str) -> list[float]:
            """查询编码：使用查询侧编码方法，返回单条 dense 向量。"""
            return self._encode([text], is_query=True)[0]

        def _encode(self, texts: list[str], is_query: bool) -> list[list[float]]:
            """串行访问共享模型，避免评估线程并发调用 BGE-M3。"""
            if not texts:
                return []
            with embedding_lock:
                encode = embedding_model.encode_queries if is_query else embedding_model.encode_documents
                dense = encode(texts)["dense"]
                return [[float(value) for value in vector] for vector in dense]

    run_config = RunConfig(timeout=timeout, max_retries=2, max_workers=1)
    # 解析类指标使用 Ragas 的 Instructor 结构化输出，能自动处理 Markdown 代码块和
    # 控制字符；AnswerRelevancy 需要 n=3，仍使用原生 LangChain LLM 的批量生成路径。
    factory_kwargs = {"temperature": 0.0, "max_tokens": 4096}
    if "extra_body" in model_kwargs:
        factory_kwargs["extra_body"] = model_kwargs["extra_body"]
    structured_client = AsyncOpenAI(
        api_key=LLMConfig.aliyun_api_key,
        base_url=LLMConfig.aliyun_api_base,
    )
    llm_structured = llm_factory(eval_model, client=structured_client, **factory_kwargs)
    embeddings = LangchainEmbeddingsWrapper(BGEM3EmbeddingsAdapter(), run_config=run_config)
    metrics = [
        Faithfulness(llm=llm_structured),
        AnswerRelevancy(llm=llm_client, embeddings=embeddings),
        ContextPrecision(llm=llm_structured),
        ContextRecall(llm=llm_structured),
        AnswerCorrectness(llm=llm_structured, embeddings=embeddings),
    ]
    return metrics, run_config


def step_3_extract_contexts(state: dict, answer_node) -> list[str]:
    """提取实际进入答案提示词的检索上下文，保持检索顺序与截断行为。"""
    if not state.get("reranked_docs"):
        # 商品确认阶段直接作答或检索为空时，没有上下文可评估。
        return []

    # 复用答案节点自身的提示词组装逻辑，确保与线上生成答案的输入一致。
    _, _, prompt, _ = answer_node.format_prompt(state)

    # ANSWER_PROMPT 中参考内容位于这两个标记之间；提示词超长时会被截断，
    # 此时末尾标记可能不存在，截断后的内容就是实际送入模型的上下文。
    start_marker = "【参考内容】\n"
    end_marker = "\n\n【历史对话】"
    start = prompt.find(start_marker)
    if start < 0:
        return []
    start += len(start_marker)
    end = prompt.find(end_marker, start)
    context_text = prompt[start:] if end < 0 else prompt[start:end]

    # 答案节点把每篇文档格式化为 "[序号][来源][标题][URL]\n内容\n\n"。
    parts = re.split(r"(?=^\[\d+\]\[)", context_text, flags=re.MULTILINE)
    return [part.strip() for part in parts if part.strip()]


def step_4_invoke_rag(case: dict, runner, answer_node, keep_session: bool) -> tuple[str, list[str]]:
    """执行一次完整查询流程，并在结束后清理临时队列和会话历史。"""
    from atguigu.tool.mongo_client_tool import clear_history
    from atguigu.tool.task_utils import cleanup_task, create_queue

    identifier = uuid4().hex
    session_id = f"ragas_eval_session_{identifier}"
    task_id = f"ragas_eval_task_{identifier}"

    # 查询节点会通过 put_data 推送进度；队列必须先存在，否则节点会阻塞等待。
    create_queue(task_id)
    try:
        state = {
            "original_query": case["question"],
            "session_id": session_id,
            "task_id": task_id,
            "message_id": "",
        }
        # 标准答案只交给评估器，不传给被评估的 RAG。
        result = runner.run(state)
        answer = result.get("answer")
        if not isinstance(answer, str) or not answer.strip():
            raise ValueError("RAG 未返回有效的文本答案")
        contexts = step_3_extract_contexts(result, answer_node)
        return answer, contexts
    finally:
        # 评估不消费进度队列，及时移除任务资源，避免内存持续增长。
        cleanup_task(task_id)
        if not keep_session:
            try:
                clear_history(session_id)
            except Exception as exc:
                LOGGER.warning("清理临时会话 %s 失败: %s", session_id, exc)


def step_5_evaluate_sample(record: dict, contexts: list[str], metrics, run_config) -> dict:
    """构建单轮评估样本，输出五项分数，并显式标记无法计算的指标。"""
    from ragas import EvaluationDataset, evaluate

    dataset = EvaluationDataset.from_list([{
        "user_input": record["question"],
        "response": record["answer"],
        "retrieved_contexts": contexts,
        "reference": record["ground_truth"],
    }])
    result = evaluate(
        dataset=dataset,
        metrics=metrics,
        run_config=run_config,
        raise_exceptions=False,
        show_progress=False,
    )
    scores = result.scores[0]
    failed = []
    for name in METRIC_COLUMNS:
        raw_value = scores.get(name)
        # 无法计算的指标可能返回 None，统一转换成 NaN 便于写回 CSV。
        value = float(raw_value) if raw_value is not None else float("nan")
        record[name] = value
        if not math.isfinite(value):
            failed.append(name)
    record["status"] = "partial" if failed else "success"
    record["error"] = (
        "以下指标无法计算（NaN），请查看 Ragas 日志：" + ", ".join(failed)
        if failed else ""
    )
    return record


def step_6_save_results(records: list[dict], output_path: Path) -> None:
    """逐题保存完整进度，使用 UTF-8 BOM 方便 Excel 正确显示中文。"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_suffix(".csv.tmp")
    with temporary_path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()
        writer.writerows(records)
    # 先写临时文件再替换，降低中断导致已有结果被截断的风险。
    temporary_path.replace(output_path)


def step_7_main() -> int:
    """组织读取、调用、评分和保存流程，并报告有效分数均值与失败数量。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, help="仅评估测试集前 N 条，默认评估全部")
    parser.add_argument("--llm-model", help="评估模型名称，默认使用项目 ITEM_MODEL 配置")
    parser.add_argument("--timeout", type=int, default=180, help="单项评估超时秒数，默认 180")
    parser.add_argument("--keep-session", action="store_true", help="保留评估产生的临时会话历史")
    args = parser.parse_args()
    if args.limit is not None and args.limit <= 0:
        parser.error("--limit 必须为正整数")
    if args.timeout <= 0:
        parser.error("--timeout 必须为正整数")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    cases = step_1_load_test_cases(EVAL_DIR / "qa.csv")
    if args.limit is not None:
        cases = cases[:args.limit]

    from atguigu.query_process.nodes.node_answer_output import NodeAnswerOutput
    from atguigu.query_process.query_main_graph import QueryMainGraphRunner

    # 复用同一个查询流程实例，避免每道题重复编译 LangGraph。
    runner = QueryMainGraphRunner()
    answer_node = NodeAnswerOutput()
    metrics, run_config = step_2_create_evaluators(args.llm_model, args.timeout)
    records = [{
        "sample_id": index, **case, "answer": "", "contexts": "[]",
        **{name: float("nan") for name in METRIC_COLUMNS},
        "status": "pending", "error": "",
    } for index, case in enumerate(cases, 1)]
    output_path = EVAL_DIR / "result.csv"
    step_6_save_results(records, output_path)
    for record in records:
        LOGGER.info("正在评估 %s/%s", record["sample_id"], len(records))
        try:
            answer, contexts = step_4_invoke_rag(record, runner, answer_node, args.keep_session)
        except Exception as exc:
            # 保留失败样本，避免只统计成功样本而掩盖系统问题。
            record["status"] = "rag_failed"
            record["error"] = f"{type(exc).__name__}: {exc}"
            LOGGER.exception("第 %s 条样本的 RAG 调用失败", record["sample_id"])
        else:
            record["answer"] = answer
            record["contexts"] = json.dumps(contexts, ensure_ascii=False)
            if not contexts:
                LOGGER.info("第 %s 条样本没有检索上下文，检索类指标可能为 NaN", record["sample_id"])
            try:
                step_5_evaluate_sample(record, contexts, metrics, run_config)
            except Exception as exc:
                record["status"] = "eval_failed"
                record["error"] = f"{type(exc).__name__}: {exc}"
                LOGGER.exception("第 %s 条样本的评分失败", record["sample_id"])
        step_6_save_results(records, output_path)

    for name in METRIC_COLUMNS:
        values = [row[name] for row in records if math.isfinite(row[name])]
        LOGGER.info("%s: 均值=%s，有效样本=%s/%s", name,
                    f"{sum(values) / len(values):.4f}" if values else "NaN",
                    len(values), len(records))
    unsuccessful = sum(row["status"] != "success" for row in records)
    LOGGER.info("结果已保存至 %s；未完整评分样本数：%s", output_path, unsuccessful)
    return 1 if unsuccessful else 0


if __name__ == "__main__":
    raise SystemExit(step_7_main())
