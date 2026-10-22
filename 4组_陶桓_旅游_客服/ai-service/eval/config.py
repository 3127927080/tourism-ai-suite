"""评估专用配置。

复用项目 .env 的数据库、JWT、LLM 配置，额外增加评估才需要的字段。
所有 EVAL_* 变量都可以写在同一个 .env 里，也可以由命令行覆盖。
"""

from atguigu.common.config import Settings


class EvalSettings(Settings):
    """在服务配置基础上追加评估参数。"""

    # 评估样本默认使用的用户身份。知识类样本不依赖真实业务数据，任意值即可；
    # 订单/出行类样本必须换成旅游数据服务里真实存在的 user_id，否则工具调用会失败。
    eval_user_id: str = "eval-customer"

    # 订单类样本可以直接提供一个真实用户 JWT；为空时脚本按 JWT_SECRET 自动签发。
    eval_user_token: str = ""

    # 评分用的 judge 模型，留空则复用 llm_model。建议与被测模型分开，避免自评偏差。
    # base_url / api_key 留空时复用主模型配置，方便接入同一个 OpenAI 兼容网关。
    eval_judge_model: str = ""
    eval_judge_base_url: str = ""
    eval_judge_api_key: str = ""

    # 语义类指标（answer_relevancy / answer_correctness）依赖 embedding。
    # 三项都配置才会启用这两个指标，否则自动跳过并在结果里标记 skipped。
    eval_embedding_model: str = ""
    eval_embedding_base_url: str = ""
    eval_embedding_api_key: str = ""

    # 单条样本调用 RAGAS 的超时秒数。
    eval_timeout_seconds: int = 180

    @property
    def embedding_ready(self) -> bool:
        """判断语义类指标是否具备运行条件。"""
        return bool(self.eval_embedding_model and self.eval_embedding_base_url)

    @property
    def judge_api_key(self) -> str:
        """judge 模型没有单独配置密钥时复用主模型密钥。"""
        return self.eval_judge_api_key or self.llm_api_key

    @property
    def judge_base_url(self) -> str:
        """judge 端点留空时复用被测模型的端点。"""
        return self.eval_judge_base_url or self.llm_base_url

    @property
    def embedding_api_key(self) -> str:
        """embedding 密钥缺省时复用主模型密钥。"""
        return self.eval_embedding_api_key or self.llm_api_key


def get_eval_settings() -> EvalSettings:
    """构造评估配置（不走 lru_cache，方便命令行覆盖后重新实例化）。"""
    return EvalSettings()
