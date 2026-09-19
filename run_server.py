import os
import uvicorn

if __name__ == "__main__":
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "7000"))
    uvicorn.run("app:app", host=host, port=port, reload=False)
