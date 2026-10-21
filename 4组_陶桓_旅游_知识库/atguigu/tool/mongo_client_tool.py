"""
有关mongo的工具
"""
import time
from pymongo import MongoClient, errors
from atguigu.config.config import MongoConfig
from atguigu.tool.logger import logger

_mongo_client = None
_db = None
_collection = None

def get_mongo_client():
    """获取 MongoDB 客户端（单例）"""
    global _mongo_client
    try:
        if _mongo_client is None:
            _mongo_client = MongoClient(
                MongoConfig.mongo_url,
                serverSelectionTimeoutMS=5000
            )
            # 验证连接
            _mongo_client.admin.command('ping')
            logger.info("✅ MongoDB 连接成功")
        return _mongo_client
    except errors.ConnectionFailure as e:
        logger.error(f"MongoDB 连接失败: {e}")
        raise RuntimeError("MongoDB 连接失败，请检查服务是否启动") from e
    except Exception as e:
        logger.error(f"MongoDB 连接异常: {e}")
        raise RuntimeError(f"MongoDB 连接异常: {e}") from e

def get_mongo_collection(collection_name="chat_history"):
    """
    获取 MongoDB 集合
    :param collection_name: 集合名称，默认 "chat_history"
    :return: 集合对象
    """
    global _db, _collection

    try:
        client = get_mongo_client()

        if _db is None:
            _db = client[MongoConfig.mongo_db_name]
            logger.info(f"✅ 切换到数据库: {MongoConfig.mongo_db_name}")

        # 如果集合名称变化或未初始化，重新获取
        if _collection is None or _collection.name != collection_name:
            _collection = _db[collection_name]
            # ✅ 创建合理的索引
            _collection.create_index([("session_id", 1), ("ts", -1)])
            _collection.create_index([("ts", -1)])
            logger.info(f"✅ 集合 {collection_name} 就绪")

        return _collection
    except Exception as e:
        logger.error(f"获取集合失败: {e}")
        raise RuntimeError(f"获取集合失败: {e}") from e

def get_history_list(session_id, limit=10, ascending=False):
    """获取最近的 limit 条消息

    ascending=True 时按时间正序返回，适合直接拼接到提示词中；
    默认保持倒序，兼容前端历史列表接口。
    """
    collection = get_mongo_collection()
    result = collection.find({"session_id": session_id}).sort("ts", -1).limit(limit)

    items = list(result)
    if ascending:
        items.reverse()
    return items

def add_or_update_history(session_id,role,text,rewritten_query=None,item_names=None,image_urls=None,ts=None,_id=None):
    # 全量更新和增量更新
    # 为什么人们在封装数据库增和改的时候全部合二为一写一个方法或者函数，就是因为他们传递参数的时候唯一不同就是id
    # 如果是修改，那么id一定存在
    # 如果是新增，那么id一定不存在
    collection = get_mongo_collection()
    if _id:
        # 修改操作
        data = {
            "_id": _id,
            "session_id": session_id,
            "role": role,
            "text": text,
            "rewritten_query": rewritten_query,
            "item_names": item_names,
            "image_urls": image_urls,
            "ts": ts or time.time(),
        }
        collection.update_one({"_id": _id}, {"$set": data})
        return _id
    else:
        # 新增操作
        data = {
            "session_id": session_id,
            "role": role,
            "text": text,
            "rewritten_query": rewritten_query,
            "item_names": item_names,
            "image_urls": image_urls,
            "ts": ts or time.time(),
        }
        result = collection.insert_one(data)
        print(result.inserted_id)
        return result.inserted_id

def clear_history(session_id):
    """删除指定会话的所有历史记录"""
    collection = get_mongo_collection()
    result = collection.delete_many({"session_id": session_id})
    logger.info(f"✅ 删除 {result.deleted_count} 条历史记录")
    return result.deleted_count

def update_item_names_and_query(ids, item_names=None, rewritten_query=None):
    """
    更新会话中所有消息的主题和重写查询
    """
    collection = get_mongo_collection()
    collection.update_many(
        {"_id": {"$in": ids}},
        {
            "$set": {
                "item_names": item_names,
                "rewritten_query": rewritten_query
            }
        }
    )


if __name__ == '__main__':
    add_or_update_history("test_001", "user", "咨询下烫金机。")
    # add_or_update_history("test_001", "assistant", "您好。请问是哪个型号")
    # result = add_or_update_history("test_001", "user", "hak180")
    # print(result,type(result))
    # add_or_update_history("test_001", "assistant", "具体有什么问题呢？")


    # result = get_recent_history_list("test_001")
    # print(result)

