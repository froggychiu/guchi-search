"""Every ingest CLI flag must reach a handler.

Run with:  python backend/tests/test_cli.py

Written after three action branches — --apply-vocab, --mine-vocab and
--scan-hallucinations — were deleted from ingest.py by a careless edit. The
flags kept parsing, so `--mine-vocab` silently fell through to the default
path and ran a full ingest instead. The maintenance API happily reported
"started", and the only clue was output belonging to a different job.

Argparse cannot catch this: a declared flag with no `if args.x:` is valid
Python. So compare the two lists directly.
"""

import ast
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
INGEST = os.path.join(os.path.dirname(HERE), "app", "scripts", "ingest.py")

PASS, FAIL = "\033[32m  PASS\033[0m", "\033[31m  FAIL\033[0m"
failures = []


def check(label, got, want):
    ok = got == want
    print(f"{PASS if ok else FAIL}  {label}: got {got!r}, want {want!r}")
    if not ok:
        failures.append(label)


source = open(INGEST, encoding="utf-8").read()
tree = ast.parse(source)

# Flags declared via parser.add_argument("--x", action="store_true").
declared = set()
for node in ast.walk(tree):
    if not (isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "add_argument"):
        continue
    if not (node.args and isinstance(node.args[0], ast.Constant)):
        continue
    name = node.args[0].value
    if not isinstance(name, str) or not name.startswith("--"):
        continue
    is_flag = any(kw.arg == "action" and kw.value.value == "store_true"
                  for kw in node.keywords
                  if isinstance(getattr(kw, "value", None), ast.Constant))
    if is_flag:
        declared.add(name[2:].replace("-", "_"))

# Flags actually branched on somewhere in the file.
handled = set(re.findall(r"if args\.(\w+)\b", source))

# Modifiers, not actions: they change how another flag behaves rather than
# selecting a job of their own.
MODIFIERS = {"dry_run"}

print("--- every action flag has a handler ---")
print(f"       declared: {sorted(declared)}")
check("no flag is left unhandled",
      sorted(declared - handled - MODIFIERS), [])
check("no handler references an undeclared flag",
      sorted(handled - declared - {"episode_id", "limit", "show", "min_evidence",
                                   "replace_text", "dry_run"}), [])

print("\n--- the module still imports ---")
sys.path.insert(0, os.path.dirname(HERE))
os.environ.setdefault("GUCHI_DATABASE_URL", "sqlite+aiosqlite:///:memory:")
try:
    import app.scripts.ingest as ingest_module  # noqa: F401
    check("import succeeds", True, True)
except Exception as e:  # pragma: no cover
    check("import succeeds", f"{type(e).__name__}: {e}", True)

print("\n" + "=" * 60)
if failures:
    print(f"\033[31m{len(failures)} FAILURES: {failures}\033[0m")
    sys.exit(1)
print("\033[32mall CLI checks passed\033[0m")
