# tests/test_main_module.py
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Load jarvis/main.py the way `python -m jarvis.main` does (as __main__), minus the
# final main() call, then import it the way web.py / wake.py do.
_SCRIPT = r'''
import re, sys, types
from pathlib import Path
src = Path("jarvis/main.py").read_text(encoding="utf-8")
src = re.sub(r'if __name__ == "__main__":\s*\n\s*main\(\)\s*$', "pass\n", src)
m = types.ModuleType("__main__")
m.__file__ = str(Path("jarvis/main.py").resolve())
sys.modules["__main__"] = m
exec(compile(src, m.__file__, "exec"), m.__dict__)

import jarvis.main
from jarvis.main import abort_all
from jarvis import stt
assert jarvis.main is m and abort_all is m.abort_all, "web.py/wake.py would get a second copy of jarvis.main"
assert stt._abort_event is m._abort, "recorder watches a different abort flag than the voice loop"
print("ONE-MODULE-OK")
'''


def test_script_run_shares_one_main_module():
    """Running as a script must not create a second jarvis.main (stale abort flag bug)."""
    out = subprocess.run([sys.executable, "-c", _SCRIPT], cwd=ROOT, capture_output=True,
                         text=True, timeout=300)
    assert "ONE-MODULE-OK" in out.stdout, out.stdout[-2000:] + out.stderr[-4000:]
