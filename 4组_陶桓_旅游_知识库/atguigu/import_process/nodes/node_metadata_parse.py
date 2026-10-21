# atguigu/import_process/nodes/node_metadata_parse.py
"""旅游元数据解析节点。

职责：
1. 解析 Markdown 文件开头的 `## 元数据` 段，归一化为统一 schema；
2. 城市缺失时用文件名前缀兜底，仍缺失再调用 LLM 兜底；
3. 线路类文档解析行程天数，并用 LLM 归纳预算区间（标记为估算）；
4. 用 LLM 对 `###` 标题做实体分类，过滤结构性标题；
5. 任何一步失败都只记录 warning，不阻断入库。
"""
import json
import re
from pathlib import Path

from langchain.chat_models import init_chat_model

from atguigu.config.config import LLMConfig, TourismConfig
from atguigu.config.prompt import (
    BUDGET_ESTIMATE_SYSTEM_PROMPT,
    BUDGET_ESTIMATE_USER_TEMPLATE,
    ENTITY_TITLE_CLASSIFY_SYSTEM_PROMPT,
    ENTITY_TITLE_CLASSIFY_USER_TEMPLATE,
    METADATA_FALLBACK_SYSTEM_PROMPT,
    METADATA_FALLBACK_USER_TEMPLATE,
)
from atguigu.import_process.base import NodeBase
from atguigu.import_process.state import ImportGraphState
from atguigu.tool.logger import logger

# 实体类型白名单，LLM 给出其他值时归为「其他」
ENTITY_TYPES = {"景点", "片区", "古镇", "酒店", "餐厅", "线路", "民俗项目", "民俗", "其他"}
# 实体数量上限，防止标题很长时把整篇结构都当成实体
MAX_ENTITIES = 50
# 结构性标题兜底黑名单：即使 LLM 误判，也不写入实体
STRUCTURAL_PATTERNS = (
    r"^第[一二三四五六七八九十\d]+层",
    r"核心景点$",
    r"核心片区$",
    r"型景点$",
    r"适合人群",
    r"优先级",
    r"景点安排",
    r"停留时长",
    r"常见决策点",
    r"推荐游玩逻辑",
    r"行程逻辑$",
    r"注意事项$",
    r"^建议",
    # 文化民俗类文档里的结构性标题
    r"文化线$",
    r"半日线$",
    r"体验方式",
    r"体验项目",
    r"礼仪",
    r"常见问题",
    r"目的地概览",
    r"核心文化",
    r"可体验的",
)


def is_structural_title(name: str) -> bool:
    """判断标题是否为结构性描述而非真实实体。"""
    return any(re.search(pattern, name) for pattern in STRUCTURAL_PATTERNS)


class NodeMetadataParse(NodeBase):
    """解析并归一旅游文档元数据。"""

    name = "node_metadata_parse"

    # ============================================================
    # 基础工具
    # ============================================================
    def get_md_path(self, state: ImportGraphState) -> Path:
        md_path = state.get("md_path") or ""
        if not md_path:
            logger.error("md_path必须提供")
            raise ValueError("md_path必须提供")
        path_obj = Path(md_path)
        if not path_obj.exists():
            logger.error(f"文件不存在：{md_path}")
            raise ValueError(f"文件不存在：{md_path}")
        return path_obj

    def read_metadata_block(self, content: str) -> dict:
        """提取 `## 元数据` 段的 key-value，兼容全角冒号与缺项。"""
        match = re.search(r"^##\s*元数据\s*$\n(.*?)(?=^##\s|\Z)", content, re.M | re.S)
        if not match:
            return {}
        raw = {}
        for line in match.group(1).splitlines():
            line = line.strip()
            if not line:
                continue
            item = re.match(r"^[-*+]\s*([^:：]+)[:：]\s*(.*)$", line)
            if not item:
                continue
            raw[item.group(1).strip()] = item.group(2).strip()
        return raw

    def split_cities(self, text: str) -> list[str]:
        """把「昆明、大理、丽江」这类文本拆成城市列表。"""
        if not text:
            return []
        cleaned = text.replace("多城市", "")
        parts = re.split(r"[、，,/\s]+", cleaned)
        return [p for p in (x.strip() for x in parts) if p]

    # ============================================================
    # 归一化
    # ============================================================
    def normalize(self, raw: dict, md_path: Path) -> dict:
        warnings: list[str] = []

        # 1. 内容类型
        content_type = (raw.get("内容类型") or "").strip().replace(" ", "").replace("　", "")
        content_type = TourismConfig.content_type_aliases.get(content_type, content_type)
        if content_type and content_type not in TourismConfig.content_types:
            warnings.append(f"非法内容类型：{content_type}")
            content_type = TourismConfig.unknown_content_type
        if not content_type:
            warnings.append("元数据缺少内容类型")

        # 2. 城市
        city_text = raw.get("城市") or raw.get("目的地") or ""
        cities = self.split_cities(city_text)
        file_prefix_city = next(
            (d for d in TourismConfig.destinations if md_path.stem.startswith(d)), ""
        )
        if file_prefix_city:
            city = file_prefix_city
        elif cities:
            city = cities[0]
        else:
            city = ""
        # 「云南多城市」这类文档展开成具体城市
        if city in TourismConfig.multi_city_defaults:
            cities = list(TourismConfig.multi_city_defaults[city])

        # 3. 名称字段
        scenic_name = (raw.get("景点名称") or "").strip()
        route_name = (raw.get("线路名称") or "").strip()
        hotel_name = (raw.get("住宿名称") or raw.get("酒店名称") or "").strip()
        restaurant_name = (raw.get("餐厅名称") or "").strip()
        doc_name = (raw.get("文档名称") or "").strip()
        if doc_name and not restaurant_name and content_type == "美食推荐":
            # 美食文档没有「餐厅名称」，用「文档名称」顶上
            restaurant_name = doc_name

        # 4. 来源
        source_file = md_path.name
        content_dir = TourismConfig.content_type_to_dir.get(content_type, "")
        source_path = f"{content_dir}/{source_file}" if content_dir else source_file

        meta = {
            "content_type": content_type,
            "city": city,
            "cities": cities,
            "scenic_name": scenic_name,
            "route_name": route_name,
            "hotel_name": hotel_name,
            "restaurant_name": restaurant_name,
            "source_file": source_file,
            "source_path": source_path,
            "theme": (raw.get("主题") or "").strip(),
            "entities": [],
            "duration_days": 0,
            "budget_ref": "",
            "estimated": False,
            "raw": raw,
            "warnings": warnings,
        }
        return meta

    # ============================================================
    # LLM 调用
    # ============================================================
    def invoke_json(self, system_prompt: str, user_prompt: str) -> dict:
        """调用 LLM 并解析 JSON，失败返回空字典。"""
        llm = init_chat_model(
            model=LLMConfig.item_model,
            model_provider="openai",
            api_key=LLMConfig.aliyun_api_key,
            base_url=LLMConfig.aliyun_api_base,
            temperature=LLMConfig.llm_default_temperature,
        )
        result = llm.invoke(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]
        )
        text = str(result.content or "").strip()
        text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
        match = re.search(r"\{.*\}", text, re.S)
        if not match:
            return {}
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            logger.warning(f"LLM 返回非合法 JSON：{text[:200]}")
            return {}

    def llm_fill_meta(self, meta: dict, content: str) -> dict:
        """元数据缺城市或内容类型时，用 LLM 兜底。"""
        if meta.get("city") and meta.get("content_type") not in (
            "",
            TourismConfig.unknown_content_type,
        ):
            return meta
        try:
            filled = self.invoke_json(
                METADATA_FALLBACK_SYSTEM_PROMPT,
                METADATA_FALLBACK_USER_TEMPLATE.format(
                    file_title=meta["source_file"], context=content[:3000]
                ),
            )
        except Exception as exc:  # noqa: BLE001
            meta["warnings"].append(f"元数据兜底失败：{exc}")
            return meta

        for key in ("scenic_name", "route_name", "hotel_name", "restaurant_name", "theme"):
            if not meta.get(key) and filled.get(key):
                meta[key] = str(filled[key]).strip()

        if not meta.get("city") and filled.get("city") in TourismConfig.destinations:
            candidate = filled["city"]
            # 兜底猜测必须有正文依据，避免把非旅游文档硬塞进某个城市
            if candidate in content:
                meta["city"] = candidate
                meta["cities"] = [candidate]
            else:
                meta["warnings"].append(
                    f"兜底城市「{candidate}」未在正文出现，已置空"
                )

        filled_type = filled.get("content_type", "")
        if meta.get("content_type") in ("", TourismConfig.unknown_content_type):
            if filled_type in TourismConfig.content_types:
                meta["content_type"] = filled_type
            else:
                meta["warnings"].append("内容类型仍无法识别")

        if meta["content_type"] in TourismConfig.content_type_to_dir:
            meta["source_path"] = (
                f"{TourismConfig.content_type_to_dir[meta['content_type']]}/"
                f"{meta['source_file']}"
            )
        return meta

    def classify_entities(self, meta: dict, content: str) -> list[dict]:
        """对 ### 标题做实体分类，并校验实体确实出现在正文中。"""
        titles = []
        for line in content.splitlines():
            if line.startswith("### "):
                title = line[4:].strip()
                if title and title not in titles:
                    titles.append(title)
        if not titles:
            return []

        try:
            result = self.invoke_json(
                ENTITY_TITLE_CLASSIFY_SYSTEM_PROMPT,
                ENTITY_TITLE_CLASSIFY_USER_TEMPLATE.format(
                    city=meta.get("city", ""),
                    content_type=meta.get("content_type", ""),
                    titles="\n".join(f"- {t}" for t in titles),
                ),
            )
        except Exception as exc:  # noqa: BLE001
            meta["warnings"].append(f"实体分类失败：{exc}")
            return []

        compact_content = content.replace(" ", "")
        entities, seen = [], set()
        for item in result.get("entities", []) or []:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            entity_type = str(item.get("type") or "").strip()
            if not name or name in seen:
                continue
            # 结构性标题不写入实体
            if is_structural_title(name):
                continue
            # 过滤幻觉：实体必须出现在正文里
            if name.replace(" ", "") not in compact_content:
                meta["warnings"].append(f"实体未在正文出现，已丢弃：{name}")
                continue
            seen.add(name)
            entities.append(
                {"name": name, "type": entity_type if entity_type in ENTITY_TYPES else "其他"}
            )
            if len(entities) >= MAX_ENTITIES:
                break
        return entities

    # ============================================================
    # 线路：天数与预算
    # ============================================================
    def parse_duration(self, meta: dict, content: str) -> int:
        """从线路名优先、正文兜底解析行程天数；「2 至 3 日」取上限。"""
        for text in (meta.get("route_name", ""), content[:500]):
            days = [int(x) for x in re.findall(r"(\d+)\s*日", text or "")]
            if days:
                return max(days)
        return 0

    def estimate_budget(self, meta: dict) -> tuple[str, bool]:
        """线路预算由 LLM 归纳，必须标注为估算。"""
        try:
            result = self.invoke_json(
                BUDGET_ESTIMATE_SYSTEM_PROMPT,
                BUDGET_ESTIMATE_USER_TEMPLATE.format(
                    route_name=meta.get("route_name", ""),
                    duration_days=meta.get("duration_days", 0),
                    cities="、".join(meta.get("cities", [])) or meta.get("city", ""),
                    theme=meta.get("theme", ""),
                ),
            )
        except Exception as exc:  # noqa: BLE001
            meta["warnings"].append(f"预算归纳失败：{exc}")
            return "", False
        budget_ref = str(result.get("budget_ref") or "").strip()
        if not budget_ref:
            return "", False
        if "估算" not in budget_ref:
            budget_ref += "（估算，不含往返大交通）"
        return budget_ref, True

    # ============================================================
    # 主流程
    # ============================================================
    def process(self, state: ImportGraphState):
        md_path = self.get_md_path(state)
        content = md_path.read_text(encoding="utf-8")
        content = content.replace("\r\n", "\n").replace("\r", "\n")

        raw = self.read_metadata_block(content)
        meta = self.normalize(raw, md_path)
        if not raw:
            meta["warnings"].append("未找到 ## 元数据 段，走兜底识别")

        meta = self.llm_fill_meta(meta, content)
        meta["entities"] = self.classify_entities(meta, content)

        if meta["content_type"] == "线路推荐":
            meta["duration_days"] = self.parse_duration(meta, content)
            budget_ref, estimated = self.estimate_budget(meta)
            meta["budget_ref"] = budget_ref
            meta["estimated"] = estimated
        elif meta["content_type"] == "景点介绍":
            # 景点文档也可能带「X 日」表述，但不作为线路天数使用
            meta["duration_days"] = 0

        logger.info(
            f"元数据解析完成：{meta['source_file']} -> "
            f"city={meta['city']} content_type={meta['content_type']} "
            f"entities={len(meta['entities'])} warnings={len(meta['warnings'])}"
        )
        if meta["warnings"]:
            for warning in meta["warnings"]:
                logger.warning(f"[{meta['source_file']}] {warning}")

        return {"doc_metadata": meta}
