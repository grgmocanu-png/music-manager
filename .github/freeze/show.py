import json, sys
tag, path = sys.argv[1], sys.argv[2]
try:
    d = json.load(open(path, encoding="utf-8"))
except Exception as e:
    print(f"::warning::{tag} NU EXISTĂ RAPORT: {e}"); sys.exit(0)
stacks = d.pop("stacks", [])
s = json.dumps(d, ensure_ascii=True)
for i in range(0, len(s), 1500):
    print(f"::warning::{tag} [{i//1500}] {s[i:i+1500]}")
for st, c in stacks[:4]:
    print(f"::warning::{tag} STACK {c}x {st[:900]}")
