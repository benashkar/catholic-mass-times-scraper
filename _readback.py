"""Give a Render one-off job an output channel that can actually be read back.

**The problem this exists to solve.** A one-off job's stdout is not
retrievable. `GET /v1/logs` returns `logs: null` for a finished cron one-off
(reconfirmed 2026-08-09 with `job-d9scap2jobas73f2sung`), and the exit code
carries no signal either -- control jobs running `sys.exit(7)` and
`sys.exit(0)` BOTH report `"status": "succeeded"`. So a diagnostic that only
prints is an instrument with no readout.

That is not a cosmetic gap. It is the documented reason the TOP200 tier's
datacenter-vantage half went unmeasured, leaving four GREEN contracts carrying
unquantified egress risk (`docs/DISCOVERY_LOG_TOP200.md`, "The vantage we
could not get").

Telegram closes it for a human, but not for a caller: the bot cannot read its
own sent messages back, so anything automated is still blind. S3 closes it for
both. The bucket and credentials already exist on every `crime-*` service for
the mugshot pipeline, so this adds no new infrastructure and no new secret.

**Usage in a diagnostic:**

    from scripts._readback import publish
    publish("green_contract_check_200", "\\n".join(lines))

**Reading it back from anywhere:**

    from scripts._readback import fetch_latest
    key, text = fetch_latest("green_contract_check_200")

Writes go to `s3://$S3_BUCKET/diagnostics/<name>/<utc-iso>.txt`. `diagnostics/`
is deliberately outside the `inmates/` prefix the mugshot pipeline owns, so an
orphan-object purge over image keys can never sweep a measurement away.

Never raises. A readback channel that can fail the job it is reporting on is
worse than no channel -- the whole point is to observe the run, not to become
another way for it to die.
"""

from __future__ import annotations

import datetime
import logging
import os

logger = logging.getLogger(__name__)

PREFIX = "diagnostics"


def _client():
    """Build an S3 client, accepting EITHER credential naming convention.

    This is not defensive padding -- the two halves of the platform are named
    differently and a readback that only knew one would no-op exactly where it
    is needed most. `.env` on a workstation carries `S3_ACCESS_KEY_ID` /
    `S3_SECRET_ACCESS_KEY` / `S3_BUCKET`, while `crime-daily-sweep-1` carries
    `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` / `AWS_DEFAULT_REGION` and no
    `S3_*` at all (checked 2026-08-09). Reading only `S3_*` would mean the
    channel worked on the laptop and silently published nothing from Render --
    reproducing the blindness this module exists to end.

    Bucket falls back to the same literal `src/parsers/photo_handler.py`
    already defaults to, so both halves address one bucket.

    Returns (None, None) rather than raising when nothing is configured, so a
    credential-less local run degrades to print-only.
    """
    key = os.environ.get("S3_ACCESS_KEY_ID") or os.environ.get("AWS_ACCESS_KEY_ID")
    secret = os.environ.get("S3_SECRET_ACCESS_KEY") or os.environ.get("AWS_SECRET_ACCESS_KEY")
    bucket = os.environ.get("S3_BUCKET", "hamster-storage1")
    region = os.environ.get("S3_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"
    if not (key and secret):
        logger.warning("[ERR] readback: no S3/AWS credentials, skipping")
        return None, None
    import boto3  # imported lazily: local dev may not have it

    return boto3.client(
        "s3",
        aws_access_key_id=key,
        aws_secret_access_key=secret,
        region_name=region,
    ), bucket


def publish(name: str, text: str) -> str | None:
    """Write `text` under `diagnostics/<name>/<utc-iso>.txt`. Return the key.

    Returns None on any failure -- see the module docstring on why this must
    never raise.
    """
    try:
        s3, bucket = _client()
        if s3 is None:
            return None
        stamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%dT%H%M%SZ")
        key = f"{PREFIX}/{name}/{stamp}.txt"
        s3.put_object(
            Bucket=bucket,
            Key=key,
            Body=text.encode("utf-8", errors="replace"),
            ContentType="text/plain; charset=utf-8",
        )
        print(f"[OK] readback published s3://{bucket}/{key}", flush=True)
        return key
    except Exception as e:
        print(f"[ERR] readback publish failed: {type(e).__name__}: {e}", flush=True)
        return publish_env(name, text)


def publish_env(name: str, text: str) -> str | None:
    """Fallback channel: stash the report in one of this service's env vars.

    The church-scrapes services carry AWS credentials for Secrets Manager
    (db99 passwords) but NOT s3:PutObject on the diagnostics bucket, so the S3
    channel no-ops here -- a verify job reported success while writing nothing,
    which is worse than no channel because it looks like it worked.

    Render's env vars are readable through the same API that sets them, and a
    single-key PUT touches only that key (never use the bulk PUT, which
    REPLACES the whole set) and does not trigger a redeploy. That makes it a
    serviceable, if unglamorous, mailbox for a job that has no other way home.

    Truncated to 4KB: env values are not meant to hold essays, and the useful
    part of these reports is the head.
    """
    try:
        key = os.environ.get("RENDER_API_KEY")
        service = os.environ.get("RENDER_SERVICE_ID")
        if not (key and service):
            print("[ERR] readback env fallback: no RENDER_API_KEY/SERVICE_ID", flush=True)
            return None
        import requests

        var = f"READBACK_{name.upper()[:40]}"
        # Stamp it. Unlike the S3 channel, which keys every report by
        # timestamp, this mailbox has ONE key per name and each write
        # overwrites the last. Read it while a job is still running and you get
        # the PREVIOUS run's text, which looks exactly like a fresh result that
        # happens to be unchanged -- a genuinely dangerous way to misread a
        # verification. The stamp makes staleness visible.
        stamped = (
            datetime.datetime.now(datetime.UTC).strftime("published %Y-%m-%dT%H:%M:%SZ")
            + "\n"
            + text
        )
        r = requests.put(
            f"https://api.render.com/v1/services/{service}/env-vars/{var}",
            headers={"Authorization": f"Bearer {key}"},
            json={"value": stamped[:4000]},
            timeout=30,
        )
        if r.status_code in (200, 201):
            print(f"[OK] readback published to env var {var}", flush=True)
            return var
        print(f"[ERR] readback env fallback {r.status_code}: {r.text[:200]}", flush=True)
        return None
    except Exception as e:
        print(f"[ERR] readback env fallback failed: {type(e).__name__}: {e}", flush=True)
        return None


def fetch_latest(name: str) -> tuple[str | None, str | None]:
    """Return (key, text) of the most recent readback for `name`.

    Keys are UTC-timestamped in a sortable format, so lexical max is newest.
    """
    try:
        s3, bucket = _client()
        if s3 is None:
            return None, None
        objs: list[dict] = []
        token = None
        while True:
            kw = {"Bucket": bucket, "Prefix": f"{PREFIX}/{name}/"}
            if token:
                kw["ContinuationToken"] = token
            r = s3.list_objects_v2(**kw)
            objs.extend(r.get("Contents", []))
            if not r.get("IsTruncated"):
                break
            token = r.get("NextContinuationToken")
        if not objs:
            return None, None
        key = max(o["Key"] for o in objs)
        body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
        return key, body.decode("utf-8", errors="replace")
    except Exception as e:
        print(f"[ERR] readback fetch failed: {type(e).__name__}: {e}", flush=True)
        return None, None
