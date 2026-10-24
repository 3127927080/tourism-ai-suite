# atguigu/import_process/state.py

from typing import TypedDict, List, Optional, Dict, Any

class ImportGraphState(TypedDict):
    """
    存库阶段图的状态定义
    包含所有节点产生和消费的数据字段
    """

    # ============================================================
    # 1. 任务追踪与日志
    # ============================================================
    task_id: str  # 任务唯一ID，用于追踪日志

    # ============================================================
    # 2. 流程控制标记
    # ============================================================
    is_md_read_enabled: bool   # 是否启用 Markdown 读取路径
    is_pdf_read_enabled: bool  # 是否启用 PDF 读取路径

    # ============================================================
    # 3. 路径相关
    # ============================================================
    local_dir: str          # 当前工作目录或输出目录
    local_file_path: str    # 原始输入文件路径
    file_title: str         # 文件标题（文件名去后缀）
    pdf_path: str           # PDF 文件路径 (如果输入是PDF)
    md_path: str            # Markdown 文件路径 (转换后或直接输入的)

    # ============================================================
    # 4. 内容数据
    # ============================================================
    md_content: str         # Markdown 的全文内容

    # 切片列表，每个切片包含：
    #   - title: str (标题)
    #   - content: str (内容)
    #   - file_title: str (文件名)
    #   - part: int (切片序号)
    #   - item_name: str (主体名称，由主体识别节点添加)
    #   - dense_vector: List[float] (稠密向量，由向量化节点添加)
    #   - sparse_vector: Dict[int, float] (稀疏向量，由向量化节点添加)
    chunks: List[Dict[str, Any]]

    # 主体名称（旅游版为目的地城市，如 "三亚"）
    item_name: str

    # ============================================================
    # 4.1 旅游元数据（node_metadata_parse 产出）
    # ============================================================
    # 统一元数据字典，字段：
    #   content_type: str          景点介绍/线路推荐/酒店信息/美食推荐/交通指南/文化民俗
    #   city: str                  主目的地
    #   cities: List[str]          目的地列表（云南多城市会展开）
    #   scenic_name / route_name / hotel_name / restaurant_name: str  文档级名称
    #   source_file / source_path: str   来源文件与相对路径（幂等删除键）
    #   theme: str                 主题
    #   entities: List[Dict]       [{"name": "亚龙湾", "type": "景点"}]
    #   duration_days: int         线路天数，非线路类为 0
    #   budget_ref: str            预算估算文本，非线路类为空
    #   estimated: bool            预算是否为估算值
    #   warnings: List[str]        解析告警
    doc_metadata: Dict[str, Any]

    # ============================================================
    # 5. 图片处理（node_md_img）
    # ============================================================
    # 图片上下文列表，每个元素包含：
    #   - image_name: str (图片文件名)
    #   - image_path: str (图片本地路径)
    #   - pre_text: str (图片前文)
    #   - post_text: str (图片后文)
    #   - summary: str (图片摘要，由VL模型生成)
    #   - url: str (上传到MinIO后的URL)
    image_with_context_list: List[Dict[str, Any]]

    # ============================================================
    # 6. 图数据库（Neo4j）相关
    # ============================================================
    # 三元组列表，格式：[["头实体", "关系", "尾实体"], ...]
    # 例如：[["亚龙湾", "位于", "三亚"], ["玉龙雪山", "需要", "提前预约"]]
    triples: List[List[str]]

    # 图入库统计信息（可选）
    graph_import_stats: Optional[Dict[str, int]]

    # ============================================================
    # 7. 数据库关联
    # ============================================================
    # 向量入库后的结果（包含id等信息）
    embeddings_content: List[Dict[str, Any]]

    # ============================================================
    # 8. 错误信息
    # ============================================================
    error: Optional[str]