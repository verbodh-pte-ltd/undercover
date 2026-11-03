"""The AWS route without Supabase, for Week 3 Days 4 and 5.

The course's labs use Supabase for the database and the handlers' sign-in, and an IAM user's access key.
This build keeps everything inside AWS instead:

- the database on Amazon RDS for PostgreSQL, AWS_DATABASE_URL in .env
- the handlers' sign-in with Amazon Cognito, AWS_COGNITO_USER_POOL_ID and AWS_COGNITO_CLIENT_ID
- signing in to AWS through IAM Identity Center, with SSO and MFA, never an IAM user

week3day2_rds_cognito.ipynb makes the database and the user pool, and its Part 3 deletes them.
The labs import this module beside week3day4_aws.py and call it from their "AWS without Supabase" cells.
"""

import json
import os
import shutil
import ssl
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import boto3
import psycopg
from botocore.exceptions import (
    SSOTokenLoadError,
    TokenRetrievalError,
    UnauthorizedSSOTokenError,
)
from dotenv import load_dotenv

REPO = Path(__file__).resolve().parents[2]
CLI_VERSION = "1.0.0-rc.5"
DB_INSTANCE = "undercover-db"
REGION = "us-east-1"
COUNT_CLAIMS = "SELECT status, count(*) FROM undercover.claims WHERE evaluation_id IS NULL GROUP BY status ORDER BY status"


def ready(label, ok, detail):
    """Print one line of a readiness check."""
    print(f"{'OK  ' if ok else 'FIX '} {label:<18}{detail}")
    return ok


def check_sign_in(session):
    """The session signs in through Identity Center, with a token that's still valid."""
    try:
        arn = session.client("sts").get_caller_identity()["Arn"]
    except (SSOTokenLoadError, TokenRetrievalError, UnauthorizedSSOTokenError):
        return ready("Sign-in", False, "the SSO token has expired: run aws sso login, then this cell again")
    sso = "AWSReservedSSO_" in arn
    return ready("Sign-in", sso, arn if sso else f"{arn} is not an Identity Center role")


def check_rds(session):
    """The database on RDS is available, reachable by Runtime and Lambda on 5432, and uses TLS."""
    url = os.getenv("AWS_DATABASE_URL", "")
    if not url:
        return ready("RDS", False, "AWS_DATABASE_URL is missing: run week3day2_rds_cognito.ipynb")
    rds, ec2 = session.client("rds"), session.client("ec2")
    instance = rds.describe_db_instances(DBInstanceIdentifier=DB_INSTANCE)["DBInstances"][0]
    group_ids = [g["VpcSecurityGroupId"] for g in instance["VpcSecurityGroups"]]
    groups = ec2.describe_security_groups(GroupIds=group_ids)["SecurityGroups"]
    open_5432 = any(
        rule.get("FromPort") == 5432 and any(r["CidrIp"] == "0.0.0.0/0" for r in rule.get("IpRanges", []))
        for group in groups
        for rule in group["IpPermissions"]
    )
    ok = ready("RDS instance", instance["DBInstanceStatus"] == "available", f"{DB_INSTANCE} is {instance['DBInstanceStatus']}")
    reachable = instance["PubliclyAccessible"] and open_5432
    ok &= ready("RDS reachable", reachable, "public, port 5432 open to Runtime and Lambda" if reachable else "Runtime can't reach it on port 5432")
    with psycopg.connect(url, connect_timeout=10) as connection:
        tls = connection.execute("SELECT ssl FROM pg_stat_ssl WHERE pid = pg_backend_pid()").fetchone()[0]
        claims = connection.execute("SELECT count(*) FROM undercover.claims").fetchone()[0]
    ok &= ready("RDS TLS", tls, "this connection uses TLS" if tls else "this connection doesn't use TLS")
    return ok & ready("RDS data", claims > 0, f"{claims} claims" if claims else "no claims: run Part 2 of week3day2_rds_cognito.ipynb")


def check_cognito(session):
    """The user pool and its app client exist."""
    pool_id, client_id = os.getenv("AWS_COGNITO_USER_POOL_ID", ""), os.getenv("AWS_COGNITO_CLIENT_ID", "")
    if not (pool_id and client_id):
        return ready("Cognito", False, "AWS_COGNITO_USER_POOL_ID or AWS_COGNITO_CLIENT_ID is missing: run week3day2_rds_cognito.ipynb")
    cognito = session.client("cognito-idp")
    pool = cognito.describe_user_pool(UserPoolId=pool_id)["UserPool"]
    cognito.describe_user_pool_client(UserPoolId=pool_id, ClientId=client_id)
    return ready("Cognito", True, f"user pool {pool['Name']} with its app client")


def check_cli():
    """The AgentCore CLI at the course's version."""
    cli = shutil.which("agentcore")
    version = subprocess.run([cli, "--version"], capture_output=True, text=True, check=False).stdout.strip() if cli else ""
    return ready("AgentCore CLI", CLI_VERSION in version, version or f"not installed: npm install -g @aws/agentcore@{CLI_VERSION}")


def check_build(runtimes):
    """The product build for the day: its AgentCore project with the runtimes the lab deploys, and Cognito sign-in."""
    config = REPO / "agentcore" / "agentcore.json"
    if not config.exists():
        return ready("Product build", False, "no agentcore/agentcore.json: build the day's brief, or move it in from setup/ as Day 4 Lab 1 says")
    text = config.read_text(encoding="utf-8")
    names = {r.get("name") for r in json.loads(text).get("runtimes", [])} if text.strip().startswith("{") else set()
    missing = [r for r in runtimes if r not in names]
    ok = ready("Runtimes", not missing, f"agentcore.json declares {', '.join(sorted(n for n in names if n))}" if not missing else f"agentcore.json lacks {', '.join(missing)}: build the day's brief")
    entry = REPO / "agent" / "entrypoint.py"
    ok &= ready("Agent's app", entry.exists(), "agent/entrypoint.py" if entry.exists() else "no agent/entrypoint.py: build Day 4's brief, docs/RUNTIME.md")
    cdk = REPO / "agentcore" / "cdk" / "node_modules"
    ok &= ready("CDK packages", cdk.exists(), "installed" if cdk.exists() else "run npm ci --prefix agentcore/cdk in the undercover folder")
    backend = (REPO / "backend" / "auth.py").read_text(encoding="utf-8").lower()
    dashboard = REPO / "dashboard" / "src" / "auth.ts"
    cognito = "cognito" in backend and dashboard.exists() and "VITE_COGNITO" in dashboard.read_text(encoding="utf-8")
    return ok & ready("Cognito sign-in", cognito, "the app and dashboard sign in with Cognito" if cognito else "this build signs in only with Supabase: bring over the Cognito sign-in from your Day 3 build")


def terminal_certificates():
    """Behind Cloudflare WARP on Windows, save the certificates Windows trusts and print the terminal's lines."""
    if sys.platform != "win32":
        return ready("Certificates", True, "not Windows, so nothing to do")
    certs = Path(tempfile.gettempdir()) / "undercover_windows_certs.pem"
    pems = {ssl.DER_cert_to_PEM_cert(cert) for store in ("ROOT", "CA") for cert, encoding, _ in ssl.enum_certificates(store) if encoding == "x509_asn"}
    certs.write_text("".join(pems))
    ready("Certificates", True, f"saved to {certs}")
    print("\nPaste these into the PowerShell terminal where you'll run npm and agentcore:\n")
    for name in ["NODE_EXTRA_CA_CERTS", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "AWS_CA_BUNDLE"]:
        print(f'$env:{name} = "{certs}"')
    return True


def check_readiness(session, runtimes):
    """Everything a Day 4 or Day 5 lab needs on this route, one line each; True when all is ready."""
    load_dotenv(REPO / ".env", override=True)
    results = [check_sign_in(session), check_rds(session), check_cognito(session), check_cli(), check_build(runtimes)]
    terminal_certificates()
    print("\nReady for this lab" if all(results) else "\nFix the lines marked FIX before you go on")
    return all(results)


def build_dashboard_for_cognito():
    """Build the dashboard again with the user pool and its app client, so the deployed dashboard signs in through Cognito."""
    load_dotenv(REPO / ".env", override=True)
    built = subprocess.run(  # noqa: S603 (a fixed command)
        [shutil.which("npm"), "run", "build"],
        cwd=REPO,
        env=os.environ
        | {
            "VITE_COGNITO_USER_POOL_ID": os.environ["AWS_COGNITO_USER_POOL_ID"],
            "VITE_COGNITO_CLIENT_ID": os.environ["AWS_COGNITO_CLIENT_ID"],
        },
        capture_output=True,
        text=True,
        check=False,
    )
    print(built.stdout[-1000:] or built.stderr[-2000:])
    built.check_returncode()


def app_environment(api_key):
    """The app's settings on Lambda for RDS and Cognito: the database straight to 5432, and the user pool's keys,
    issuer and app client for the sign-in check."""
    load_dotenv(REPO / ".env", override=True)
    issuer = f"https://cognito-idp.{REGION}.amazonaws.com/{os.environ['AWS_COGNITO_USER_POOL_ID']}"
    return {
        "DATABASE_URL": os.environ["AWS_DATABASE_URL"],
        "AUTH_JWKS_URL": f"{issuer}/.well-known/jwks.json",
        "AUTH_ISSUER": issuer,
        "AUTH_AUDIENCE": os.environ["AWS_COGNITO_CLIENT_ID"],
        "UNDERCOVER_API_KEY": api_key,
    }


def update_app(function_name, environment):
    """Give the app on Lambda new settings, and wait until they're live."""
    lambda_client = boto3.Session().client("lambda")
    lambda_client.update_function_configuration(FunctionName=function_name, Environment={"Variables": environment})
    lambda_client.get_waiter("function_updated_v2").wait(FunctionName=function_name)
    print(f"{function_name} uses the database on RDS and signs handlers in with Cognito")


def seed_rds():
    """Seed the database on RDS from data/, as npm run seed does locally."""
    load_dotenv(REPO / ".env", override=True)
    environment = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"} | {"DATABASE_URL": os.environ["AWS_DATABASE_URL"]}
    seeded = subprocess.run(  # noqa: S603 (a fixed command)
        [shutil.which("uv"), "run", "-m", "backend.seed"], cwd=REPO, env=environment, capture_output=True, text=True, check=False
    )
    print(seeded.stdout.strip() or seeded.stderr[-2000:])
    seeded.check_returncode()


def watch_queue(minutes=20):
    """Count the queue's claims on RDS by status every 15 seconds, until none is waiting or in progress."""
    with psycopg.connect(os.environ["AWS_DATABASE_URL"], autocommit=True) as connection:
        for _ in range(minutes * 4):
            counts = dict(connection.execute(COUNT_CLAIMS).fetchall())
            print(", ".join(f"{status} {number}" for status, number in counts.items()))
            if not counts.keys() & {"waiting", "in_progress"}:
                return
            time.sleep(15)


def show_what_stays(session):
    """The database, its security group and the user pool, which week3day2_rds_cognito.ipynb Part 3 deletes."""
    rds, ec2, cognito = session.client("rds"), session.client("ec2"), session.client("cognito-idp")
    found = {
        "RDS databases": [d["DBInstanceIdentifier"] for d in rds.describe_db_instances()["DBInstances"] if d["DBInstanceIdentifier"].startswith("undercover")],
        "Security groups": [g["GroupName"] for g in ec2.describe_security_groups(Filters=[{"Name": "group-name", "Values": ["undercover*"]}])["SecurityGroups"]],
        "Cognito user pools": [p["Name"] for page in cognito.get_paginator("list_user_pools").paginate(MaxResults=60) for p in page["UserPools"] if p["Name"].startswith("undercover")],
    }
    for label, names in found.items():
        print(f"{label + ':':<20}{', '.join(names) or 'none'}")
