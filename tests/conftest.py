"""Config pytest commune — ajoute la racine du repo au sys.path pour que
`from modules.xxx import` et `from api import app` fonctionnent depuis tests/."""
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
