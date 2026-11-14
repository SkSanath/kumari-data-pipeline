"""POST signed JSON to the site's ingest endpoint (spec 2.3, 14). Stdlib only."""
import hashlib, hmac, json, os, sys, time, urllib.request
from datetime import datetime, timezone

def post(path: str, payload: dict) -> int:
    base, key = os.environ["INGEST_URL"].rstrip("/"), os.environ["INGEST_HMAC_KEY"]
    body = json.dumps(payload, separators=(",", ":"))
    ts = str(int(time.time() * 1000))
    sig = hmac.new(key.encode(), f"{ts}.{body}".encode(), hashlib.sha256).hexdigest()
    req = urllib.request.Request(f"{base}/{path}", data=body.encode(), method="POST",
        headers={"Content-Type": "application/json", "X-Timestamp": ts, "X-Signature": sig,
                 "User-Agent": "KumariLivePipeline/0.1"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.status

if __name__ == "__main__" and sys.argv[1:] == ["ping"]:
    print(post("ping", {"kind": "ping", "sentAt": datetime.now(timezone.utc).isoformat()}))
