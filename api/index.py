# Punto de entrada de Vercel. Toda la lógica vive en app/: no duplicar código aquí.
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.main import app  # noqa: E402,F401
