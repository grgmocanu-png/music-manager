import json, sys
tag, path = sys.argv[1], sys.argv[2]
try:
    d = json.load(open(path, encoding="utf-8"))
except Exception as e:
    print(f"::warning::{tag} NU EXISTĂ RAPORT: {e}"); sys.exit(0)
for k, v in d.items():
    s = json.dumps(v, ensure_ascii=False)
    print(f"::warning::{tag} {k} = {s[:900]}")
