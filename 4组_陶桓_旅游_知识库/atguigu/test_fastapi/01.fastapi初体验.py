from fastapi import FastAPI
import uvicorn

app = FastAPI(
    title="测试",
    description="测试fastapi的小接口",
    version="0.1.0",
)


@app.get("/")
def index():
    return {"message": "hello world"}





if __name__ == "__main__":

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000
    )