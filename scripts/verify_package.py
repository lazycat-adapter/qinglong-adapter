"""Run the final image integrity check after official transfer and before LPK assembly."""

import json

from contract import (
    ROOT,
    recipe_hash,
    source_binding,
    validate_manifest,
    validate_proof,
)
from registry import inspect_image


def verify():
    package, mirror = validate_manifest()
    proof = json.loads((ROOT / ".lazycat-action" / "upgrade-proof.json").read_text())
    source = source_binding((ROOT / "lzc-manifest.yml").read_text())
    validate_proof(
        proof, source, mirror, inspect_image(mirror), recipe_hash(), package["version"]
    )
    print(
        "PASS: immutable custom image, official mirror, package version and upgrade proof match.",
        flush=True,
    )


if __name__ == "__main__":
    verify()
