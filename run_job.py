"""Wrapper to run any script and log errors to db99 scrape_log."""
import subprocess
import sys
import os
import json

os.chdir(os.path.dirname(os.path.abspath(__file__)))

cmd = sys.argv[1:]
if not cmd:
    print("Usage: python run_job.py <command> [args...]")
    sys.exit(1)

print(f"Running: {' '.join(cmd)}", flush=True)

try:
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=3600,
    )
    print(result.stdout[-2000:] if result.stdout else "(no stdout)", flush=True)
    if result.stderr:
        print(f"STDERR: {result.stderr[-2000:]}", flush=True)

    # Log to db99
    try:
        import pymysql
        import boto3
        client = boto3.client("secretsmanager", region_name="us-east-1")
        resp = client.get_secret_value(SecretId="/ben/ai-tool/db99")
        secret = json.loads(resp["SecretString"])
        host = os.getenv("DB_HOST") or secret.get("DB_HOST") or "10.10.0.8"
        conn = pymysql.connect(
            host=host, port=3306, user=secret["DB_USER"], password=secret["DB_PASSWORD"],
            database="church_scrapes", autocommit=True, charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor,
        )
        cur = conn.cursor()
        notes = f"exit={result.returncode}\nSTDOUT(last 1500):\n{(result.stdout or '')[-1500:]}\nSTDERR(last 500):\n{(result.stderr or '')[-500:]}"
        cur.execute(
            "INSERT INTO scrape_log (scrape_type, completed_at, status, notes) VALUES (%s, NOW(), %s, %s)",
            ("job_wrapper", "completed" if result.returncode == 0 else "failed", notes[:4000]),
        )
        conn.close()
    except Exception as e:
        print(f"Failed to log to db99: {e}", flush=True)

    sys.exit(result.returncode)

except Exception as e:
    print(f"FATAL: {e}", flush=True)
    sys.exit(1)
