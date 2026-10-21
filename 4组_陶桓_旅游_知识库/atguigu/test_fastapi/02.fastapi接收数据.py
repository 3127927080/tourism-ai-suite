import os
import shutil
import uuid

from fastapi import FastAPI, UploadFile
from fastapi.params import Path, Query
from pydantic import BaseModel, Field
from fastapi import Request


app = FastAPI(
    title="测试2",
    description="测试fastapi接收数据的接口",
    version="0.1.0",
)

#1.第一个,接收路径参数
@app.get("/testpath/{id}/{name}/{age}")
def testpath(
        id: int = Path(..., description="用户id"),
        name: str = Path(..., description="用户名"),
        age: int = Path(..., description="用户年龄")
):
    return {"id": id, "name": name, "age": age}

#2.第二个,接收查询参数
@app.get("/testquery")
def testquery(
        id: int = Query(..., description="用户id"),
        name: str = Query(default="杨幂", description="用户名"),
        age: int = Query(default=18, description="用户年龄")
):
    return {"id": id, "name": name, "age": age}




class User(BaseModel):
    username: str = Field(title="用户名", description="用户名")
    age: int = Field(title="年龄", description="年龄",default=18)
#3.第三个,接收body参数
@app.post("/testbody")
def testbody(user:User):
    return user

#4.第四个,接受混合参数
@app.post("/testmiedx/{id}")
def testmiedx(
        user:User,
        id:int = Path(description="用户id"),
        name:str =Query(...,description="客户姓名"),
        age:int = Query(...,description="年龄")
):
    user_dict = user.model_dump()
    return {"id": id, "name": name, "user": user, "age": age}

#5.第五种,接收请求头信息
@app.get("/testheader")
def testheader(request:Request):
    token = request.headers.get("token")
    return {"token": token}

#6.第六种,文件上传
@app.post("/testupload")
def testupload(file:UploadFile):
    print(file.filename)
    print(file.size)
    print(file.content_type)
    print(file.file)
    print(file.headers)
    file_name = str(uuid.uuid4())[:8] + file.filename
    dir_path = "./images"
    if not os.path.exists(dir_path):
        os.makedirs(dir_path)
    fs = open(f"./{dir_path}/{file_name}", "wb")
    shutil.copyfileobj(file.file,fs , length=1024*1024)
    return {"filename":file.filename}




if __name__ == '__main__':
    import uvicorn
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000
    )