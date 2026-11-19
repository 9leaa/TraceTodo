"""One-command launcher for Trace-Memory Web & PWA Server (supports `--lan` for iPhone access)."""

from __future__ import annotations

import argparse
from pathlib import Path
import socket
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from trace_memory.api.app import run_web_server  # noqa: E402
from trace_memory.config import TraceMemoryConfig  # noqa: E402
from trace_memory.container import AppContainer  # noqa: E402


def _detect_lan_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return str(s.getsockname()[0])
    except Exception:
        return "127.0.0.1"


def main() -> None:
    parser = argparse.ArgumentParser(description="Start Trace-Memory Web & iPhone PWA Server")
    parser.add_argument(
        "--lan",
        action="store_true",
        help="Bind 0.0.0.0 so iPhone on the same Wi-Fi can connect via LAN IP",
    )
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()

    cfg = TraceMemoryConfig.from_env(dotenv_path=ROOT / ".env")
    host = "0.0.0.0" if args.lan else cfg.web_host
    port = args.port or cfg.web_port

    if args.lan:
        lan_ip = _detect_lan_ip()
        print(f"📱 iPhone 同一 Wi-Fi 访问地址 (Safari 打开后点「分享 -> 添加到主屏幕」): http://{lan_ip}:{port}")
    print(f"💻 Mac 本机访问地址: http://127.0.0.1:{port}")

    container = AppContainer.build(cfg)
    try:
        run_web_server(container, host=host, port=port)
    finally:
        container.close()


if __name__ == "__main__":
    main()
