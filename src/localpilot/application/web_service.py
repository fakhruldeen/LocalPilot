"""Web serving service for LocalPilot documentation, architecture, and model catalog."""

from __future__ import annotations
import functools
import http.server
import logging
import os
import socketserver
import threading
import webbrowser
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


def get_web_dir() -> Path:
    """Resolves the authoritative directory containing the website static assets."""
    # 1. Explicit environment variable override
    env_dir = os.environ.get("LOCALPILOT_WEB_DIR")
    if env_dir:
        p = Path(env_dir).resolve()
        if p.exists() and (p / "index.html").exists():
            return p

    # 2. Local working directory /web (development repo)
    cwd_web = (Path.cwd() / "web").resolve()
    if cwd_web.exists() and (cwd_web / "index.html").exists():
        return cwd_web

    # 3. Repository root relative to this package file
    repo_web = (Path(__file__).resolve().parent.parent.parent.parent / "web").resolve()
    if repo_web.exists() and (repo_web / "index.html").exists():
        return repo_web

    # 4. Packaged web directory inside src/localpilot/web
    pkg_web = (Path(__file__).resolve().parent.parent / "web").resolve()
    if pkg_web.exists() and (pkg_web / "index.html").exists():
        return pkg_web

    # Fallback to local 'web' path
    return cwd_web


class LocalPilotWebHandler(http.server.SimpleHTTPRequestHandler):
    """Custom HTTP handler serving the website from /web with clean headers."""

    def __init__(self, *args, directory: Optional[str] = None, **kwargs) -> None:
        web_dir = directory or str(get_web_dir())
        super().__init__(*args, directory=web_dir, **kwargs)

    def end_headers(self) -> None:
        # Enable CORS for local asset fetching (like data/models.json)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-cache, must-revalidate")
        super().end_headers()

    def log_message(self, format: str, *args) -> None:
        logger.debug("%s - - [%s] %s\n", self.client_address[0], self.log_date_time_string(), format % args)


class WebServer:
    """Manages the lifecycle of the local static website HTTP server."""

    def __init__(self, host: str = "127.0.0.1", port: int = 8000, web_dir: Optional[Path] = None) -> None:
        self.host = host
        self.port = port
        self.web_dir = web_dir or get_web_dir()
        self.server: Optional[http.server.ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    def start(self, blocking: bool = True, open_browser: bool = False) -> None:
        """Starts the static web server."""
        if not self.web_dir.exists() or not (self.web_dir / "index.html").exists():
            raise FileNotFoundError(f"Website directory not found at '{self.web_dir}'. Expected 'index.html'.")

        handler = functools.partial(LocalPilotWebHandler, directory=str(self.web_dir))
        self.server = http.server.ThreadingHTTPServer((self.host, self.port), handler)
        self.server.daemon_threads = True

        url = f"http://{self.host}:{self.port}/"
        if open_browser:
            try:
                webbrowser.open(url)
            except Exception:
                pass

        if blocking:
            try:
                self.server.serve_forever()
            finally:
                self.server.server_close()
        else:
            self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)
            self._thread.start()

    def stop(self) -> None:
        """Shuts down the web server."""
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.server = None
