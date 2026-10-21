# atguigu/import_process/nodes/node_md_img.py
import base64
import os
import time
from collections import deque
from pathlib import Path
import re
from typing import Any

from langchain.chat_models import init_chat_model
from minio.deleteobjects import DeleteObject

from atguigu.config.config import LLMConfig, MinIoConfig
from atguigu.import_process.base import NodeBase
from atguigu.import_process.state import ImportGraphState
from atguigu.tool.json_format_tool import json_format
from atguigu.tool.logger import logger
from atguigu.tool.minio_client_tool import get_minio_client


class NodeMDImg(NodeBase):
    """
    MarkDown图片处理节点：多模态图片理解
    """

    name = "node_md_img"


    def get_md_content(self, state: ImportGraphState) -> tuple[Path, str]:
        md_path = state.get("md_path", "")
        if not md_path:
            logger.error("md文件路径为空")
            raise ValueError("md文件路径必须提供")
        # 有文件之后还要判断文件对应的地址也存在
        md_path_obj = Path(md_path)
        if not md_path_obj.exists():
            logger.error(f"文件{md_path}不存在")
            raise ValueError(f"文件{md_path}不存在")

        # 获取文件里面的内容,如果后面节点完成好了可以直接在state里面获取
        # md_content = state.get("md_content", "")
        with open(md_path_obj, 'r', encoding="utf-8") as f:
            md_content = f.read()

        # 校验文件内容存不存在
        if not md_content:
            logger.error("md文件内容为空")
            raise ValueError("md文件内容为空")
        return md_content, md_path_obj

    def get_image_with_context_list(self, image_name_list: list[str] | list[bytes], img_dir_path_obj,
                                    md_content: Path) -> \
    list[Any]:
        # 图片文件的后缀要是这些才行
        IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"}
        MAX_CONTEXT_LENGTH = 250
        image_with_context_list = []
        for image_name in image_name_list:
            # 第一步对文件进行格式判断
            if Path(image_name).suffix not in IMAGE_EXTENSIONS:
                logger.warning(f"图片文件格式错误:{image_name}")
                continue
            # 确定是图片之后,利用正则判断图片在md文件中的位置
            pattern = re.compile(r"!\[.*?\]\(.*?" + re.escape(image_name) + r"\)")
            match = pattern.search(md_content)
            if not match:
                logger.warning(f"图片文件{image_name}在md文件中不存在")
                continue

            # 如果存在了,开始准备切割上下文
            start, end = match.span()
            pre_text = md_content[max(0, start - MAX_CONTEXT_LENGTH):start]
            post_text = md_content[end:end + MAX_CONTEXT_LENGTH]
            # 把图片和上下文构造成字典，添加到准备好的列表当中
            # 构造这个图片的路径一起放到字典
            image_path = str(img_dir_path_obj / image_name)

            image_with_context_list.append({
                "image_name": image_name,
                "pre_text": pre_text,
                "post_text": post_text,
                "image_path": image_path
            })
        # return image_with_context_list
        return image_with_context_list

    def get_image_with_summary_list(self, image_with_context_list):
        MAX_COUNT = 300
        # 3.1创建滑动时间窗口来完成请求的分割,保证每分钟请求数不超3000
        dq = deque(maxlen=MAX_COUNT)
        # 创建一个视觉模型
        llm = init_chat_model(
            model=LLMConfig.vl_model,
            model_provider="openai",
            api_key=LLMConfig.aliyun_api_key,
            base_url=LLMConfig.aliyun_api_base,
            temperature=LLMConfig.llm_default_temperature
        )
        for image_with_context in image_with_context_list:
            # 第一步先进行一波清理,将dq里面超时的进行清理
            current_time = time.time()
            while dq and (current_time - dq[0] > 60):
                dq.popleft()
            # 清理完成后判断当前dq里面的大小,满了就等待
            if len(dq) >= MAX_COUNT:
                # 计算等待的时间
                wait_time = 60 - (current_time - dq[0])
                time.sleep(wait_time)
                current_time = time.time()
                while dq and (current_time - dq[0] > 60):
                    dq.popleft()
            # 做完上面的步骤之后,dq里面一定有空位置
            current_time = time.time()
            dq.append(current_time)

            # 确认可以开始发送请求后,可以准备调用模型进行图片理解
            # 先将图片内容转换成base64
            with open(image_with_context.get("image_path"), 'rb') as f:
                image_data = f.read()
                base64_str = base64.b64encode(image_data).decode('utf-8')

            # 生成模型的输入
            messages = [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                # 这个格式就是base64在使用的时候的规定
                                "url": "data:image/jpeg;base64," + base64_str,
                            },
                        },
                        {"type": "text", "text": f"""
                                                            这是一张图片，图片上文部分为"{image_with_context.get("pre_text")}"，
                                                            下文部分为"{image_with_context.get("post_text")}"，
                                                            请用中文简要总结这张图片的摘要,字数在50字以内。"""
                         },
                    ],
                },
            ]

            try:
                res = llm.invoke(messages)
                image_with_context["summary"] = res.content
            except Exception as exc:  # noqa: BLE001
                # 单张图片理解失败不应中断整篇文档入库（额度不足、模型异常等）
                logger.warning(
                    f"图片理解失败，已跳过 {image_with_context.get('image_name')}："
                    f"{type(exc).__name__}: {exc}"
                )
                image_with_context["summary"] = ""
                image_with_context["skip"] = True
        return image_with_context_list

    def get_doc_image_prefix(self, doc_key: str) -> str:
        """按文档生成图片目录前缀，避免不同文档互相覆盖。"""
        safe = re.sub(r"[^0-9A-Za-z一-龥._-]+", "_", str(doc_key or "doc")).strip("._")
        return f"{MinIoConfig.minio_img_dir}/{safe or 'doc'}"

    def get_image_with_summary_and_url_list(self, image_with_context_list, doc_key: str):
        # 4.1获取客户端对象
        minio_client = get_minio_client()
        # 4.2 每份文档一个独立目录：upload-images/<文档名>/
        prefix = self.get_doc_image_prefix(doc_key)
        logger.info(f"图片上传目录：{prefix}")
        # 4.3幂等性删除：只清理当前文档目录下的旧图片
        old_img_list = minio_client.list_objects(
            bucket_name=MinIoConfig.minio_bucket_name, prefix=prefix + "/", recursive=True
        )
        # 批量删除的时候要求列表的对象为delect_object
        delete_object_list = [DeleteObject(obj.object_name) for obj in old_img_list]
        #参考官网,minio_client.remove_objects返回的是一个迭代器,不便利不会实际去删除
        errors = minio_client.remove_objects(bucket_name=MinIoConfig.minio_bucket_name,
                                    delete_object_list=delete_object_list)

        for error in errors:
            logger.error("error occurred when deleting object", error)

        # 删除完成后可以开始上传图片
        for image_with_context in image_with_context_list:
            try:
                object_name = f"{prefix}/{image_with_context.get('image_name')}"
                minio_client.fput_object(bucket_name=MinIoConfig.minio_bucket_name,
                                         object_name=object_name,
                                         file_path=image_with_context.get("image_path"),
                                         )
                url = f"http://{MinIoConfig.minio_endpoint}/{MinIoConfig.minio_bucket_name}/{object_name}"
                # 将拼接的url放进字典里面
                image_with_context["url"] = url
            except Exception as exc:  # noqa: BLE001
                # 图片上传失败只跳过该图片，正文仍可入库
                logger.warning(
                    f"图片上传 MinIO 失败，已跳过 {image_with_context.get('image_name')}："
                    f"{type(exc).__name__}: {exc}"
                )
                image_with_context["url"] = ""
        return image_with_context_list

    def replace_md_image(self, image_with_context_list,md_path_obj,md_content):
        for image_with_context in image_with_context_list:
            # 上传失败或理解失败时保留原始图片引用，避免生成空链接
            if not image_with_context.get("url"):
                continue
            pattern = re.compile(r"!\[.*?\]\(.*?" + re.escape(image_with_context["image_name"]) + r"\)")
            # md_content = pattern.sub(f"![{image_with_summary_and_url.get("summary")}]({image_with_summary_and_url.get("url")})", md_content)
            # lambda 匿名函数，可以规避特殊字符，特殊字符也能替换成功
            md_content = pattern.sub(
                lambda _: f"![{image_with_context.get("summary")}]({image_with_context.get("url")})",
                md_content)
            logger.info(f"替换图片{image_with_context.get('image_name')}成功")
        # 因为下一步节点要使用md_content,所以这里将内容保存到一个新的文件里,方便读取,后期节点串联起来后可以删除
        new_md_path_obj = md_path_obj.parent / str(md_path_obj.stem + "_new.md")
        with open(new_md_path_obj, "w", encoding="utf-8") as f:
            f.write(md_content)
        return md_content,new_md_path_obj

    def process(self, state: ImportGraphState):
        #1.第一步,获取md文件路径,判断文件是否存在
        md_content, md_path_obj = self.get_md_content(state)

        #构造图片保存的目录
        img_dir_path_obj = md_path_obj.parent / "images"
        #判断文件夹是否存在,不存在说明我们的md里面没有要处理的图片
        if not img_dir_path_obj.exists():
            logger.warning("md文件里面没有图片")
            return {
                "md_content": md_content
            }

        #获取目录下面的所有文件夹和文件的名字
        image_name_list=os.listdir(img_dir_path_obj)
        # 在对目录里面的内容进行判断,如果里面没有图片,则返回md文件内容
        if not image_name_list:
            logger.warning("md文件里面没有图片")
            return {
                "md_content": md_content
            }

        #2.有了文件名列表后,可以去获取图片的对应的上下文
        image_with_context_list = self.get_image_with_context_list(image_name_list, img_dir_path_obj, md_content)

        #3.第三步,利用多模态模型进行图片理解
        image_with_context_list = self.get_image_with_summary_list(image_with_context_list)

        #4.第四步,将图片上传到minio,获取对应的url,放进字典列表
        image_with_context_list = self.get_image_with_summary_and_url_list(
            image_with_context_list, md_path_obj.stem
        )

        #第五步,利用得到的字典,进行md文件的图片替换
        md_content,new_md_path_obj = self.replace_md_image(image_with_context_list, md_path_obj,md_content)
        return {
            "md_content": md_content,
            "md_path": str(new_md_path_obj),
        }








if __name__ == "__main__":
    node = NodeMDImg()
    state = {
        "md_path": r"C:\Users\th\Desktop\4组_陶桓_实战\4组_陶桓_旅游_知识库\旅游数据\文化民俗\三亚文化民俗.md",
    }
    res = node(state)
    logger.info(json_format(res))