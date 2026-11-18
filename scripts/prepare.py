"""Compile and test a QingLong image from the exact stable GitHub release."""

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from contract import CHECKS, DIGEST, REVISION, ROOT, STAGING, VERSION, recipe_hash
from registry import inspect_image, pull, verify_local


def run(*args, cwd=ROOT, timeout=60):
    subprocess.run(args, cwd=cwd, timeout=timeout, check=True)


def capture(*args, cwd=ROOT, timeout=45):
    return subprocess.check_output(args, cwd=cwd, timeout=timeout, text=True).strip()


def source_checkout(env):
    version = env.get("LAZYCAT_SOURCE_VERSION", "")
    revision = env.get("LAZYCAT_SOURCE_REVISION", "")
    app_version = env.get("LAZYCAT_APPLICATION_VERSION", "")
    if (
        not VERSION.fullmatch(version)
        or env.get("LAZYCAT_SOURCE_REF") != "refs/tags/v" + version
        or not REVISION.fullmatch(revision)
        or not VERSION.fullmatch(app_version)
        or env.get("LAZYCAT_TARGET_PLATFORM") != "linux/amd64"
    ):
        raise ValueError(
            "Expected a stable upstream Git tag, exact revision and amd64 target"
        )
    return version, revision, app_version


def export_context(checkout, context):
    # A shallow local clone preserves the Git metadata required by QingLong's
    # build:info command. No upstream credentials or untracked files enter Docker.
    context.mkdir()
    source = context / "src"
    run(
        "git",
        "clone",
        "--quiet",
        "--depth",
        "1",
        "--single-branch",
        checkout.as_uri(),
        str(source),
        timeout=120,
    )
    run("git", "remote", "remove", "origin", cwd=source)
    run("git", "apply", "--check", str(ROOT / "image" / "first-run.patch"), cwd=source)
    run("git", "apply", str(ROOT / "image" / "first-run.patch"), cwd=source)
    shutil.copyfile(ROOT / "image" / "Dockerfile", context / "Dockerfile")
    return source


def pinned_runtime(version, source_tree):
    base = inspect_image(f"docker.io/whyour/qinglong:{version}")
    local = pull(base)
    # The image publisher may build a merge commit with an identical tree.
    # A matching version or revision label alone does not prove code/lockfile parity.
    tree = capture(
        "docker",
        "run",
        "--rm",
        "--pull",
        "never",
        "--network",
        "none",
        "--entrypoint",
        "git",
        local,
        "-C",
        "/ql",
        "rev-parse",
        "HEAD^{tree}",
    )
    if tree != source_tree:
        raise ValueError(
            "Docker Hub runtime base does not contain the same tree as the GitHub release"
        )
    return base["pinned_ref"]


def build(checkout, version, revision, work):
    actual_rev = capture("git", "rev-parse", "HEAD", cwd=checkout)
    if actual_rev != revision:
        raise ValueError("Source checkout differs from the selected release revision")
    source_tree = capture("git", "rev-parse", "HEAD^{tree}", cwd=checkout)
    runtime = pinned_runtime(version, source_tree)
    context = work / "context"
    source = export_context(checkout, context)
    build_node = (source / ".nvmrc").read_text().strip().removeprefix("v")
    package_manager = json.loads((source / "package.json").read_text()).get(
        "packageManager", ""
    )
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]+){0,2}", build_node) or not re.fullmatch(
        r"pnpm@[0-9]+\.[0-9]+\.[0-9]+", package_manager
    ):
        raise ValueError(
            "Upstream must specify a numeric Node version and an exact pnpm version"
        )
    fingerprint = recipe_hash()
    candidate = f"qinglong-adapter:qa-{revision[:10]}-{fingerprint[7:15]}"
    run(
        "docker",
        "build",
        "--platform",
        "linux/amd64",
        "--provenance=false",
        "--tag",
        candidate,
        "--build-arg",
        f"BUILD_IMAGE=docker.io/library/node:{build_node}-bookworm",
        "--build-arg",
        f"RUNTIME_IMAGE={runtime}",
        "--build-arg",
        f"PNPM_VERSION={package_manager.split('@')[1]}",
        "--build-arg",
        f"SOURCE_REVISION={revision}",
        "--build-arg",
        f"SOURCE_VERSION={version}",
        "--build-arg",
        f"RECIPE={fingerprint}",
        str(context),
        timeout=1800,
    )
    return candidate, fingerprint


def prepare(args):
    from test_upgrade import exercise

    with tempfile.TemporaryDirectory(prefix="qinglong-release-") as directory:
        work = Path(directory)
        if args.test_tag is not None:
            if not args.test_tag.startswith("v") or not VERSION.fullmatch(
                args.test_tag[1:]
            ):
                raise ValueError("Pass a stable GitHub release tag such as v2.24.0")
            version = args.test_tag[1:]
            checkout = work / "checkout"
            run(
                "git",
                "clone",
                "--branch",
                args.test_tag,
                "--depth",
                "1",
                "--single-branch",
                "https://github.com/whyour/qinglong.git",
                str(checkout),
                timeout=120,
            )
            revision = capture("git", "rev-parse", "HEAD", cwd=checkout)
            app_version = version
            output = ""
        else:
            version, revision, app_version = source_checkout(os.environ)
            checkout = Path(os.environ["LAZYCAT_SOURCE_DIR"]).resolve()
            output = os.environ.get("LAZYCAT_OUTPUT_IMAGE", "")
            if not STAGING.fullmatch(output) or not DIGEST.fullmatch(
                os.environ.get("LAZYCAT_BUILD_FINGERPRINT", "")
            ):
                raise ValueError(
                    "Expected lazycat-action's generated anonymous staging image"
                )
        receipt = ROOT / ".lazycat-action" / "upgrade-proof.json"
        if output:
            receipt.unlink(missing_ok=True)
        candidate, fingerprint = build(checkout, version, revision, work)
        local = json.loads(capture("docker", "image", "inspect", candidate))[0]
        exercise(local["Id"], version)
        if not output:
            print(f"PASS: image {candidate} compiled and verified locally.", flush=True)
            return
        if recipe_hash() != fingerprint:
            raise ValueError("Image recipe changed during acceptance")
        run("docker", "tag", local["Id"], output)
        run("docker", "push", output, timeout=900)
        staged = inspect_image(output)
        verify_local(staged, local)
        proof = {
            "source_revision": revision,
            "source_version": version,
            "application_version": app_version,
            "staged_ref": staged["pinned_ref"],
            "content_sha256": staged["content_sha256"],
            "recipe": fingerprint,
            "checks": CHECKS,
        }
        receipt.parent.mkdir(exist_ok=True)
        receipt.write_text(json.dumps(proof, indent=2) + "\n")
        print(f"Verified staging image: {proof['staged_ref']}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--test-tag",
        help="Build and verify a stable release locally without publishing",
    )
    prepare(parser.parse_args())
