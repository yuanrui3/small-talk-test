"""Check or prepare a public history without changing the working study branch.

The default is read-only. --write recreates commits under a separate public
branch with the specified GitHub no-reply identity. It never pushes, reads
ignored data, checks out files, or rewrites the source branch. Commit trees,
messages, parent relationships and original dates are preserved. Signatures
and other nonessential commit headers are not copied to recreated commits.

The explicit path allowlist and exact private-email checks supplement content
review; they are not a general detector of participant information or secrets.
"""

import argparse
import json
import re
import subprocess
from pathlib import Path


PUBLIC_REF = "refs/heads/codex/public-release"
PUBLIC_FILES = {
    ".gitignore", "LOG.md", "PLAN.md", "README.md", "requirements.txt",
    "part1/__main__.py", "part1/data.py", "part1/models.py", "part1/quiz.py",
    "part2/__main__.py", "part2/analysis.py", "part2/app.py", "part2/config.json",
    "part2/protocol.py", "part2/provider.py", "part2/storage.py",
    "part2/static/pilot.css", "part2/static/pilot.js", "part2/templates/pilot.html",
    "reports/part1.md", "reports/gate2-research.md",
    "reports/figures/gss-music-quiz.png", "reports/figures/young-combined-quiz.png",
    "reports/figures/young-film-quiz.png", "reports/figures/young-hobbies-quiz.png",
    "reports/figures/young-music-quiz.png", "scripts/reproduce_gate2_math.py",
    "scripts/prepare_public_release.py",
}


def git(root, *args, data=None, required=True):
    result = subprocess.run(
        ["git", "-C", str(root), *args], input=data, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, check=False,
    )
    if required and result.returncode:
        raise RuntimeError(f"Git operation failed: {args[0]}")
    return result


def text(root, *args):
    return git(root, *args).stdout.decode("utf-8").strip()


def commit_parts(root, oid):
    raw = git(root, "cat-file", "commit", oid).stdout
    headers, separator, message = raw.partition(b"\n\n")
    if not separator:
        raise RuntimeError("A source commit has no message separator.")
    fields = {}
    for line in headers.splitlines():
        if line.startswith(b" "):
            continue
        key, _, value = line.partition(b" ")
        fields.setdefault(key, []).append(value)
    for field in (b"tree", b"author", b"committer"):
        if len(fields.get(field, [])) != 1:
            raise RuntimeError("A source commit has unexpected identity or tree headers.")
    return fields, message


def identity_parts(value):
    match = re.fullmatch(rb"(.+) <([^<>]+)> (-?\d+ [+-]\d{4})", value)
    if not match:
        raise RuntimeError("A source commit has an unsupported identity header.")
    return match.groups()


def audit(root, source, public_identity):
    commits = text(root, "rev-list", "--reverse", "--topo-order", source).splitlines()
    records, private_emails, private_commits, blobs = {}, set(), set(), set()
    current_files = set()
    for oid in commits:
        fields, message = commit_parts(root, oid)
        records[oid] = (fields, message)
        for field in (b"author", b"committer"):
            name, email, _ = identity_parts(fields[field][0])
            if name + b" <" + email + b">" != public_identity:
                private_commits.add(oid)
            if email != public_identity.split(b"<", 1)[1][:-1]:
                private_emails.add(email)
        listing = git(root, "ls-tree", "-r", "-z", oid).stdout.split(b"\0")
        paths = set()
        for entry in listing:
            if not entry:
                continue
            attributes, _, raw_path = entry.partition(b"\t")
            mode, kind, blob = attributes.split()
            path = raw_path.decode("utf-8")
            if path not in PUBLIC_FILES or mode not in (b"100644", b"100755") or kind != b"blob":
                raise RuntimeError("A historical path, symlink or submodule is outside the public allowlist.")
            paths.add(path)
            blobs.add(blob.decode("ascii"))
        if oid == source:
            current_files = paths
    for fields, message in records.values():
        if any(email in message for email in private_emails):
            raise RuntimeError("Private identity information occurs in a commit message.")
    for oid in blobs:
        content = git(root, "cat-file", "blob", oid).stdout
        if any(email in content for email in private_emails):
            raise RuntimeError("Private identity information occurs in a tracked file.")
    return commits, records, private_commits, current_files


def prepare(root, source, identity, commits, records, private_commits):
    active_ref = git(root, "symbolic-ref", "-q", "HEAD", required=False).stdout.strip()
    if active_ref == PUBLIC_REF.encode():
        raise RuntimeError("Run preparation from the original study branch, not the public branch.")
    original_head = text(root, "rev-parse", "HEAD")
    previous = git(root, "rev-parse", "--verify", PUBLIC_REF, required=False)
    old_public = previous.stdout.decode().strip() if not previous.returncode else "0" * len(source)
    mapping = {}
    for oid in commits:
        fields, message = records[oid]
        lines = [b"tree " + fields[b"tree"][0]]
        for parent in fields.get(b"parent", []):
            parent_oid = parent.decode("ascii")
            if parent_oid not in mapping:
                raise RuntimeError("A source parent was not recreated before its child.")
            lines.append(b"parent " + mapping[parent_oid].encode("ascii"))
        for field in (b"author", b"committer"):
            _, _, date = identity_parts(fields[field][0])
            lines.append(field + b" " + identity + b" " + date)
        for encoding in fields.get(b"encoding", []):
            lines.append(b"encoding " + encoding)
        raw = b"\n".join(lines) + b"\n\n" + message
        new_oid = git(root, "hash-object", "-t", "commit", "-w", "--stdin", data=raw).stdout.decode().strip()
        recreated_fields, recreated_message = commit_parts(root, new_oid)
        if recreated_fields[b"tree"] != fields[b"tree"] or recreated_message != message:
            raise RuntimeError("A recreated commit changed its tree or message.")
        mapping[oid] = new_oid
    public_head = mapping[source]
    reachable = set(text(root, "rev-list", public_head).splitlines())
    if len(reachable) != len(commits) or reachable.intersection(private_commits):
        raise RuntimeError("The public graph still reaches a private commit or has the wrong size.")
    for oid in reachable:
        fields, _ = commit_parts(root, oid)
        for field in (b"author", b"committer"):
            name, email, _ = identity_parts(fields[field][0])
            if name + b" <" + email + b">" != identity:
                raise RuntimeError("The public graph has unexpected identity metadata.")
    if text(root, "rev-parse", "HEAD") != original_head:
        raise RuntimeError("The working branch changed during preparation.")
    git(root, "update-ref", PUBLIC_REF, public_head, old_public)
    return public_head


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default="HEAD", help="Original local commit or branch to publish.")
    parser.add_argument("--author-name", required=True, help="Public GitHub login or chosen public author name.")
    parser.add_argument("--author-email", required=True, help="GitHub-provided no-reply email address.")
    parser.add_argument("--write", action="store_true", help="Create or update the separate local public branch.")
    args = parser.parse_args()
    if not args.author_name.strip() or re.search(r"[\r\n<>]", args.author_name):
        parser.error("Use a nonempty public author name without control characters or angle brackets.")
    if not re.fullmatch(r"[A-Za-z0-9+._-]+@users\.noreply\.github\.com", args.author_email):
        parser.error("Use a GitHub-provided no-reply email address.")
    try:
        root = Path(text(Path.cwd(), "rev-parse", "--show-toplevel"))
        if git(root, "status", "--porcelain", "--untracked-files=normal").stdout:
            raise RuntimeError("Commit or set aside reviewed working changes before preparing a release.")
        source = text(root, "rev-parse", "--verify", "--end-of-options", args.source + "^{commit}")
        identity = f"{args.author_name} <{args.author_email}>".encode("utf-8")
        commits, records, private_commits, files = audit(root, source, identity)
        result = {
            "mode": "prepare" if args.write else "check",
            "source_head": source,
            "source_commits": len(commits),
            "public_file_count": len(files),
            "public_files": sorted(files),
            "public_identity": identity.decode("utf-8"),
            "source_commits_requiring_identity_replacement": len(private_commits),
        }
        if args.write:
            result["public_ref"] = PUBLIC_REF
            result["public_head"] = prepare(root, source, identity, commits, records, private_commits)
            result["working_head_unchanged"] = True
            result["private_metadata_reachable"] = False
        print(json.dumps(result, indent=2))
    except (RuntimeError, UnicodeError, ValueError) as error:
        parser.exit(1, str(error) + "\n")


if __name__ == "__main__":
    main()
