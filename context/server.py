"""UnderCover's context layer: data governance's seven files, served as MCP tools."""

import re
from pathlib import Path
from typing import Any, Literal

import yaml
from mangum import Mangum
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations

HERE = Path(__file__).parent
HEADING = re.compile(r"^## (\d+)\. (.+)$", re.MULTILINE)
FRONT_MATTER = re.compile(r"\A---\n(.*?)\n---\n(.*)\Z", re.DOTALL)
DOCUMENTS = {"policy_wording": "policy_wording.md", "claims_manual": "claims_manual.md"}
READ_ONLY = ToolAnnotations(readOnlyHint=True)

Document = Literal["policy_wording", "claims_manual"]
Record = Literal["claim", "policy", "previous_claim"]
Plan = Literal["economy", "premium", "first"]


def source_of(name: str, data: dict[str, Any]) -> dict[str, str]:
    """A file's name with where it comes from: its system, owner and review date."""
    return {"file": name, **{k: str(v) for k, v in data["source"].items()}}


def read_yaml(name: str) -> dict[str, Any]:
    """One of the YAML files, as data."""
    return yaml.safe_load((HERE / name).read_text(encoding="utf-8"))


def read_document(name: str) -> dict[str, Any]:
    """A document split at its numbered headings, each section tagged with its
    document, number and title; the front matter is its source and version."""
    text = (HERE / DOCUMENTS[name]).read_text(encoding="utf-8").replace("\r\n", "\n")
    found = FRONT_MATTER.match(text)
    if found is None:
        raise ValueError(f"{DOCUMENTS[name]} has no front matter")
    front, body = yaml.safe_load(found.group(1)), found.group(2)
    headings = list(HEADING.finditer(body))
    ends = [h.start() for h in headings[1:]] + [len(body)]
    sections = [
        {
            "document": name,
            "number": h.group(1),
            "title": h.group(2).strip(),
            "text": body[h.start() : end].strip(),
        }
        for h, end in zip(headings, ends, strict=True)
    ]
    return {
        "version": str(front["version"]),
        "source": source_of(DOCUMENTS[name], front),
        "sections": sections,
    }


# Read and prepared once, when the process starts, never for each request.
DICTIONARY = read_yaml("data_dictionary.yaml")
GLOSSARY = read_yaml("glossary.yaml")
PLANS = read_yaml("plans.yaml")
RULES = read_yaml("claim_rules.yaml")
REFERENCE = read_yaml("reference_data.yaml")
PREPARED = {name: read_document(name) for name in DOCUMENTS}


def lookup_term(term: str) -> dict[str, Any]:
    """Look up a term the policy wording defines, with the sections it comes from."""
    source = source_of("glossary.yaml", GLOSSARY)
    for entry in GLOSSARY["terms"]:
        if entry["term"].lower() == term.strip().lower():
            return {**entry, "source": source}
    terms = [entry["term"] for entry in GLOSSARY["terms"]]
    return {"term": term, "found": False, "terms_defined": terms, "source": source}


def describe_fields(record: Record) -> dict[str, Any]:
    """Describe what each field of a claim, a policy or a previous claim means."""
    found = next(r for r in DICTIONARY["records"] if r["record"] == record)
    return {**found, "source": source_of("data_dictionary.yaml", DICTIONARY)}


def get_plan(plan: Plan) -> dict[str, Any]:
    """Get one plan's limits, waiting periods and deductible, and the add-ons."""
    return {
        "plan": next(p for p in PLANS["plans"] if p["plan"] == plan),
        "deductible_applies_to": PLANS["deductible_applies_to"],
        "add_ons": PLANS["add_ons"],
        "source": source_of("plans.yaml", PLANS),
    }


def get_claim_rules() -> dict[str, Any]:
    """Get the claim rules: filing window, pre-existing look-back, repeats, meals."""
    return {"rules": RULES["rules"], "source": source_of("claim_rules.yaml", RULES)}


def search_reference_data(query: str) -> dict[str, Any]:
    """Find outside facts on events, places and products holding every query word."""
    words = query.lower().split()
    facts = [
        {"topic": topic["topic"], "fact": " ".join(fact.split())}
        for topic in REFERENCE["topics"]
        for fact in topic["facts"]
        if all(word in fact.lower() for word in words)
    ]
    source = source_of("reference_data.yaml", REFERENCE)
    return {"query": query, "facts": facts, "source": source}


def search_documents(query: str) -> dict[str, Any]:
    """Find the sections of the wording and the manual that hold every query word."""
    words = query.lower().split()
    found = [
        {
            "document": name,
            "number": section["number"],
            "title": section["title"],
            "source": prepared["source"],
        }
        for name, prepared in PREPARED.items()
        for section in prepared["sections"]
        if all(word in section["text"].lower() for word in words)
    ]
    return {"query": query, "sections": found}


def list_sections(document: Document) -> dict[str, Any]:
    """List the numbered sections of policy_wording or claims_manual."""
    prepared = PREPARED[document]
    return {
        "document": document,
        "version": prepared["version"],
        "sections": [
            {"number": s["number"], "title": s["title"]} for s in prepared["sections"]
        ],
        "source": prepared["source"],
    }


def read_section(document: Document, number: str) -> dict[str, Any]:
    """Read one numbered section of policy_wording or claims_manual."""
    prepared = PREPARED[document]
    for section in prepared["sections"]:
        if section["number"] == str(number).strip():
            return {
                **section,
                "version": prepared["version"],
                "source": prepared["source"],
            }
    raise ToolError(f"Section {number} of {document} not found")


TOOLS = [
    lookup_term,
    describe_fields,
    get_plan,
    get_claim_rules,
    search_reference_data,
    search_documents,
    list_sections,
    read_section,
]


def build_server(on_lambda: bool = False) -> FastMCP:
    """A stateless MCP server with JSON responses, every tool read-only.

    On Lambda, the Function URL's IAM auth guards it, so FastMCP's check of the Host
    header, which would refuse the Function URL's host, is off.
    """
    security = TransportSecuritySettings(enable_dns_rebinding_protection=False)
    server = FastMCP(
        "UnderCover context layer",
        stateless_http=True,
        json_response=True,
        transport_security=security if on_lambda else None,
    )
    for tool in TOOLS:
        server.add_tool(tool, annotations=READ_ONLY)
    return server


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """The context layer on AWS Lambda, with a fresh server for each request."""
    # FastMCP's session manager runs once, and Mangum runs the app's startup and
    # shutdown around every request, so a server can't be used twice.
    app = build_server(on_lambda=True).streamable_http_app()
    return Mangum(app, lifespan="auto")(event, context)
