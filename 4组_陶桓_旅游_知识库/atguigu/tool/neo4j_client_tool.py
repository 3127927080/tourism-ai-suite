"""
Neo4j 数据库客户端（单例模式，仿照 get_minio_client）
"""
from neo4j import GraphDatabase, Driver
from atguigu.config.config import Neo4jConfig
from atguigu.tool.logger import logger

_neo4j_driver: Driver = None

def get_neo4j_driver() -> Driver:
    """
    获取全局唯一的 Neo4j 驱动实例（单例）
    如果未连接则自动创建连接，并验证连通性
    """
    global _neo4j_driver
    if _neo4j_driver is None:
        try:
            _neo4j_driver = GraphDatabase.driver(
                Neo4jConfig.URI,
                auth=Neo4jConfig.get_auth(),
                max_connection_lifetime=3600,
                max_connection_pool_size=50,
            )
            # 验证连接是否成功
            _neo4j_driver.verify_connectivity()
            logger.info(f"✅ Neo4j 连接成功: {Neo4jConfig.URI}")
        except Exception as e:
            logger.error(f"❌ Neo4j 连接失败: {e}")
            raise RuntimeError("Neo4j 连接失败，请检查配置和服务状态") from e
    return _neo4j_driver

def close_neo4j_connection():
    """
    关闭 Neo4j 连接（应用退出时调用）
    """
    global _neo4j_driver
    if _neo4j_driver:
        _neo4j_driver.close()
        _neo4j_driver = None
        logger.info("✅ Neo4j 连接已关闭")