import os, sys, requests, urllib.parse, json

BASE = "https://api.charitycommission.gov.uk/register/api"
KEY  = os.environ.get("CHARITY_API_KEY") or os.environ.get("CCEW_KEY")
assert KEY, "Set CHARITY_API_KEY (or CCEW_KEY) in your environment"

name = " ".join(sys.argv[1:]) or "WhiteChapel Centre"
q = urllib.parse.quote(name)
h = {"Ocp-Apim-Subscription-Key": KEY}

# search
u = f"{BASE}/searchCharityName/{q}"
r = requests.get(u, headers=h, timeout=15)
print("[SEARCH]", r.status_code, u)
r.raise_for_status()
payload = r.json()
print(json.dumps(payload if isinstance(payload, dict) else {"results":payload}, indent=2)[:1200], "...\n")

# pick first reg number if present and fetch details
items = payload.get("results") if isinstance(payload, dict) else payload
if items:
    reg = str(items[0].get("RegisteredNumber") or items[0].get("registeredNumber") or "").strip()
    if reg:
        durl = f"{BASE}/allcharitydetailsV2/{reg}/0"
        d = requests.get(durl, headers=h, timeout=15)
        print("[DETAILS]", d.status_code, durl)
        d.raise_for_status()
        print(json.dumps(d.json(), indent=2)[:1200], "...")
    else:
        print("No RegisteredNumber on first hit.")
else:
    print("No items returned.")