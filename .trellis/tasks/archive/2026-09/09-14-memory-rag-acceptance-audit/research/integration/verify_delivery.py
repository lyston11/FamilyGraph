"""Read-only integrity check for the frozen memory/RAG acceptance delivery."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import subprocess
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    audit = root / args.audit

    def git(*argv: str) -> bytes:
        return subprocess.check_output(["git", *argv], cwd=root)

    manifests = []
    for rel, base in (("research/artifacts.json", audit),
                      ("research/final/artifacts.json", audit / "research/final")):
        file_count = raw_count = 0
        records = json.loads((audit / rel).read_text())["artifacts"]
        assert len({record["file"] for record in records}) == len(records)
        for record in records:
            path = base / record["file"]
            raw = path.read_bytes()
            assert hashlib.sha256(raw).hexdigest() == record["sha256"], path
            file_count += 1
            if path.suffix == ".gz":
                assert hashlib.sha256(gzip.decompress(raw)).hexdigest() == record["original_sha256"], path
                raw_count += 1
        manifests.append({"manifest": rel, "files_passed": file_count, "decompressed_passed": raw_count})

    checkpoint = json.loads((audit / "research/final/code-checkpoint.json").read_text())
    package_counts = {}
    for package, expected_tree in checkpoint["package_trees"].items():
        assert git("rev-parse", f"HEAD:{package}").decode().strip() == expected_tree, package
        assert git("rev-parse", f"{checkpoint['code_commit']}:{package}").decode().strip() == expected_tree, package
        entries = git("ls-tree", "-r", "-z", "HEAD", "--", package).split(b"\0")
        count = 0
        for entry in entries:
            if not entry:
                continue
            meta, name = entry.split(b"\t", 1)
            mode, kind, expected_blob = meta.decode().split()
            assert kind == "blob", entry
            path = root / os.fsdecode(name)
            if mode == "120000":
                assert path.is_symlink(), path
                raw = os.fsencode(os.readlink(path))
            else:
                assert path.is_file() and not path.is_symlink(), path
                raw = path.read_bytes()
                assert bool(path.stat().st_mode & 0o111) == (mode == "100755"), path
            blob = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
            assert blob == expected_blob, path
            count += 1
        package_counts[package] = count
    result = {"head": git("rev-parse", "HEAD").decode().strip(),
              "code_commit": checkpoint["code_commit"],
              "audit_path": str(args.audit), "artifacts": manifests,
              "package_trees": checkpoint["package_trees"],
              "working_source_files_passed": package_counts,
              "passed": True}
    args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
