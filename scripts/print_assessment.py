"""Pretty-print an assessment JSON response read from stdin (used by scripts/demo.sh)."""

import json
import sys

body = json.load(sys.stdin)
a = body.get("assessment", body)
print(
    f"score={a['score']} (excluding unknowns {a['score_excluding_unknowns']}) "
    f"coverage={a['coverage']} tier={a['tier']} blocking_gaps={a['has_blocking_gaps']}"
)
for r in a["requirements"]:
    print(f"  [{r['status']:>13}] ({r['importance']}) {r['text']}")
    print(f"                  {r['explanation']}")
for c in a["preference_checks"]:
    print(f"  preference {c['name']}: {c['status']} - {c['explanation']}")
print(a["disclaimer"])
