"""Entry point — run with: python run.py"""
import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "backend.main:app",
        host="0.0.0.0",
        port=8080,
        reload=False,
        log_level="info",
    )
