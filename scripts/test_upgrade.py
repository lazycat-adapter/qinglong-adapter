"""Accept a running image using fresh and migrated, disposable workspaces."""

import argparse
from contextlib import contextmanager
from http.client import HTTPConnection, HTTPException
import json
import re
import secrets
import subprocess
import time
from urllib.parse import urlsplit

import yaml

from contract import ROOT, VERSION
from registry import inspect_image, pull

BASELINE = (
    "registry.lazycat.cloud/qinglong@"
    "sha256:9ffda9b6a291bfc73f070dfe6512e7da057199017aded6169a5b8c091073e07f"
)
LEGACY_VERSION = "2.17.11"
SCRIPT_NAME = "lazycat-adapter-fixture.sh"
SCRIPT = '#!/bin/bash\nprintf "%s\\n" "$QL_ADAPTER_FIXTURE" > /ql/data/scripts/adapter-ran.txt\n'


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def docker(*args, check=True, timeout=65):
    result = subprocess.run(
        ["docker", *args], text=True, capture_output=True, timeout=timeout
    )
    if check and result.returncode:
        raise RuntimeError(
            f"Docker {args[0]} {args[1]} failed: {result.stderr.strip()}"
        )
    return result.stdout.strip()


class Panel:
    def __init__(self, name, port):
        self.name = name
        self.port = port
        self.token = None

    def request(self, path, method="GET", body=None, authenticated=True):
        conn = HTTPConnection("127.0.0.1", self.port, timeout=10)
        headers = {"Accept-Language": "en-US"}
        if body is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(body)
        if authenticated and self.token:
            headers["Authorization"] = "Bearer " + self.token
        try:
            conn.request(method, path, body, headers)
            response = conn.getresponse()
            return response.status, response.read()
        finally:
            conn.close()

    def api(self, path, method="GET", body=None):
        status, raw = self.request("/api/" + path, method, body)
        result = json.loads(raw)
        require(
            status == 200 and result.get("code") == 200,
            f"{method} /api/{path}: HTTP {status}, QingLong code {result.get('code')}",
        )
        return result.get("data")

    def login(self, credentials):
        self.token = None
        self.token = self.api("user/login", "POST", credentials)["token"]
        require(
            self.token and self.api("user")["username"] == credentials["username"],
            "Session does not belong to the expected user",
        )

    def reject(self, path, method="GET", body=None):
        status, raw = self.request("/api/" + path, method, body, authenticated=False)
        require(
            status in (400, 401, 403)
            or status == 200
            and json.loads(raw).get("code") != 200,
            f"{method} /api/{path} unexpectedly accepted anonymous/default credentials",
        )

    def verify_static(self, revision):
        status, raw = self.request("/")
        require(
            status == 200 and b"<html" in raw.lower(), "Panel did not serve index.html"
        )
        scripts = re.findall(rb'<script[^>]+src=["\']([^"\']+)["\']', raw)
        bundles = [
            item.decode()
            for item in scripts
            if re.fullmatch(rb"\.?/?umi\.[a-z0-9]+\.js", item)
        ]
        require(bool(bundles), "Compiled Umi bundle missing from index.html")
        for url in bundles:
            path = "/" + urlsplit(url).path.lstrip("./")
            status, body = self.request(path)
            require(
                status == 200 and len(body) > 1000,
                "Compiled frontend asset is unavailable",
            )
        proof = json.loads(
            docker("exec", self.name, "cat", "/ql/static/build-info.json")
        )
        require(
            proof["sourceCommit"] == revision,
            "Build information points at a different Git revision",
        )
        require(
            docker("exec", self.name, "test", "-s", "/ql/static/build/app.js") == "",
            "Compiled backend is missing",
        )


@contextmanager
def workspace():
    volume = "qinglong-adapter-qa-" + secrets.token_hex(10)
    docker("volume", "create", volume)
    try:
        yield volume
    finally:
        docker("volume", "rm", volume)


def import_workspace(image, volume, legacy=None):
    manifest = yaml.safe_load((ROOT / "lzc-manifest.yml").read_text())
    setup_script = manifest["services"]["gateway"]["setup_script"]
    mounts = [
        "--mount",
        f"type=bind,src={ROOT / 'content'},dst=/lzcapp/pkg/content,readonly",
        "--mount",
        f"type=volume,src={volume},dst=/ql/data,volume-nocopy",
    ]
    if legacy:
        mounts += [
            "--mount",
            f"type=volume,src={legacy},dst=/lzcapp/run/mnt/home/.ql,readonly,volume-nocopy",
        ]
    docker(
        "run", "--rm", "--pull", "never", "--network", "none",
        *mounts, "--entrypoint", "/bin/sh", image, "-c", setup_script,
        timeout=120,
    )


@contextmanager
def started(image, volume, version):
    name = "qinglong-adapter-qa-" + secrets.token_hex(10)
    try:
        docker(
            "run",
            "-d",
            "--pull",
            "never",
            "--name",
            name,
            "--memory",
            "1024m",
            "--cpus",
            "1.5",
            "--publish",
            "127.0.0.1::5700",
            "--mount",
            f"type=volume,src={volume},dst=/ql/data,volume-nocopy",
            image,
        )
        port = int(docker("port", name, "5700/tcp").rsplit(":", 1)[1])
        panel = Panel(name, port)
        deadline = time.monotonic() + 145
        while True:
            try:
                system = panel.api("system")
                break
            except (OSError, HTTPException, ValueError, RuntimeError):
                if (
                    time.monotonic() >= deadline
                    or docker("inspect", "--format", "{{.State.Running}}", name)
                    != "true"
                ):
                    raise RuntimeError(f"QingLong {version} did not start")
                time.sleep(2)
        require(
            str(system["version"]).lstrip("v") == version,
            f"Unexpected running release, wanted {version}",
        )
        yield panel
    finally:
        docker("stop", "--time", "20", name, check=False)
        docker("rm", "--force", name, check=False)


def credentials(user):
    return {"username": user, "password": secrets.token_hex(20)}


def seed(legacy, account, marker):
    legacy.login({"username": "admin", "password": "admin"})
    legacy.api("user", "PUT", account)
    legacy.login(account)
    legacy.api("envs", "POST", [{"name": "QL_ADAPTER_FIXTURE", "value": marker}])
    legacy.api("scripts", "POST", {"filename": SCRIPT_NAME, "content": SCRIPT})
    task = legacy.api(
        "crons",
        "POST",
        {
            "name": "LazyCat adapter migration",
            "command": f"task {SCRIPT_NAME}",
            "schedule": "0 0 1 1 *",
        },
    )
    return task["id"]


def rows(result):
    for key in ("data", "list"):
        if isinstance(result, dict) and isinstance(result.get(key), list):
            return result[key]
    return result


def verify_workspace(panel, account, marker, task):
    require(
        panel.api("system")["isInitialized"] is True,
        "Existing instance requires reinitialization",
    )
    panel.reject("envs")
    panel.reject("user/init", "PUT", credentials("attempted-reset"))
    panel.login(account)
    envs = rows(panel.api("envs"))
    require(
        any(x["name"] == "QL_ADAPTER_FIXTURE" and x["value"] == marker for x in envs),
        "Legacy environment variable did not survive",
    )
    tasks = rows(panel.api("crons"))
    require(
        any(x["id"] == task and x["command"] == f"task {SCRIPT_NAME}" for x in tasks),
        "Legacy scheduled task did not survive",
    )
    require(
        panel.api("scripts/detail?file=" + SCRIPT_NAME) == SCRIPT,
        "Legacy script did not survive",
    )


def initialize(panel):
    require(
        panel.api("system")["isInitialized"] is False,
        "Workspace with default credentials must request setup",
    )
    panel.reject("user/login", "POST", {"username": "admin", "password": "admin"})
    account = credentials("admin")
    panel.api("user/init", "PUT", account)
    panel.login(account)
    panel.reject("user/init", "PUT", credentials("second-user"))


def exercise(candidate, version):
    labels = json.loads(
        docker("image", "inspect", "--format", "{{json .Config.Labels}}", candidate)
    )
    revision = labels["org.opencontainers.image.revision"]
    old = pull(inspect_image(BASELINE))
    account = credentials("no-default-user")
    marker = secrets.token_hex(12)
    with workspace() as legacy_data, workspace() as volume:
        print(
            f"Validating legacy {LEGACY_VERSION} → {version} across isolated data volumes",
            flush=True,
        )
        with started(old, legacy_data, LEGACY_VERSION) as legacy:
            task = seed(legacy, account, marker)
        import_workspace(candidate, volume, legacy_data)
        with started(candidate, volume, version) as upgraded:
            verify_workspace(upgraded, account, marker, task)
            upgraded.verify_static(revision)
            upgraded.api("crons/run", "PUT", [task])
            deadline = time.monotonic() + 40
            while (
                docker(
                    "exec",
                    upgraded.name,
                    "cat",
                    "/ql/data/scripts/adapter-ran.txt",
                    check=False,
                )
                != marker
            ):
                require(
                    time.monotonic() < deadline,
                    "Migrated task did not run with its persisted variable",
                )
                time.sleep(1)
        import_workspace(candidate, volume, legacy_data)
        with started(candidate, volume, version) as restarted:
            verify_workspace(restarted, account, marker, task)
    with workspace() as legacy_default, workspace() as migrated_default:
        with started(old, legacy_default, LEGACY_VERSION) as legacy:
            legacy.login({"username": "admin", "password": "admin"})
            legacy.api(
                "envs", "POST", [{"name": "QL_ADAPTER_FIXTURE", "value": marker}]
            )
        import_workspace(candidate, migrated_default, legacy_default)
        with started(candidate, migrated_default, version) as upgraded:
            initialize(upgraded)
            require(
                any(
                    x["name"] == "QL_ADAPTER_FIXTURE" and x["value"] == marker
                    for x in rows(upgraded.api("envs"))
                ),
                "Legacy data did not survive replacing the default credentials",
            )
    with workspace() as fresh:
        import_workspace(candidate, fresh)
        with started(candidate, fresh, version) as clean:
            initialize(clean)
            require(
                not any(
                    x["name"] == "QL_ADAPTER_FIXTURE" for x in rows(clean.api("envs"))
                ),
                "Fresh instance shares another user's data",
            )
    print(
        "PASS: initialization, credentials, per-user data, persisted task execution, restart and paired static assets",
        flush=True,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image")
    parser.add_argument("version")
    args = parser.parse_args()
    if not VERSION.fullmatch(args.version):
        parser.error("Provide a stable release version")
    exercise(args.image, args.version)
