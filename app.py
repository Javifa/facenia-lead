"""
Punto de entrada principal de BuscaLeads · FACENIA.

Compatible simultáneamente con:
1. Despliegue serverless en Vercel (`@vercel/python`): exporta las variables WSGI de primer nivel
   `app` y `application` que sirven la aplicación móvil SPA + API REST (`vercel_web.py`).
2. Ejecución local con Streamlit (`streamlit run app.py` o `streamlit run streamlit_app.py`).
"""

import os
from pathlib import Path
from typing import Any, Callable, Dict, List

from vercel_web import wsgi_app


def app(environ: Dict[str, Any], start_response: Callable) -> List[bytes]:
    """Entrypoint WSGI de primer nivel requerido por Vercel Python Runtime."""
    return wsgi_app(environ, start_response)


application = app


def _is_streamlit_runtime() -> bool:
    if os.getenv("VERCEL") or os.getenv("VERCEL_ENV"):
        return False
    try:
        from streamlit.runtime.scriptrunner import get_script_run_ctx

        return get_script_run_ctx() is not None
    except Exception:
        return False


if _is_streamlit_runtime():
    _st_file = Path(__file__).resolve().parent / "streamlit_app.py"
    exec(compile(_st_file.read_text(encoding="utf-8"), str(_st_file), "exec"), globals())
