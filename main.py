"""兼容旧入口：实际应用位于 ``app.main``。"""

from app.main import app

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
