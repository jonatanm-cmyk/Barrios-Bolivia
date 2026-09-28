"""Arranque local: python main.py  →  http://localhost:8000"""
from app.main import app  # noqa: F401 — lo usan uvicorn y Vercel

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
