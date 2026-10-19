"""Serves the built web workspace (``frontend/dist``) from the API's origin (ADR 0005).

Files that exist are served as-is; any other non-API path returns ``index.html`` so that
client-side routes such as ``/reviews/<id>`` (used in notification links) work on reload.
"""

from pathlib import Path

from fastapi import FastAPI, HTTPException, status
from fastapi.responses import FileResponse

# The SPA loads only its own bundled scripts and styles, and talks only to this origin.
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'; "
    "object-src 'none'"
)
_HTML_HEADERS = {
    "Content-Security-Policy": CONTENT_SECURITY_POLICY,
    "X-Frame-Options": "DENY",
    "Cache-Control": "no-cache",
}
_RESERVED = ("api", "health", "docs", "redoc", "openapi.json")


def mount_spa(app: FastAPI, static_dir: Path) -> None:
    root = static_dir.resolve()
    index = root / "index.html"
    if not index.is_file():
        raise ValueError(f"NEXT_ROLE_STATIC_DIR has no index.html: {root}")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> FileResponse:
        if path.split("/", 1)[0] in _RESERVED:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Not found")
        candidate = (root / path).resolve()
        if path and candidate.is_relative_to(root) and candidate.is_file():
            if candidate == index:
                return FileResponse(index, headers=_HTML_HEADERS)
            # Vite fingerprints everything under assets/, so it can be cached forever.
            cache = (
                "public, max-age=31536000, immutable" if path.startswith("assets/") else "no-cache"
            )
            return FileResponse(candidate, headers={"Cache-Control": cache})
        return FileResponse(index, headers=_HTML_HEADERS)
