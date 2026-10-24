# atguigu/import_process/nodes/node_pdf_to_md.py
import time
from pathlib import Path

from atguigu.config.config import MineruConfig
from atguigu.import_process.base import NodeBase
from atguigu.import_process.state import ImportGraphState
from atguigu.tool.json_format_tool import json_format
from atguigu.tool.logger import logger


def mineru_session():
    """访问 MinerU 时禁用系统代理。

    本机若开了 127.0.0.1:7890 这类代理，requests 会自动沿用，
    而 MinerU 的 CDN 走代理会 TLS 中断（SSL: UNEXPECTED_EOF_WHILE_READING）。
    """
    import requests

    session = requests.Session()
    session.trust_env = False
    return session


class NodePDFToMD(NodeBase):
    """
    PDF 转 Markdown 节点：PDF结构化解析
    """

    name = "node_pdf_to_md"

    def check_path(self,state):
        """
        校验state里面的文件名和地址是否正确
        :param state:
        :return:
        """
        pdf_path = state.get("pdf_path", "")
        local_dir = state.get("local_dir", "")
        # 判断文件是否存在,就是地址有没有给
        if not pdf_path:
            logger.error("文件路径为空")
            raise ValueError("pdf_path路径必须提供")

        pdf_path_obj = Path(pdf_path)
        # 判断文件地址是否正确,给了一个地址,但是地址是不是存在,有没有文件
        if not pdf_path_obj.exists():
            logger.error(f"文件{pdf_path}不存在")
            raise ValueError(f"文件{pdf_path}不存在")

        # 判断local_dir文件是否存在,不存在就去创建
        if not local_dir:
            logger.error("local_dir路径为空")
            raise ValueError("local_dir路径必须提供")

        local_dir_obj = Path(local_dir)
        # 如果提供了文件,但是查找没有这个文件,就去创建一个
        if not local_dir_obj.exists():
            local_dir_obj.mkdir(parents=True, exist_ok=True)

        return pdf_path,local_dir_obj,pdf_path_obj

    def upload_pdf(self,pdf_path,pdf_path_obj):
        """
        上传pdf文件到mineru
        :return:
        """
        token = MineruConfig.mineru_token
        session = mineru_session()
        url = "https://mineru.net/api/v4/file-urls/batch"
        header = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}"
        }
        data = {
            "files": [
                {"name": f"{pdf_path_obj.name}", "data_id": "abcd"}
            ],
            "model_version": "vlm"
        }
        file_path = [f"{pdf_path}"]
        # 以后只要是碰到发请求的逻辑，那么我们都要去三层判断考虑：
        # 1、考虑请求是否成功
        # 2、考虑请求数据是否成功
        # 3、考虑请求数据是否符合预期
        response = session.post(url, headers=header, json=data, timeout=60)
        if response.status_code != 200:
            logger.error("上传PDF文件请求失败")
            raise Exception(f"上传PDF文件请求失败：{pdf_path}")

        logger.info("上传PDF文件请求成功")
        result = response.json()
        if result["code"] != 0:
            logger.error("上传PDF文件请求数据失败")
            raise Exception(f"上传PDF文件请求数据失败")
        logger.info("上传PDF文件请求数据成功")
        batch_id = result["data"]["batch_id"]

        urls = result["data"]["file_urls"]

        for i in range(0, len(urls)):
            with open(file_path[i], 'rb') as f:
                res_upload = session.put(urls[i], data=f, timeout=300)
                if res_upload.status_code == 200:
                    logger.info(f"{urls[i]}上传成功")
                else:
                    logger.error(f"{urls[i]}上传失败")

        return batch_id

    def get_md_zip_url(self,batch_id):
        token = MineruConfig.mineru_token
        batch_id = batch_id
        session = mineru_session()
        url = f"https://mineru.net/api/v4/extract-results/batch/{batch_id}"
        header = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}"
        }

        total_time = 300  # 总时间
        use_time = 0
        while True:
            start_time = time.time()
            try:
                res = session.get(url, headers=header, timeout=30)
                if res.status_code != 200:
                    logger.error("获取PDF文件处理结果请求失败")
                    raise Exception(f"获取PDF文件处理结果请求失败")

                result = res.json()
                if result["code"] != 0:
                    logger.error("获取PDF文件处理结果请求数据失败")
                    raise Exception(f"获取PDF文件处理结果请求数据失败")
                data = result["data"]['extract_result'][0]
                if data['state'] != "done":
                    logger.info("PDF文件处理中")
                    raise Exception(f"PDF文件处理中尚未完成")

                zip_url = data['full_zip_url']
                return zip_url
            except Exception as e:
                logger.error(f"PDF文件处理异常，等待重试{e}")
                end_time = time.time()
                use_time += end_time - start_time
                if use_time > total_time:
                    raise Exception(f"PDF文件处理超时,请稍后再试")
                # 轮询间隔，避免高频请求把日志刷爆
                time.sleep(2)
                continue

    def download_zip_handler(self,zip_url,local_dir_obj,pdf_path_obj):
        """
        下载zip文件,将文件做处理,处理成特点的情况
        :return:
        """
        session = mineru_session()
        md_zip_content = None
        last_error = None
        # CDN 偶发 TLS 中断，重试三次
        for attempt in range(1, 4):
            try:
                md_zip_res = session.get(zip_url, timeout=300)
                if md_zip_res.status_code != 200:
                    raise Exception(f"HTTP {md_zip_res.status_code}")
                md_zip_content = md_zip_res.content
                break
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                logger.warning(f"下载结果包失败（第 {attempt} 次）：{type(exc).__name__}: {exc}")
                time.sleep(3)
        if md_zip_content is None:
            logger.error("下载PDF文件处理结果请求失败")
            raise Exception(f"下载PDF文件处理结果请求失败：{last_error}")
        #         我们获取到的是zip的内容，并不是直接变成zip文件，我们需要通过文件流操作把这个内容写入磁盘文件
        #         print(md_zip_content)

        #         构造下载的磁盘文件的路径
        md_zip_path_obj = local_dir_obj / f"{pdf_path_obj.stem}.zip"

        # 以后读写文件如果是读写二进制，不要加encoding="utf-8"  如果不是二进制就加
        with open(md_zip_path_obj, 'wb') as f:
            f.write(md_zip_content)

        #       解压zip文件
        import zipfile
        import shutil
        unzip_file_content = zipfile.ZipFile(md_zip_path_obj)
        #       解压到哪，构造解压的目的地 路径
        unzip_file_path_obj = local_dir_obj / f"{pdf_path_obj.stem}"

        #       判断解压的目录存在不存在，如果存在先删除，然后再创建
        if unzip_file_path_obj.exists():
            shutil.rmtree(unzip_file_path_obj)
        unzip_file_path_obj.mkdir(parents=True, exist_ok=True)

        #         真正的把解压的内容，放到这个目录
        unzip_file_content.extractall(unzip_file_path_obj)

        #       解压完成后，原本的md文件叫 full.md,我们需要重命名
        origin_md_path_obj = unzip_file_path_obj / "full.md"
        new_md_path_obj = origin_md_path_obj.with_name(f"{pdf_path_obj.stem}.md")  # 在内存当中改了，我们还得落盘
        origin_md_path_obj.rename(new_md_path_obj)

        # 读取Markdown文件内容 存储state
        with open(new_md_path_obj, 'r', encoding="utf-8") as f:
            md_content = f.read()
            return md_content,new_md_path_obj

    def process(self, state: ImportGraphState):
        """
        将传入的pdf文件利用mineru转换为md文件
        :param state:
        :return:
        """
        #第一步,判断state状态里面的文件都是否存在
        pdf_path,local_dir_obj,pdf_path_obj = self.check_path( state)

        #第二步,将文件上传至mineru,去解析 文件
        batch_id = self.upload_pdf(pdf_path,pdf_path_obj)


        #第三步,将文件解析成md文件,去网站获取文件的压缩包url
        zip_url=self.get_md_zip_url(batch_id)

        #第四步,通过获取的zip_url去下载压缩包,然后解压,获取md文件改名
        md_content, new_md_path_obj = self.download_zip_handler(zip_url,local_dir_obj,pdf_path_obj)
        return {
            "md_path": str(new_md_path_obj),
            "md_content": md_content
        }








if __name__ == "__main__":
    node = NodePDFToMD()
    state = {
        "pdf_path":r"C:\Users\th\Desktop\4组_陶桓_实战\4组_陶桓_旅游_知识库\output\20261021\hak180产品安全手册.pdf",
        "local_dir":r"D:\output\doc\parse"
    }
    res = node(state)
    logger.info(json_format(res))