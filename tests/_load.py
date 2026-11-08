"""Import the plugin directory as a package named `hermes_wrapped` (its folder name has a dash)."""

import importlib.util
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent


def load():
    if "hermes_wrapped" in sys.modules:
        return sys.modules["hermes_wrapped"]
    spec = importlib.util.spec_from_file_location(
        "hermes_wrapped", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)]
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["hermes_wrapped"] = mod
    spec.loader.exec_module(mod)
    return mod
