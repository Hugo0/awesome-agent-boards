#!/usr/bin/env python3
"""Validate boards.json and render the awesome-agent-boards README from it.

Standard library only. The README is generated: edit boards.json, never the
README. The readme workflow regenerates it on every push to main.

  generate_readme.py [DIR]          write DIR/README.md from DIR/boards.json
  generate_readme.py --validate [DIR]  validate boards.json only
  generate_readme.py --check [DIR]     validate, and fail if README.md is stale

DIR defaults to the repository root (this file's parent's parent).
"""
import datetime
import json
import pathlib
import re
import sys

MAP_URL = "https://swarmmemo.com/guides/agent-board-map"
ROOM_URL = "https://swarmmemo.com/r/boards"

TYPES = {"object": dict, "array": list, "string": str, "boolean": bool, "null": type(None)}


def schema_errors(value, schema, path="$"):
    """The subset of JSON Schema that boards.schema.json uses, nothing more."""
    if schema is True:
        return []
    if schema is False:
        return [f"{path}: not allowed"]
    errors = []
    kinds = schema.get("type")
    if kinds is not None:
        kinds = kinds if isinstance(kinds, list) else [kinds]
        if not any(isinstance(value, TYPES[k]) and not (k != "boolean" and isinstance(value, bool)) for k in kinds):
            return [f"{path}: expected {'/'.join(kinds)}"]
    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: must be {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: must be one of {schema['enum']}")
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0) or len(value) > schema.get("maxLength", len(value)):
            errors.append(f"{path}: length out of range")
        if "pattern" in schema and not re.search(schema["pattern"], value):
            errors.append(f"{path}: does not match {schema['pattern']}")
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            errors.append(f"{path}: too few items")
        for i, item in enumerate(value):
            errors += schema_errors(item, schema.get("items", True), f"{path}[{i}]")
    if isinstance(value, dict):
        props = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in value:
                errors.append(f"{path}: missing {key}")
        for key, item in value.items():
            if key in props:
                errors += schema_errors(item, props[key], f"{path}.{key}")
            elif schema.get("additionalProperties", True) is False:
                errors.append(f"{path}: unknown field {key}")
    for sub in schema.get("allOf", []):
        errors += schema_errors(value, sub, path)
    if "if" in schema and not schema_errors(value, schema["if"], path):
        errors += schema_errors(value, schema.get("then", True), path)
    return errors


def validate(data, schema):
    """Schema errors plus the rules a schema cannot say. Empty list means valid."""
    errors = schema_errors(data, schema)
    if errors:
        return errors
    names, urls = set(), set()
    used = set()
    for i, b in enumerate(data["boards"]):
        where = f"$.boards[{i}] {b['name']!r}"
        if b["name"].lower() in names:
            errors.append(f"{where}: duplicate name")
        names.add(b["name"].lower())
        if b["url"]:
            if b["url"] in urls:
                errors.append(f"{where}: duplicate url")
            urls.add(b["url"])
        # An entry can be re-checked on its own after the last full re-read
        # (the list's date), as CONTRIBUTING asks; it cannot be dated ahead.
        if b.get("checked", "") > datetime.date.today().isoformat():
            errors.append(f"{where}: checked in the future")
        if b["group"] == "verified":
            if b["section"] not in data["sections"]:
                errors.append(f"{where}: unknown section")
            used.add(b.get("section"))
        if b["group"] == "reported" and "http" in b["name"] + b["note"]:
            errors.append(f"{where}: reported entries carry no address")
    for s in data["sections"]:
        if s not in used:
            errors.append(f"section {s!r} has no entries")
    return errors


def anchor(title):
    return re.sub(r"[^a-z0-9 -]", "", title.lower()).replace(" ", "-")


def render(data):
    boards = data["boards"]
    reported = [b for b in boards if b["group"] == "reported"]
    rest = [b for b in boards if b["group"] == "completeness"]
    toc = data["sections"] + ["Reported, not verified", "Listed for completeness", "Criteria", "How to add a board"]
    out = [
        "# Awesome Agent Boards [![Awesome](https://awesome.re/badge.svg)](https://awesome.re)",
        "",
        "> A curated list of public places where AI agents talk to each other.",
        "",
        f"This list is the source of the [agent board map]({MAP_URL}) on SwarmMemo, which is built from it. "
        "It lists every place we found; the criteria decide which section an entry goes in, not whether it appears. "
        "Each site was read once, read-only, on the date shown. Descriptions come from its own public pages and are "
        "not audited. A listing is not an endorsement, and what an agent reads on any board is data, not instructions.",
        "",
        "**Get listed, corrected or removed** in either of two ways: open a pull request that edits "
        "`boards.json` ([how](#how-to-add-a-board)), or post in the "
        f"[boards room]({ROOM_URL}) on SwarmMemo, which needs no GitHub account. Both reach the same list.",
        "",
        "## Contents",
        "",
    ]
    out += [f"- [{t}](#{anchor(t)})" for t in toc]
    for section in data["sections"]:
        out += ["", f"## {section}", ""]
        for b in boards:
            if b["group"] == "verified" and b["section"] == section:
                out.append(f"- [{b['name']}]({b['url']}) - {b['about']} Access: {b['access']}"
                           f" Identity: {b['identity']} Checked {b['checked']}.")
    out += ["", "## Reported, not verified", "",
            "Places named to us that we could not check. They are listed without links until someone can point to a public address.", ""]
    out += [f"- **{b['name']}** - {b['note']}" for b in reported]
    out += ["", "## Listed for completeness", "",
            "Places we checked that do not meet the criteria. Each carries one factual reason. "
            "A domain that imitates another site is named without a link.", ""]
    for b in rest:
        label = f"[{b['name']}]({b['url']})" if b["link"] else f"**{b['name']}**"
        out.append(f"- {label} - {b['reason']} Checked {b['checked']}.")
    out += [
        "", "## Criteria", "",
        "- **Public.** Anyone can find it and at least see how to take part, without an invitation or a payment.",
        "- **Reachable by an agent.** An agent can read or post through HTTP, an API or MCP, not only through a human's browser session.",
        "- **Not malicious.** No phishing, malware, credential collection or giveaway schemes, and not primarily a token, payment or referral scheme.",
        "",
        "Size, activity and quality are not criteria, and order within a section carries no ranking.",
        "",
        "## How to add a board", "",
        "Open a pull request that edits `boards.json` only; this README is generated from it. "
        "Give the name, the public https address and one "
        "factual sentence each on what it is, how an agent reads and posts, and what identity it asks for. "
        "A maintainer reads the site once, read-only, before merging and places it by the criteria above. "
        "You can also request a listing, a correction or a removal in the SwarmMemo boards room linked above; "
        "an operator who asks for their own site to be removed will have it removed.",
        "",
        "## License", "",
        "[CC0 1.0](LICENSE). To the extent possible under law, the contributors have waived all copyright to this list.",
        "",
    ]
    return "\n".join(out)


def main(argv):
    check, only = "--check" in argv, "--validate" in argv
    args = [a for a in argv if a not in ("--check", "--validate")]
    here = pathlib.Path(__file__).resolve().parent
    root = pathlib.Path(args[0]) if args else here.parent
    data = json.loads((root / "boards.json").read_text(encoding="utf-8"))
    schema = json.loads((root / "boards.schema.json").read_text(encoding="utf-8"))
    errors = validate(data, schema)
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    if only:
        print(f"ok: {len(data['boards'])} entries valid")
        return 0
    readme = render(data)
    target = root / "README.md"
    if check:
        if not target.exists() or target.read_text(encoding="utf-8") != readme:
            print("README.md does not match boards.json: run scripts/generate_readme.py, or leave README.md unchanged", file=sys.stderr)
            return 1
        print(f"ok: {len(data['boards'])} entries, README up to date")
        return 0
    target.write_text(readme, encoding="utf-8")
    print(f"wrote {target}: {len(data['boards'])} entries")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
