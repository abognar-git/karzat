"""Put a Content-Security-Policy and the rest of the security headers on the distribution.

This script spent its first weeks outside the repository. The policy was live, the README explained it at
length, and a clone could not have reproduced it — the one piece of the deployment that decides what a
reader's browser is allowed to do existed only on the machine that had run it once.

    python3 apply_csp.py --check
    python3 apply_csp.py

The policy is strict because a scan of the built site earned it: across six thousand pages there is not one
off-origin image, stylesheet or script. Every parlament.hu and wikidata URL is an <a href>, which CSP does not
govern. So the default can be `'none'` and each capability granted only where it is actually used, rather than
the usual `default-src 'self'` shrug.

Three grants are worth their explanation, because each is a hole somebody could widen later without noticing:

  `'wasm-unsafe-eval'` — the SQL page compiles WebAssembly. This is the narrow modern grant, not `'unsafe-eval'`,
  which would also re-open eval() and new Function() for every page on the site.
  `worker-src 'self' blob:` — DuckDB runs in a worker. Ours is a same-origin file, but the library also creates
  blob workers internally, and a blocked worker fails as an opaque WebAssembly error rather than a CSP message.
  `style-src 'unsafe-inline'` — the pages carry inline style attributes. Style injection is a far smaller matter
  than script injection, and removing them is a refactor rather than a security fix; it is recorded as a
  concession rather than hidden.

`report-uri` is deliberately absent. There is nowhere to send reports that would not be a third party, and this
site does not call third parties.
"""
from __future__ import annotations

import argparse
import sys
import time

import boto3

DIST = "E18RUGR14RBPWS"
NAME = "karzat-security-headers"

CSP = "; ".join([
    "default-src 'none'",
    "script-src 'self' 'wasm-unsafe-eval'",
    "worker-src 'self' blob:",
    "style-src 'self' 'unsafe-inline'",
    # the portraits are local; the remote original stays allowed because portrait_html() falls back to it for a
    # member the render manifest does not know yet, and a blocked image would look like a missing person
    # data: is here for one reason: rasterising a chart to PNG in the browser has exactly one route, an
    # <img> whose src is the serialised SVG, and img-src governs it. Without this the export button fails
    # silently on the live site and works in local preview, where no CSP header is served — which is how it
    # shipped unnoticed once already (the Riport page's own PNG button has never worked in production).
    # It admits no network origin and no script; an <img> cannot execute a data: SVG.
    "img-src 'self' data: https://www.parlament.hu",
    "connect-src 'self'",
    "font-src 'self'",
    "form-action 'self'",
    "base-uri 'none'",
    "frame-ancestors 'none'",
    "object-src 'none'",
    "upgrade-insecure-requests",
])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default="karzat")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    cf = boto3.Session(profile_name=a.profile, region_name="eu-central-1").client("cloudfront")

    cfg = {
        "Name": NAME,
        "Comment": "karzat: strict CSP plus the standard headers",
        "SecurityHeadersConfig": {
            "ContentSecurityPolicy": {"ContentSecurityPolicy": CSP, "Override": True},
            "ContentTypeOptions": {"Override": True},
            "FrameOptions": {"FrameOption": "DENY", "Override": True},
            "ReferrerPolicy": {"ReferrerPolicy": "no-referrer", "Override": True},
            "StrictTransportSecurity": {"AccessControlMaxAgeSec": 31536000, "IncludeSubdomains": True,
                                        "Preload": False, "Override": True},
            "XSSProtection": {"Protection": True, "ModeBlock": True, "Override": True},
        },
    }
    if a.check:
        print(CSP.replace("; ", ";\n  "))
        return 0

    existing = next((p for p in cf.list_response_headers_policies()["ResponseHeadersPolicyList"]["Items"]
                     if p["ResponseHeadersPolicy"]["ResponseHeadersPolicyConfig"]["Name"] == NAME), None)
    if existing:
        pid = existing["ResponseHeadersPolicy"]["Id"]
        etag = cf.get_response_headers_policy(Id=pid)["ETag"]
        cf.update_response_headers_policy(Id=pid, IfMatch=etag, ResponseHeadersPolicyConfig=cfg)
        print(f"policy {pid}: updated")
    else:
        pid = cf.create_response_headers_policy(ResponseHeadersPolicyConfig=cfg)["ResponseHeadersPolicy"]["Id"]
        print(f"policy {pid}: created")

    for attempt in range(6):
        c = cf.get_distribution_config(Id=DIST)
        etag, d = c["ETag"], c["DistributionConfig"]
        if d["DefaultCacheBehavior"].get("ResponseHeadersPolicyId") == pid:
            print("distribution: already attached")
            return 0
        d["DefaultCacheBehavior"]["ResponseHeadersPolicyId"] = pid
        try:
            cf.update_distribution(Id=DIST, IfMatch=etag, DistributionConfig=d)
            print("distribution: policy attached — a few minutes to deploy")
            return 0
        except cf.exceptions.PreconditionFailed:
            print(f"  ETag moved (attempt {attempt + 1})")
            time.sleep(15)
    return 1


if __name__ == "__main__":
    sys.exit(main())
