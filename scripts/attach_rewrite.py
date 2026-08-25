"""Attach the directory-URL rewrite to the CloudFront distribution → one command, repeatable.

    python3 -m scripts.attach_rewrite --profile karzat                  # create/update, publish, associate
    python3 -m scripts.attach_rewrite --profile karzat --dry-run        # say what it would do
    python3 -m scripts.attach_rewrite --profile karzat --wait           # block until the change is deployed

The problem this solves: S3 behind CloudFront serves exact object keys, so `/ckl43/javadalmazas/` was a 404
while `/ckl43/javadalmazas/index.html` was the page. Every link the site emits is explicit, so only hand-typed
and externally shortened addresses ever met the gap — but those are precisely the addresses a reader types.

The shape: one CloudFront Function (viewer-request, cloudfront-js-2.0) on the default cache behaviour.
A URI ending in `/` is REWRITTEN to `…/index.html` — the browser's address keeps the slash, so the page's
relative links resolve where they were written to resolve. A URI whose last segment has no extension is
REDIRECTED (301) to the slashed form instead of being rewritten: serving `/ckl43/javadalmazas` with the
content of `…/javadalmazas/index.html` would make the browser resolve `../../assets/…` one level too high
and break every relative link on the page. One extra round trip on the rare bare form is the honest price.
A missing directory still 404s: the rewrite produces a key that is not in the bucket, and the distribution's
custom error response turns that into the site's own 404 page, which stands at any depth by design.

The credentials come from a named profile in ~/.aws/credentials; nothing is read from the repository, and no
secret is ever printed. The function is idempotent about itself: an unchanged body is not re-published, an
existing association is left alone, and the script says which of the three steps it actually performed.
"""

from __future__ import annotations

import argparse
import sys
import time

FUNCTION_NAME = "karzat-index-rewrite"
FUNCTION_COMMENT = "karzat: a könyvtár-cím is oldal — / végű útvonalra index.html, perjel nélkülire 301"

# cloudfront-js-2.0. The querystring is re-serialized as received (CloudFront hands the values over still
# percent-encoded), so `/kereses?q=…` redirects to `/kereses/?q=…` with the query intact.
FUNCTION_CODE = """\
function handler(event) {
    var request = event.request;
    var uri = request.uri;
    if (uri.endsWith('/')) {
        request.uri = uri + 'index.html';
        return request;
    }
    var last = uri.split('/').pop();
    if (!last.includes('.')) {
        var parts = [];
        var keys = Object.keys(request.querystring);
        for (var i = 0; i < keys.length; i++) {
            var k = keys[i];
            var v = request.querystring[k];
            if (v.multiValue) {
                for (var j = 0; j < v.multiValue.length; j++) parts.push(k + '=' + v.multiValue[j].value);
            } else if (v.value === '') {
                parts.push(k);
            } else {
                parts.push(k + '=' + v.value);
            }
        }
        var qs = parts.length ? '?' + parts.join('&') : '';
        return {
            statusCode: 301,
            statusDescription: 'Moved Permanently',
            headers: { 'location': { value: uri + '/' + qs } }
        };
    }
    return request;
}
"""


def ensure_function(cfront, dry: bool) -> str:
    """Create or update the function, publish it when its LIVE stage is behind, return its ARN."""
    cfg = {"Comment": FUNCTION_COMMENT, "Runtime": "cloudfront-js-2.0"}
    code = FUNCTION_CODE.encode()
    try:
        live = cfront.get_function(Name=FUNCTION_NAME, Stage="LIVE")["FunctionCode"].read()
    except cfront.exceptions.NoSuchFunctionExists:
        live = None
    except Exception:
        live = b""                                             # exists but never published
    try:
        desc = cfront.describe_function(Name=FUNCTION_NAME)
        etag, arn = desc["ETag"], desc["FunctionSummary"]["FunctionMetadata"]["FunctionARN"]
        if live == code:
            print(f"function {FUNCTION_NAME}: unchanged and live")
            return arn
        if dry:
            print(f"function {FUNCTION_NAME}: would update and publish")
            return arn
        etag = cfront.update_function(Name=FUNCTION_NAME, IfMatch=etag,
                                      FunctionConfig=cfg, FunctionCode=code)["ETag"]
        cfront.publish_function(Name=FUNCTION_NAME, IfMatch=etag)
        print(f"function {FUNCTION_NAME}: updated and published")
        return arn
    except cfront.exceptions.NoSuchFunctionExists:
        if dry:
            print(f"function {FUNCTION_NAME}: would create and publish")
            return ""
        made = cfront.create_function(Name=FUNCTION_NAME, FunctionConfig=cfg, FunctionCode=code)
        cfront.publish_function(Name=FUNCTION_NAME, IfMatch=made["ETag"])
        print(f"function {FUNCTION_NAME}: created and published")
        return made["FunctionSummary"]["FunctionMetadata"]["FunctionARN"]


def ensure_association(cfront, dist_id: str, arn: str, dry: bool) -> bool:
    """Point the default cache behaviour's viewer-request at the function. True when a deploy was started."""
    got = cfront.get_distribution_config(Id=dist_id)
    dc, etag = got["DistributionConfig"], got["ETag"]
    beh = dc["DefaultCacheBehavior"]
    items = (beh.get("FunctionAssociations") or {}).get("Items") or []
    kept = [a for a in items if a["EventType"] != "viewer-request"]
    cur = next((a["FunctionARN"] for a in items if a["EventType"] == "viewer-request"), None)
    if cur == arn:
        print(f"distribution {dist_id}: association already in place")
        return False
    if dry:
        print(f"distribution {dist_id}: would associate viewer-request → {FUNCTION_NAME}"
              + (f" (replacing {cur})" if cur else ""))
        return False
    new = kept + [{"FunctionARN": arn, "EventType": "viewer-request"}]
    beh["FunctionAssociations"] = {"Quantity": len(new), "Items": new}
    cfront.update_distribution(Id=dist_id, IfMatch=etag, DistributionConfig=dc)
    print(f"distribution {dist_id}: viewer-request associated, deploying")
    return True


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile", default=None, help="a named profile from ~/.aws/credentials")
    ap.add_argument("--distribution", default="E18RUGR14RBPWS")
    ap.add_argument("--region", default="us-east-1", help="CloudFront is a global service homed here")
    ap.add_argument("--wait", action="store_true", help="poll until the distribution reports Deployed")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    import boto3

    sess = boto3.Session(profile_name=args.profile, region_name=args.region) if args.profile \
        else boto3.Session(region_name=args.region)
    cfront = sess.client("cloudfront")
    arn = ensure_function(cfront, args.dry_run)
    started = arn and ensure_association(cfront, args.distribution, arn, args.dry_run)
    if started and args.wait:
        t0 = time.time()
        while True:
            status = cfront.get_distribution(Id=args.distribution)["Distribution"]["Status"]
            if status == "Deployed":
                print(f"deployed in {(time.time() - t0) / 60:.1f} min")
                break
            print(f"  {status} · {(time.time() - t0) / 60:.1f} min", flush=True)
            time.sleep(30)
    if not args.dry_run:
        print("\ncheck (both spellings should answer, missing paths should still 404):\n"
              "  curl -s -o /dev/null -w '%{http_code}\\n' https://ogykarzat.hu/ckl43/javadalmazas/\n"
              "  curl -s -o /dev/null -w '%{http_code} %{redirect_url}\\n' https://ogykarzat.hu/ckl43/javadalmazas\n"
              "  curl -s -o /dev/null -w '%{http_code}\\n' https://ogykarzat.hu/ckl43/nincs/ilyen/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
