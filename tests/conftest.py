import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


os.environ.setdefault("WEBUI_USERNAME", "admin")
os.environ.setdefault("WEBUI_PASSWORD", "test-password")
os.environ.setdefault("WEBUI_PROTOCOL", "http")
os.environ.setdefault("WEBUI_ACCESS_MODE", "all")
