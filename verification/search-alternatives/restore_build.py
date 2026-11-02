"""Restore exact measured libraries from a preserved directory, never rebuild or overwrite."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile

WORK = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WORK))
from experiments.search_alternatives import run as cycle


def digest(data):
    return hashlib.sha256(data).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("backup", type=Path, help="directory with the five archived libraries")
    backup = parser.parse_args().backup.resolve()
    evidence = Path(__file__).resolve().parent
    manifest = json.loads((evidence / "build-manifest.json").read_text())
    frozen = cycle.read(cycle.ROOT / "finalist.json")
    assert manifest["sources"] == cycle.sources(), "source changed"
    raw_manifest = json.dumps(manifest, ensure_ascii=False, indent=1, allow_nan=False).encode()
    assert digest(raw_manifest) == frozen["hashes"]["experiments/search_alternatives/build/manifest.json"]
    artifacts = {}
    for arm, record in manifest["engines"].items():
        library = (backup / (arm + Path(cycle.LIBRARY).suffix)).read_bytes()
        source = (evidence / (arm + ".cpp")).read_bytes()
        assert digest(library) == record["sha256"] and digest(source) == record["source_sha256"], arm
        artifacts[arm] = (library, source)
    with cycle.lease():
        build = cycle.ROOT / "build"
        if build.exists():
            cycle.check_build()
            print("Existing build verified; no files replaced")
            return
        temporary = Path(tempfile.mkdtemp(prefix="restore-", dir=cycle.ROOT))
        try:
            for arm, (library, source) in artifacts.items():
                target = temporary / arm
                target.mkdir()
                (target / cycle.LIBRARY).write_bytes(library)
                (target / "engine.cpp").write_bytes(source)
            (temporary / "manifest.json").write_bytes(raw_manifest)
            (temporary / "intent.json").write_text(json.dumps(manifest["sources"], indent=1))
            temporary.rename(build)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
        cycle.check_build()
        print("Restored five exact measured libraries; no games run")


if __name__ == "__main__":
    main()
