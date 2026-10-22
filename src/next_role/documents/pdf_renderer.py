"""PDF rendering with Chromium through Playwright (ADR 0007). Renderer image only.

The browser loads the bundled print page (``print.html``) from local files: every request is
answered by ``_route``, which serves files under the bundle directory and aborts everything
else, so no document content can make the browser reach the network. The display model and
tokens are passed in after load and rendered as text by React. Before printing, the page is
measured for content that overflows its box; any overflow fails QA instead of producing a
clipped PDF.
"""

import logging
import mimetypes
from importlib import metadata
from pathlib import Path
from typing import Any

from playwright.sync_api import Browser, Playwright, Route, sync_playwright

from next_role.documents import qa
from next_role.documents.display import DisplayDocument

logger = logging.getLogger(__name__)

ORIGIN = "http://print.invalid"

# Elements that extend past the document's right edge (clipped or overlapping text).
OVERFLOW_JS = """
() => {
  const doc = document.querySelector('.nr-doc');
  if (!doc) return 1;
  const edge = doc.getBoundingClientRect().right + 1;
  return [...doc.querySelectorAll('*')].filter((el) => el.getBoundingClientRect().right > edge)
    .length;
}
"""


class PdfRenderer:
    def __init__(self, timeout_seconds: float, bundle_dir: str) -> None:
        self.version = f"chromium-playwright-{metadata.version('playwright')}"
        self.timeout_ms = timeout_seconds * 1000
        self.root = Path(bundle_dir).resolve()
        if not (self.root / "print.html").is_file():
            raise FileNotFoundError(f"print.html not found in {self.root}")
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._overflow = 0

    def _ensure_browser(self) -> Browser:
        if self._browser is None or not self._browser.is_connected():
            if self._playwright is None:
                self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch(
                args=["--disable-gpu", "--no-first-run", "--disable-dev-shm-usage"]
            )
        return self._browser

    def _route(self, route: Route) -> None:
        url = route.request.url
        if not url.startswith(f"{ORIGIN}/"):
            route.abort()
            return
        relative = url[len(ORIGIN) + 1 :].split("?", 1)[0].split("#", 1)[0]
        path = (self.root / relative).resolve()
        if not path.is_relative_to(self.root) or not path.is_file():
            route.abort()
            return
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        route.fulfill(path=path, content_type=content_type)

    def render(self, document: DisplayDocument, tokens: dict[str, Any]) -> bytes:
        browser = self._ensure_browser()
        context = browser.new_context(
            java_script_enabled=True, service_workers="block", accept_downloads=False
        )
        try:
            context.set_default_timeout(self.timeout_ms)
            context.route("**/*", self._route)
            page = context.new_page()
            page.goto(f"{ORIGIN}/print.html", wait_until="load")
            page.wait_for_function("() => typeof window.renderDocument === 'function'")
            page.evaluate(
                "payload => window.renderDocument(payload)",
                {"document": document.model_dump(mode="json"), "tokens": tokens},
            )
            page.wait_for_function("() => document.body.dataset.ready === '1'")
            page.evaluate("() => document.fonts.ready.then(() => true)")
            page.emulate_media(media="print")
            self._overflow = int(page.evaluate(OVERFLOW_JS))
            margin = f"{tokens['margin_in']}in"
            return page.pdf(
                width=f"{tokens['page']['width_in']}in",
                height=f"{tokens['page']['height_in']}in",
                margin={"top": margin, "right": margin, "bottom": margin, "left": margin},
                print_background=True,
                tagged=True,
            )
        finally:
            context.close()

    def check(self, document: DisplayDocument, data: bytes, tokens: dict[str, Any]) -> qa.QAResult:
        result = qa.check_pdf(document, data, tokens)
        if self._overflow:
            result.passed = False
            result.warnings.append(f"{self._overflow} element(s) overflow their box")
        return result

    def close(self) -> None:
        if self._browser is not None:
            self._browser.close()
        if self._playwright is not None:
            self._playwright.stop()
