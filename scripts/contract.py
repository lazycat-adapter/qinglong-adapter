"""Deployment compatibility and the receipt for a tested image."""

import hashlib
import json
from pathlib import Path
import re

import yaml

DIGEST = re.compile(r"sha256:[a-f0-9]{64}")
REVISION = re.compile(r"[a-f0-9]{40}")
VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")
STAGING = re.compile(r"ttl\.sh/qinglong-lazycat-[a-f0-9]{12}:24h")
MIRROR = re.compile(r"registry\.lazycat\.cloud/[a-z0-9_./-]+:[a-f0-9]{16,64}")
ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "cloud.lazycat.app.qinglong"
CHECKS = ["upgrade", "fresh-install"]


def recipe_hash(root=ROOT):
    digest = hashlib.sha256()
    paths = sorted(
        path
        for folder in ("content", "image", "scripts")
        for path in (root / folder).glob("*")
        if path.is_file()
    )
    paths.extend(
        root / name for name in ("lazycat-action.yml", "lzc-build.yml", "icon.png")
    )
    for path in paths:
        digest.update(path.relative_to(root).as_posix().encode() + b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    # The reusable Action may reserialize this YAML while replacing gateway.image.
    manifest = yaml.safe_load((root / "lzc-manifest.yml").read_text())
    manifest["services"]["gateway"]["image"] = None
    digest.update(json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode())
    return "sha256:" + digest.hexdigest()


def validate_manifest(root=ROOT):
    package = yaml.safe_load((root / "package.yml").read_text())
    manifest = yaml.safe_load((root / "lzc-manifest.yml").read_text())
    if package.get("package") != PACKAGE or package.get("min_os_version") != "1.5.0":
        raise ValueError("Wrong package identity or minimum LzcOS for LPK v2")
    application = dict(manifest.get("application", {}))
    injects = application.pop("injects", [])
    if [rule.get("on") for rule in injects] != [
        "request",
        "response",
        "browser",
    ] or not all(rule.get("auth_required") is True for rule in injects):
        raise ValueError(
            "Missing the authenticated three-stage LazyCat login integration"
        )
    if application != {
        "subdomain": "qinglong",
        "background_task": True,
        "multi_instance": True,
        "routes": [f"/=http://gateway.{PACKAGE}.lzcapp:5700"],
    }:
        raise ValueError("Published per-user routing or background execution changed")
    services = manifest.get("services", {})
    gateway = services.get("gateway", {})
    if (
        set(services) != {"gateway"}
        or set(gateway) != {"binds", "image", "setup_script"}
        or gateway["binds"] != ["/lzcapp/var/qinglong:/ql/data"]
        or gateway["setup_script"] != (
            "set -e\n"
            "/bin/sh /lzcapp/pkg/content/migrate-workspace.sh "
            "/lzcapp/run/mnt/home/.ql /ql/data"
        )
        or manifest.get("ext_config") != {"enable_document_access": True}
        or package.get("permissions") != {"optional": ["document.read"]}
    ):
        raise ValueError("Per-user workspace or legacy import configuration changed")
    return package, gateway["image"]


def source_binding(text):
    # The reusable executor inserts the immutable source annotation directly
    # above gateway.image during official delivery.
    matches = re.findall(r"(?m)^    # upstream: (\S+)[ \t]*\n    image: ", text)
    if len(matches) != 1:
        raise ValueError("Missing immutable source annotation for gateway")
    return matches[0]


def validate_proof(proof, source, mirror, image, recipe, version):
    if (
        proof.get("checks") != CHECKS
        or proof.get("recipe") != recipe
        or proof.get("application_version") != version
    ):
        raise ValueError(
            "Missing fresh-install and migration checks for this recipe/version"
        )
    tag, _, digest = source.partition("@")
    if (
        not STAGING.fullmatch(tag)
        or not DIGEST.fullmatch(digest)
        or source != proof.get("staged_ref")
    ):
        raise ValueError("Source digest differs from the tested custom image")
    if not MIRROR.fullmatch(mirror):
        raise ValueError("Expected the official LazyCat registry reference")
    if (
        not DIGEST.fullmatch(proof.get("content_sha256", ""))
        or image.get("content_sha256") != proof["content_sha256"]
    ):
        raise ValueError(
            "Official copy differs from the tested filesystem or runtime configuration"
        )
    labels = image.get("config", {}).get("config", {}).get("Labels") or {}
    if (
        not REVISION.fullmatch(proof.get("source_revision", ""))
        or not VERSION.fullmatch(proof.get("source_version", ""))
        or labels.get("org.opencontainers.image.revision") != proof["source_revision"]
        or labels.get("org.opencontainers.image.version") != proof["source_version"]
        or labels.get("cloud.lazycat.qinglong.recipe") != recipe
    ):
        raise ValueError("Delivered image does not contain the selected Git release")
