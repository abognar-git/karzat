"""Publish site/ to S3 behind CloudFront, from the committed build → one command, repeatable.

    python3 -m scripts.deploy_aws --profile karzat --bucket karzat-hu            # create/refresh and upload
    python3 -m scripts.deploy_aws --profile karzat --bucket karzat-hu --dry-run  # say what it would do
    python3 -m scripts.deploy_aws --profile karzat --bucket karzat-hu --only ckl43,index.html,assets

The shape: a private bucket (no public access at all), a CloudFront distribution reading it through an Origin
Access Control, TLS on CloudFront's own certificate, and `index.html` as the default root object. Nothing about the
site is dynamic, so this is the whole architecture.

Uploads are incremental: an object is sent only when its size or its MD5 differs from what the bucket holds, so a
rebuild that changes a thousand pages uploads a thousand pages, not a hundred thousand. Content types and cache
headers are set per extension — HTML short-lived (the site is rebuilt when parliament sits), data files long.

The credentials come from a named profile in ~/.aws/credentials; nothing is read from the repository, and no
secret is ever printed.
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import hashlib
import mimetypes
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site"

CONTENT_TYPE = {
    ".html": "text/html; charset=utf-8", ".json": "application/json; charset=utf-8",
    ".xml": "application/atom+xml; charset=utf-8", ".csv": "text/csv; charset=utf-8",
    ".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8",
    ".txt": "text/plain; charset=utf-8", ".svg": "image/svg+xml",
    ".webp": "image/webp", ".png": "image/png", ".jpg": "image/jpeg", ".ico": "image/x-icon",
}
# HTML is short-lived (the site is rebuilt when parliament sits) and the feeds shorter still; a portrait is the same
# picture for as long as the person has the same face, so it is cached for a month rather than a day.
# The stylesheet and the script are versionless names, so a long cache means a reader can hold yesterday's CSS over
# today's HTML — which is how a chart drawn with new class names came to render as black rectangles for a day. They
# are small and CloudFront answers the revalidation, so they follow the HTML's own short life instead.
CACHE = {".html": "public, max-age=300", ".css": "public, max-age=300", ".js": "public, max-age=300",
         ".xml": "public, max-age=900",
         ".webp": "public, max-age=2592000", ".png": "public, max-age=2592000",
         ".jpg": "public, max-age=2592000", ".ico": "public, max-age=2592000"}
CACHE_DEFAULT = "public, max-age=86400"


def md5(path: Path) -> str:
    h = hashlib.md5()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def ensure_bucket(s3, bucket: str, region: str, dry: bool) -> None:
    try:
        s3.head_bucket(Bucket=bucket)
        print(f"bucket {bucket}: exists")
        return
    except Exception:
        pass
    if dry:
        print(f"bucket {bucket}: would create in {region}")
        return
    kw = {"Bucket": bucket}
    if region != "us-east-1":
        kw["CreateBucketConfiguration"] = {"LocationConstraint": region}
    s3.create_bucket(**kw)
    s3.put_public_access_block(Bucket=bucket, PublicAccessBlockConfiguration={
        "BlockPublicAcls": True, "IgnorePublicAcls": True, "BlockPublicPolicy": False, "RestrictPublicBuckets": False})
    print(f"bucket {bucket}: created in {region} (private; CloudFront reads it through an OAC)")


def ensure_distribution(cfront, s3, bucket: str, region: str, dry: bool) -> tuple[str, str]:
    """(distribution id, domain) — reused when one already points at this bucket."""
    origin_domain = f"{bucket}.s3.{region}.amazonaws.com"
    for d in (cfront.list_distributions().get("DistributionList", {}).get("Items") or []):
        for o in d["Origins"]["Items"]:
            if o["DomainName"] == origin_domain:
                print(f"distribution {d['Id']}: exists ({d['DomainName']})")
                return d["Id"], d["DomainName"]
    if dry:
        print(f"distribution: would create for {origin_domain}")
        return "", ""
    oac_name = f"{bucket}-oac"
    oac_id = None
    for o in (cfront.list_origin_access_controls().get("OriginAccessControlList", {}).get("Items") or []):
        if o["Name"] == oac_name:
            oac_id = o["Id"]
    if not oac_id:
        oac_id = cfront.create_origin_access_control(OriginAccessControlConfig={
            "Name": oac_name, "Description": f"karzat → {bucket}", "SigningProtocol": "sigv4",
            "SigningBehavior": "always", "OriginAccessControlOriginType": "s3"})["OriginAccessControl"]["Id"]
    dist = cfront.create_distribution(DistributionConfig={
        "CallerReference": f"karzat-{bucket}-{int(time.time())}",
        "Comment": "karzat — az Országgyűlés szavazásai",
        "Enabled": True, "DefaultRootObject": "index.html", "PriceClass": "PriceClass_100",
        "Origins": {"Quantity": 1, "Items": [{
            "Id": "s3", "DomainName": origin_domain, "OriginAccessControlId": oac_id,
            "S3OriginConfig": {"OriginAccessIdentity": ""}}]},
        "DefaultCacheBehavior": {
            "TargetOriginId": "s3", "ViewerProtocolPolicy": "redirect-to-https",
            "AllowedMethods": {"Quantity": 2, "Items": ["GET", "HEAD"],
                               "CachedMethods": {"Quantity": 2, "Items": ["GET", "HEAD"]}},
            "Compress": True,
            "CachePolicyId": "658327ea-f89d-4fab-a63d-7e88639e58f6",          # Managed-CachingOptimized
        },
        "CustomErrorResponses": {"Quantity": 1, "Items": [
            {"ErrorCode": 403, "ResponseCode": "404", "ResponsePagePath": "/404.html", "ErrorCachingMinTTL": 300}]},
    })["Distribution"]
    # let only this distribution read the bucket
    policy = ('{"Version":"2012-10-17","Statement":[{"Sid":"AllowCloudFront","Effect":"Allow",'
              '"Principal":{"Service":"cloudfront.amazonaws.com"},"Action":"s3:GetObject",'
              f'"Resource":"arn:aws:s3:::{bucket}/*",'
              f'"Condition":{{"StringEquals":{{"AWS:SourceArn":"{dist["ARN"]}"}}}}}}]}}')
    s3.put_bucket_policy(Bucket=bucket, Policy=policy)
    print(f"distribution {dist['Id']}: created ({dist['DomainName']}), bucket policy scoped to it")
    return dist["Id"], dist["DomainName"]


def remote_index(s3, bucket: str) -> dict[str, tuple[int, str]]:
    out: dict[str, tuple[int, str]] = {}
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket):
        for o in page.get("Contents") or []:
            out[o["Key"]] = (o["Size"], o["ETag"].strip('"'))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile", required=True)
    ap.add_argument("--bucket", required=True)
    ap.add_argument("--region", default="eu-central-1")
    ap.add_argument("--only", help="comma-separated top-level names to upload (default: everything)")
    ap.add_argument("--workers", type=int, default=24)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-invalidate", action="store_true")
    args = ap.parse_args(argv)
    import boto3

    sess = boto3.Session(profile_name=args.profile, region_name=args.region)
    s3, cfront = sess.client("s3"), sess.client("cloudfront")
    ensure_bucket(s3, args.bucket, args.region, args.dry_run)
    dist_id, domain = ensure_distribution(cfront, s3, args.bucket, args.region, args.dry_run)

    roots = [SITE / n for n in args.only.split(",")] if args.only else [SITE]
    files = [p for r in roots for p in ([r] if r.is_file() else r.rglob("*")) if p.is_file()]
    print(f"local: {len(files):,} files, {sum(p.stat().st_size for p in files) / 1e9:.2f} GB")
    have = {} if args.dry_run else remote_index(s3, args.bucket)
    print(f"remote: {len(have):,} objects already there")

    todo = []
    for p in files:
        key = str(p.relative_to(SITE))
        size = p.stat().st_size
        cur = have.get(key)
        if cur and cur[0] == size and "-" not in cur[1] and cur[1] == md5(p):
            continue
        todo.append((p, key))
    print(f"to upload: {len(todo):,} objects ({sum(p.stat().st_size for p, _ in todo) / 1e9:.2f} GB)")
    if args.dry_run or not todo:
        return 0

    done = [0]
    t0 = time.time()
    def put(job):
        p, key = job
        ext = p.suffix.lower()
        extra = {"ContentType": CONTENT_TYPE.get(ext, mimetypes.guess_type(p.name)[0] or "application/octet-stream"),
                 "CacheControl": CACHE.get(ext, CACHE_DEFAULT)}
        if ext == ".gz":
            extra.update({"ContentEncoding": "gzip", "ContentType": "text/csv; charset=utf-8"})
        s3.upload_file(str(p), args.bucket, key, ExtraArgs=extra)
        done[0] += 1
        if done[0] % 2000 == 0:
            el = time.time() - t0
            print(f"  {done[0]:,}/{len(todo):,} · {el/60:.1f} min · ~{(el/done[0])*(len(todo)-done[0])/60:.0f} min left", flush=True)

    with cf.ThreadPoolExecutor(max_workers=args.workers) as ex:
        list(ex.map(put, todo))
    print(f"uploaded {done[0]:,} objects in {(time.time() - t0)/60:.1f} min")

    if dist_id and not args.no_invalidate:
        cfront.create_invalidation(DistributionId=dist_id, InvalidationBatch={
            "Paths": {"Quantity": 1, "Items": ["/*"]}, "CallerReference": f"karzat-{int(time.time())}"})
        print("cache invalidation requested (/*)")
    if domain:
        print(f"\nlive at: https://{domain}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
