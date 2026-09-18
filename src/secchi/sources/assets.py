"""Work out whether TEON's field-camera images are reachable over HTTPS.

TEON stores each capture as an ``s3://`` object reference:

    s3://teon-loggernet-data-storage/Glenbrook 2 - Terrestrial/Snow photos/Glenbrook2Photo524.jpg

A browser cannot load that scheme, so 3,378 indexed frames are currently
undisplayable. But an S3 object has several possible HTTPS forms, and
whether any of them works depends on the bucket's public-access settings —
which is a question to answer by asking, not by guessing.

This module generates the candidate forms and tests them. If one returns an
image, the pattern goes into ``USGS``-style config and the dashboard can
build a snowpack time-lapse from November 2025 onward. If none do, the
bucket is private and the only routes are a TEON-side proxy endpoint or
asking them to relax the policy — and we'll know that rather than guessing.
"""

from __future__ import annotations

import logging
from urllib.parse import quote

import httpx

from secchi.config import HTTP_TIMEOUT_SECONDS, USER_AGENT

log = logging.getLogger("secchi.sources.assets")

# Regions to try. TEON's API runs on App Runner in us-west-2, so the bucket
# is most likely there; us-east-1 is the S3 default and worth a shot.
CANDIDATE_REGIONS = ("us-west-2", "us-east-1")


def parse_s3_uri(uri: str) -> tuple[str, str] | None:
    """Split ``s3://bucket/key/with/slashes.jpg`` into (bucket, key)."""
    if not uri or not uri.startswith("s3://"):
        return None
    rest = uri[len("s3://"):]
    if "/" not in rest:
        return None
    bucket, key = rest.split("/", 1)
    return bucket, key


def candidate_urls(uri: str) -> list[tuple[str, str]]:
    """Every plausible HTTPS form of an S3 URI, as (label, url) pairs.

    Keys here contain spaces ("Glenbrook 2 - Terrestrial"), so each segment
    is percent-encoded individually — quoting the whole key would escape the
    path separators too and produce a 404 that looks like a permissions
    problem.
    """
    parsed = parse_s3_uri(uri)
    if not parsed:
        return []
    bucket, key = parsed
    enc = "/".join(quote(part) for part in key.split("/"))

    out = [
        # Virtual-hosted style, region-less. Works for us-east-1 buckets and
        # redirects for others.
        ("virtual-hosted (no region)", f"https://{bucket}.s3.amazonaws.com/{enc}"),
    ]
    for region in CANDIDATE_REGIONS:
        out.append((f"virtual-hosted ({region})",
                    f"https://{bucket}.s3.{region}.amazonaws.com/{enc}"))
        out.append((f"path-style ({region})",
                    f"https://s3.{region}.amazonaws.com/{bucket}/{enc}"))
    return out


def probe_asset(uri: str, timeout: float = HTTP_TIMEOUT_SECONDS) -> dict:
    """Try each candidate URL for one asset and report what happened.

    Uses GET with a tiny Range header rather than HEAD: some S3 policies
    allow ``s3:GetObject`` while denying ``s3:ListBucket``, and a HEAD can
    fail where a ranged GET succeeds.
    """
    results: list[dict] = []
    headers = {"User-Agent": USER_AGENT, "Range": "bytes=0-1023"}

    with httpx.Client(timeout=timeout, headers=headers, follow_redirects=True) as client:
        for label, url in candidate_urls(uri):
            entry: dict = {"label": label, "url": url}
            try:
                resp = client.get(url)
                entry["status"] = resp.status_code
                entry["content_type"] = resp.headers.get("content-type")
                entry["ok"] = (
                    resp.status_code in (200, 206)
                    and "image" in (resp.headers.get("content-type") or "")
                )
                # S3 denials come back as XML with a useful error code.
                if not entry["ok"] and resp.content[:200]:
                    body = resp.content[:300].decode("utf-8", "replace")
                    for code in ("AccessDenied", "NoSuchBucket", "NoSuchKey",
                                 "PermanentRedirect", "AllAccessDisabled",
                                 "InvalidRequest"):
                        if code in body:
                            entry["s3_error"] = code
                            break
            except httpx.HTTPError as exc:
                entry["ok"] = False
                entry["error"] = str(exc)[:120]
            results.append(entry)
            if entry.get("ok"):
                break          # first working form is enough

    working = next((r for r in results if r.get("ok")), None)
    return {"uri": uri, "working": working, "attempts": results}


def report(uri: str) -> int:
    """Human-readable probe report. Returns 0 if a public URL was found."""
    parsed = parse_s3_uri(uri)
    if not parsed:
        print(f"\n  Not an s3:// URI: {uri}\n")
        return 1
    bucket, key = parsed

    print(f"\n  bucket  {bucket}")
    print(f"  key     {key}\n")

    result = probe_asset(uri)
    for r in result["attempts"]:
        mark = "OK " if r.get("ok") else "   "
        detail = ""
        if r.get("s3_error"):
            detail = f"  [{r['s3_error']}]"
        elif r.get("error"):
            detail = f"  [{r['error']}]"
        elif r.get("content_type"):
            detail = f"  [{r['content_type']}]"
        print(f"  {mark}{str(r.get('status', '---')):>4}  {r['label']:28}{detail}")
        print(f"        {r['url']}")

    if result["working"]:
        print(f"\n  PUBLIC. Working form: {result['working']['label']}")
        print("  The bucket serves images over HTTPS, so a time-lapse is")
        print("  buildable — wire this pattern into config as ASSET_URL_STYLE")
        print("  and have transform emit resolvable URLs.\n")
        return 0

    codes = {r.get("s3_error") for r in result["attempts"] if r.get("s3_error")}
    print("\n  NOT PUBLIC over any standard S3 form.")
    if "AccessDenied" in codes or "AllAccessDisabled" in codes:
        print("  AccessDenied means the object exists but the bucket policy")
        print("  blocks anonymous reads. Two routes left:")
        print("    1. TEON exposes a proxy endpoint their own frontend uses —")
        print("       check DevTools on a field-camera detail page that")
        print("       actually renders an image.")
        print("    2. Ask TEON to allow public reads, or for presigned URLs.")
    elif "NoSuchBucket" in codes:
        print("  NoSuchBucket suggests the name in the URI isn't the real")
        print("  bucket, or it's in an account/region we haven't tried.")
    else:
        print("  No definitive S3 error code came back — inconclusive rather")
        print("  than proven private. Worth a DevTools check on a camera page.")
    print()
    return 1
