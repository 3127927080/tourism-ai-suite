"""
写的是文件导入部分的接口
"""

import shutil
import uuid
from pathlib import Path
from datetime import datetime

import fastapi
from fastapi import FastAPI, BackgroundTasks, UploadFile, File
from starlette.middleware.cors import CORSMiddleware

from atguigu.config.config import MinIoConfig
from atguigu.import_process.import_main_graph import MainGraphRunner
from atguigu.tool.logger import logger
from atguigu.tool.minio_client_tool import  get_minio_client
from atguigu.tool.task_utils import add_running_task, add_done_task, get_task_info, update_task_status, \
    TASK_STATUS_PROCESSING, TASK_STATUS_COMPLETED, TASK_STATUS_FAILED

app = FastAPI(
    title="导入接口",
    description="掌柜智库项目的导入部分的api接口",
    version="0.1.0",
)


# 2. 解决跨域
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 允许的源
    allow_credentials=True,  # 允许携带cookie
    allow_methods=["*"],  # 允许的请求方法
    allow_headers=["*"],  # 允许的请求头
)
@app.post("/upload")
async def upload_file(background_tasks: BackgroundTasks,file: UploadFile = File(...)):
    """
    接收前端传送过来的文件,进行保存,返回task_id
    :param background_tasks: 后台任务
    :param file: 前端传回来的文件
    :return: 返回这个后台任务对应的任务id
    """
    #1. 生成全局唯一的 `task_id`。
    task_id=str(uuid.uuid4())

    #上传文件追踪的字典, 本质就是把upload_file放入正在执行的列表的中
    add_running_task(task_id, "upload_file")

    #2. 将文件保存到服务器临时目录。
    #2.1定义文件要存放的位置
    local_dir = rf"D:\output\{datetime.now().strftime('%Y%m%d')}"
    #2.2将文件转为path对象,方便操作
    local_dir_obj = Path(local_dir)
    #2.3判断文件是否存在,不存在则创建
    if not local_dir_obj.exists():
        local_dir_obj.mkdir(parents=True,exist_ok=True)
    #2.4存在则先拼接文件路径
    local_file_path = str(local_dir_obj / file.filename)

    #为了让前端的显示实时的进度,维护了三个状态字典,正在进行的节点字典,完成的节点字典,耗时的字典


    #2.5开始正式的保存文件
    with open(local_file_path,"wb") as f :
        shutil.copyfileobj(file.file, f, 1024*1024)
    logger.info(f"文件保存本地成功,地址是:{local_file_path}")

    #3. 上传文件到 MinIO 对象存储。
    #3.1获取minio的客户端
    minio_client = get_minio_client()
    #3.2调用minio客户端的添加文件方法
    #对储存结构的优化,在uuid的前9位后面加上要保存的文件的文件名,增加辨识度
    file_path = task_id[:13]+Path(local_file_path).name
    minio_client.fput_object(
        bucket_name=MinIoConfig.minio_bucket_name,
        object_name=f"pdf_file/{datetime.now().strftime('%Y%m%d')}/{file_path}/{file.filename}",
        file_path=local_file_path
    )
    logger.info(f"文件上传到minio成功，保存路径为：pdf_file/{datetime.now().strftime('%Y%m%d')}/{task_id}/{file.filename}")

    # 更新上传文件的状态追踪
    add_done_task(task_id, "upload_file")  # 本质就是把upload_file从正在执行的列表中移除，添加到已完成列表中

    #4. 启动后台任务开始处理
    background_tasks.add_task(run_main_graph,task_id=task_id,local_dir=local_dir,local_file_path=local_file_path)

    return {"task_id": task_id}




@app.get("/status/{task_id}")
async def get_status(task_id: str = fastapi.Path(...,description="任务ID")):
    return get_task_info(task_id)

#定义后台任务,用来启动import模块的graph
def run_main_graph(task_id,local_dir,local_file_path):
    """
    用来启动import的graph的后台任务函数
    :param task_id: 本次任务的对应id,用于追踪
    :param local_dir: 文件的操作路径
    :param local_file_path: 文件的保存路径
    :return:
    """
    #1.定义初始化的状态字典
    try:
        init_state = {
            "task_id": task_id,
            "local_dir": local_dir,
            "local_file_path": local_file_path
        }
        #将字典传给graph的启动入口
        update_task_status(task_id, TASK_STATUS_PROCESSING)
        MainGraphRunner.create_and_run(init_state)
        update_task_status(task_id, TASK_STATUS_COMPLETED)
    except Exception as e:
        logger.error(f"执行graph异常，task_id={task_id}")
        update_task_status(task_id, TASK_STATUS_FAILED)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8004)