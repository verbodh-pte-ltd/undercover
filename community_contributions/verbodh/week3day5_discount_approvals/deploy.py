"""Deploy the discount agent to AgentCore Runtime with boto3, call it, and tear it all down.

Everything is named for the course's permission set: the role starts undercover-, the code's
bucket starts cdk-, and every resource is tagged project=discounts.

- package(): the code and its packages for Linux on ARM64, as one zip
- deploy(openrouter_key, database_url): the bucket, the two credentials in AgentCore Identity,
  the Memory, the role and the runtime; returns the runtime's ARN and the Memory's id
- invoke(arn, payload, session_id): one call to the runtime
- tear_down(): deletes everything deploy() made
"""

import json
import shutil
import subprocess
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any

import boto3

HERE = Path(__file__).resolve().parent
BUILD = HERE / "build"
REGION = "us-east-1"
NAME = "discounts_agent"
ROLE = "undercover-discounts-runtime"
MEMORY = "discounts_memory"
PROVIDERS = {"OPENROUTER_API_KEY": "discounts-openrouter", "DATABASE_URL": "discounts-database"}
TAGS = {"project": "discounts"}

session = boto3.Session(region_name=REGION)
account = session.client("sts").get_caller_identity()["Account"]
BUCKET = f"cdk-discounts-agent-{account}-{REGION}"
control = session.client("bedrock-agentcore-control")
data = session.client("bedrock-agentcore")
iam = session.client("iam")
s3 = session.client("s3")


def package() -> Path:
    """The zip Runtime runs: main.py, discounts/, context/ and the packages beside them."""
    shutil.rmtree(BUILD, ignore_errors=True)
    staged = BUILD / "package"
    subprocess.run(
        [
            "uv", "pip", "install", "--quiet", "--target", str(staged),
            "--python-platform", "aarch64-manylinux_2_28", "--python-version", "3.12",
            "--only-binary", ":all:", "-r", str(HERE / "requirements.txt"),
        ],
        check=True,
    )
    shutil.copy(HERE / "main.py", staged)
    for folder in ("discounts", "context"):
        shutil.copytree(HERE / folder, staged / folder, ignore=shutil.ignore_patterns("__pycache__"))
    zipped = BUILD / "discounts_agent.zip"
    with zipfile.ZipFile(zipped, "w", zipfile.ZIP_DEFLATED) as z:
        for path in sorted(staged.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                z.write(path, path.relative_to(staged).as_posix())
    print(f"Packaged {zipped.stat().st_size / 1e6:.1f} MB")
    return zipped


def upload(zipped: Path) -> str:
    try:
        s3.head_bucket(Bucket=BUCKET)
    except s3.exceptions.ClientError:
        s3.create_bucket(Bucket=BUCKET)
        s3.put_bucket_tagging(Bucket=BUCKET, Tagging={"TagSet": [{"Key": k, "Value": v} for k, v in TAGS.items()]})
    key = f"code/{int(time.time())}.zip"
    s3.upload_file(str(zipped), BUCKET, key)
    return key


def put_credential(name: str, value: str) -> None:
    try:
        control.create_api_key_credential_provider(name=name, apiKey=value, tags=TAGS)
    except control.exceptions.ValidationException:
        control.update_api_key_credential_provider(name=name, apiKey=value)
    except Exception as exc:
        if "already exists" not in str(exc):
            raise
        control.update_api_key_credential_provider(name=name, apiKey=value)


def find_memory() -> str | None:
    for memory in control.list_memories()["memories"]:
        if memory["id"].startswith(MEMORY) and memory["status"] != "DELETING":
            return memory["id"]
    return None


def make_memory() -> str:
    """A Memory with one strategy: each rep's preferences in their own namespace."""
    memory_id = find_memory()
    if memory_id is None:
        memory_id = control.create_memory(
            name=MEMORY,
            eventExpiryDuration=30,
            memoryStrategies=[
                {
                    "userPreferenceMemoryStrategy": {
                        "name": "rep_preferences",
                        "namespaceTemplates": ["/reps/{actorId}/preferences/"],
                    }
                }
            ],
            tags=TAGS,
        )["memory"]["id"]
    while (status := control.get_memory(memoryId=memory_id)["memory"]["status"]) == "CREATING":
        time.sleep(10)
    print(f"Memory {memory_id} is {status}")
    return memory_id


def make_role(memory_id: str) -> str:
    trust = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                "Action": "sts:AssumeRole",
                "Condition": {"StringEquals": {"aws:SourceAccount": account}},
            }
        ],
    }
    arn = f"arn:aws:bedrock-agentcore:{REGION}:{account}"
    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "Logs",
                "Effect": "Allow",
                "Action": ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents", "logs:DescribeLogStreams", "logs:DescribeLogGroups"],
                "Resource": f"arn:aws:logs:{REGION}:{account}:log-group:/aws/bedrock-agentcore/runtimes/*",
            },
            {
                "Sid": "Traces",
                "Effect": "Allow",
                "Action": ["xray:PutTraceSegments", "xray:PutTelemetryRecords", "cloudwatch:PutMetricData"],
                "Resource": "*",
            },
            {
                "Sid": "Credentials",
                "Effect": "Allow",
                "Action": ["bedrock-agentcore:GetWorkloadAccessToken", "bedrock-agentcore:GetWorkloadAccessTokenForJWT",
                           "bedrock-agentcore:GetWorkloadAccessTokenForUserId", "bedrock-agentcore:GetResourceApiKey"],
                "Resource": [f"{arn}:workload-identity-directory/default", f"{arn}:workload-identity-directory/default/*",
                             f"{arn}:token-vault/default", f"{arn}:token-vault/default/*"],
            },
            {
                "Sid": "CredentialSecrets",
                "Effect": "Allow",
                "Action": "secretsmanager:GetSecretValue",
                "Resource": f"arn:aws:secretsmanager:{REGION}:{account}:secret:bedrock-agentcore-identity!*",
            },
            {
                "Sid": "Memory",
                "Effect": "Allow",
                "Action": ["bedrock-agentcore:CreateEvent", "bedrock-agentcore:RetrieveMemoryRecords", "bedrock-agentcore:ListMemoryRecords"],
                "Resource": f"{arn}:memory/{memory_id}",
            },
        ],
    }
    try:
        role_arn = iam.create_role(
            RoleName=ROLE,
            AssumeRolePolicyDocument=json.dumps(trust),
            Tags=[{"Key": k, "Value": v} for k, v in TAGS.items()],
        )["Role"]["Arn"]
        time.sleep(10)  # A new role takes a moment before a service can assume it
    except iam.exceptions.EntityAlreadyExistsException:
        role_arn = iam.get_role(RoleName=ROLE)["Role"]["Arn"]
    iam.put_role_policy(RoleName=ROLE, PolicyName="discounts-agent", PolicyDocument=json.dumps(policy))
    return role_arn


def find_runtime() -> dict[str, Any] | None:
    for runtime in control.list_agent_runtimes()["agentRuntimes"]:
        if runtime["agentRuntimeName"] == NAME:
            return runtime
    return None


def deploy(openrouter_key: str, database_url: str) -> tuple[str, str]:
    key = upload(package())
    put_credential(PROVIDERS["OPENROUTER_API_KEY"], openrouter_key)
    put_credential(PROVIDERS["DATABASE_URL"], database_url)
    memory_id = make_memory()
    role_arn = make_role(memory_id)
    settings = {
        "agentRuntimeArtifact": {
            "codeConfiguration": {
                "code": {"s3": {"bucket": BUCKET, "prefix": key}},
                "runtime": "PYTHON_3_12",
                "entryPoint": ["main.py"],
            }
        },
        "roleArn": role_arn,
        "networkConfiguration": {"networkMode": "PUBLIC"},
        "protocolConfiguration": {"serverProtocol": "HTTP"},
        "lifecycleConfiguration": {"idleRuntimeSessionTimeout": 60},
        "environmentVariables": {f"DISCOUNTS_CREDENTIAL_{k}": v for k, v in PROVIDERS.items()} | {"DISCOUNTS_MEMORY_ID": memory_id},
    }
    existing = find_runtime()
    if existing:
        runtime_id = existing["agentRuntimeId"]
        control.update_agent_runtime(agentRuntimeId=runtime_id, **settings)
    else:
        runtime_id = control.create_agent_runtime(agentRuntimeName=NAME, tags=TAGS, **settings)["agentRuntimeId"]
    while True:
        runtime = control.get_agent_runtime(agentRuntimeId=runtime_id)
        if runtime["status"] not in ("CREATING", "UPDATING"):
            break
        time.sleep(10)
    print(f"Runtime {NAME} is {runtime['status']}")
    if runtime["status"] != "READY":
        raise RuntimeError(runtime.get("failureReason", runtime["status"]))
    return runtime["agentRuntimeArn"], memory_id


def new_session() -> str:
    """A Runtime session id: at least 33 characters."""
    return f"discounts-{uuid.uuid4()}"


def invoke(arn: str, payload: dict[str, Any], session_id: str | None = None) -> dict[str, Any]:
    """One call. The user id is the person asking: with it, Runtime gives the agent a workload
    access token, which AgentCore Identity needs before it hands over the credentials. Without
    it, the SDK falls back to making a workload identity of its own, which the role can't."""
    reply = data.invoke_agent_runtime(
        agentRuntimeArn=arn,
        runtimeSessionId=session_id or new_session(),
        runtimeUserId=str(payload.get("person_id") or payload.get("rep_id") or "discounts"),
        payload=json.dumps(payload).encode(),
    )
    return json.loads(reply["response"].read())


def notes(memory_id: str, rep_id: str) -> list[str]:
    found = data.list_memory_records(memoryId=memory_id, namespace=f"/reps/{rep_id}/preferences/")
    return [r["content"]["text"] for r in found.get("memoryRecordSummaries", [])]


def tear_down() -> None:
    runtime = find_runtime()
    if runtime:
        control.delete_agent_runtime(agentRuntimeId=runtime["agentRuntimeId"])
        print(f"Deleted runtime {NAME}")
    logs = session.client("logs")
    for group in logs.describe_log_groups(logGroupNamePrefix=f"/aws/bedrock-agentcore/runtimes/{NAME}")["logGroups"]:
        logs.delete_log_group(logGroupName=group["logGroupName"])
        print(f"Deleted log group {group['logGroupName']}")
    memory_id = find_memory()
    if memory_id:
        control.delete_memory(memoryId=memory_id)
        print(f"Deleted memory {memory_id}")
    for name in PROVIDERS.values():
        try:
            control.delete_api_key_credential_provider(name=name)
            print(f"Deleted credential {name}")
        except Exception as exc:
            print(f"Credential {name}: {exc}")
    try:
        iam.delete_role_policy(RoleName=ROLE, PolicyName="discounts-agent")
        iam.delete_role(RoleName=ROLE)
        print(f"Deleted role {ROLE}")
    except iam.exceptions.NoSuchEntityException:
        pass
    try:
        for obj in s3.list_objects_v2(Bucket=BUCKET).get("Contents", []):
            s3.delete_object(Bucket=BUCKET, Key=obj["Key"])
        s3.delete_bucket(Bucket=BUCKET)
        print(f"Deleted bucket {BUCKET}")
    except s3.exceptions.ClientError:
        pass
