from pathlib import Path


APP_PATH = Path("/app/app.py")
MARKER = "# cow-legacy app bootstrap imports"

BOOTSTRAP = """# cow-legacy app bootstrap imports
import os as _cow_os
import sys as _cow_sys

_cow_plugin_dir = "/app/plugins"
_cow_plugin_core = "/app/plugins_core"
if not _cow_os.path.exists(_cow_os.path.join(_cow_plugin_dir, "__init__.py")):
    _cow_sys.path.insert(0, _cow_plugin_core)
if "/app" not in _cow_sys.path:
    _cow_sys.path.insert(0, "/app")

from config import load_config, conf
from common.log import logger
import plugins as _cow_plugins
if hasattr(_cow_plugins, "__path__") and _cow_plugin_dir not in list(_cow_plugins.__path__):
    _cow_plugins.__path__.append(_cow_plugin_dir)
from plugins import *
try:
    PluginManager
except NameError:
    from plugins.plugin_manager import PluginManager
"""


text = APP_PATH.read_text()
if MARKER not in text:
    APP_PATH.write_text(BOOTSTRAP + "\n" + text)
