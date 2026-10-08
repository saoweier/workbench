"""Actionable preflight of actual imports and browser binary availability."""
from pathlib import Path
import importlib
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
for name in ["fastapi", "uvicorn", "sqlalchemy", "pydantic_settings", "httpx", "PIL", "numpy", "edge_tts", "imageio_ffmpeg", "playwright.sync_api"]:
    try: importlib.import_module(name)
    except ImportError as exc: raise SystemExit(f"缺少依赖 {name}：请运行 install.bat。{exc}")
from app.services.renderer import PlaywrightRenderer
from playwright.sync_api import sync_playwright
binary = PlaywrightRenderer.resolve_executable()
if not binary:
    with sync_playwright() as p: binary = p.chromium.executable_path
if not Path(binary).is_file():
    raise SystemExit("没有可用的浏览器。请安装 Microsoft Edge / Chrome，或运行 .venv\\Scripts\\python -m playwright install chromium。")
print("依赖已就绪；渲染浏览器：" + binary)

from app.services.video_speech import ffmpeg
if not Path(ffmpeg()).is_file():raise SystemExit("缺少视频编码器，请重新运行 install.bat。")
print("视频编码器已就绪。")
