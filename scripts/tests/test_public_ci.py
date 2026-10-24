"""The public repository's CI workflow (ci/public-ci.yml here; .github/workflows/ci.yml in the public export).

The development repository runs no GitHub Actions, so this test is the only check the workflow gets before the
export: it parses the file, checks the triggers and the least-privilege token, that every third-party action is pinned
to a full commit SHA, that every `make` target a step runs exists in the Makefile or an mk/ fragment, and that the
tool versions it installs are the ones the repository records (Foundry in contracts/README.md, Slither in
contracts/slither.md, Playwright through make e2e-setup). Every check is also run on a broken copy and must fail.

The workflow is parsed by a small block-YAML reader (pipeline/requirements.txt has no YAML library). It refuses
anything outside the block subset the workflow uses, so the file cannot drift into syntax it does not understand; when
PyYAML or Ruby's Psych is present, the result is also compared with a real YAML parser.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CANDIDATES = [ROOT / "ci" / "public-ci.yml", ROOT / ".github" / "workflows" / "ci.yml"]
WORKFLOW = next((p for p in CANDIDATES if p.is_file()), CANDIDATES[0])
SHA_PIN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_./-]+@[0-9a-f]{40}$")
REQUIRED_TARGETS = {"setup", "check", "e2e-setup", "e2e", "slither"}


# --- a block-YAML reader for the subset the workflow uses -------------------------------------------------------

class YamlSubsetError(ValueError):
    pass


def _strip_comment(s: str) -> str:
    """Drop a trailing ' # comment' outside quotes."""
    quote = None
    for i, ch in enumerate(s):
        if quote:
            if ch == quote:
                quote = None
        elif ch in "'\"":
            quote = ch
        elif ch == "#" and (i == 0 or s[i - 1] in " \t"):
            return s[:i].rstrip()
    return s.rstrip()


def _scalar(s: str):
    s = s.strip()
    if not s:
        return None
    if s[0] in "[{&*!%@`|>":
        raise YamlSubsetError(f"unsupported YAML construct: {s!r}")
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "'\"":
        return s[1:-1]
    if s in ("true", "false"):
        return s == "true"
    if re.fullmatch(r"-?\d+", s):
        return int(s)
    return s


def parse_block_yaml(text: str):
    """Block mappings, block sequences, plain/quoted scalars and `|` literal blocks; nothing else."""
    raw = text.splitlines()
    for n, line in enumerate(raw):
        if "\t" in line[: len(line) - len(line.lstrip())]:
            raise YamlSubsetError(f"line {n + 1}: tab indentation")
        if line.strip() in ("---", "..."):
            raise YamlSubsetError(f"line {n + 1}: document markers are not used")

    def next_line(i: int) -> int:
        while i < len(raw) and (not raw[i].strip() or raw[i].lstrip().startswith("#")):
            i += 1
        return i

    def indent_of(i: int) -> int:
        return len(raw[i]) - len(raw[i].lstrip(" "))

    def literal(i: int, parent_indent: int):
        out, block_indent = [], None
        while i < len(raw):
            line = raw[i]
            if line.strip() == "":
                out.append("")
                i += 1
                continue
            ind = indent_of(i)
            if ind <= parent_indent:
                break
            if block_indent is None:
                block_indent = ind
            out.append(line[block_indent:])
            i += 1
        while out and out[-1] == "":
            out.pop()
        return "\n".join(out) + "\n", i

    def value_after_key(rest: str, i: int, ind: int):
        rest = _strip_comment(rest)
        if rest == "|":
            return literal(i + 1, ind)
        if rest:
            return _scalar(rest), i + 1
        j = next_line(i + 1)
        if j < len(raw) and indent_of(j) > ind:
            return node(j, indent_of(j))
        if j < len(raw) and indent_of(j) == ind and raw[j].lstrip().startswith("- "):
            return node(j, ind)  # a sequence may sit at its key's indent
        return None, i + 1

    def node(i: int, ind: int):
        i = next_line(i)
        if raw[i].lstrip().startswith("- "):
            return seq(i, ind)
        return mapping(i, ind)

    def mapping(i: int, ind: int, first: str | None = None):
        out = {}
        while True:
            if first is None:
                i = next_line(i)
                if i >= len(raw) or indent_of(i) < ind:
                    return out, i
                if indent_of(i) > ind:
                    raise YamlSubsetError(f"line {i + 1}: unexpected indent")
                content = raw[i].strip()
                if content.startswith("- "):
                    return out, i
            else:
                content, first = first, None
            m = re.fullmatch(r"([A-Za-z0-9_.\-\"']+):(?:\s+(.*))?", content)
            if not m:
                raise YamlSubsetError(f"line {i + 1}: not a 'key: value' line: {content!r}")
            key = _scalar(m.group(1))
            if key in out:
                raise YamlSubsetError(f"line {i + 1}: duplicate key {key!r}")
            out[key], i = value_after_key(m.group(2) or "", i, ind)

    def seq(i: int, ind: int):
        out = []
        while True:
            i = next_line(i)
            if i >= len(raw) or indent_of(i) != ind or not raw[i].lstrip().startswith("- "):
                return out, i
            item = raw[i].strip()[2:]
            item_ind = ind + 2
            if re.match(r"[A-Za-z0-9_.\-\"']+:(\s|$)", item):
                # a mapping whose first key shares the dash line; later keys sit at ind + 2
                val, i = mapping(i, item_ind, first=_strip_comment(item))
                out.append(val)
            else:
                out.append(_scalar(_strip_comment(item)))
                i += 1

    doc, end = node(next_line(0), 0)
    if next_line(end) < len(raw):
        raise YamlSubsetError(f"line {next_line(end) + 1}: content after the document")
    return doc


def real_yaml(text: str):
    """The same file through a real YAML parser (PyYAML, else Ruby's Psych), or None if neither is present."""
    try:
        import yaml  # type: ignore

        return yaml.safe_load(text)
    except ImportError:
        pass
    ruby = shutil.which("ruby")
    if not ruby:
        return None
    out = subprocess.run([ruby, "-ryaml", "-rjson", "-e", "puts JSON.generate(YAML.safe_load(STDIN.read))"],
                         input=text, capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


# --- the checks ---------------------------------------------------------------------------------------------------

def make_targets() -> set[str]:
    targets = set()
    for f in [ROOT / "Makefile", *sorted((ROOT / "mk").glob("*.mk"))]:
        for line in f.read_text().splitlines():
            m = re.match(r"^([A-Za-z0-9_-]+)\s*:(?!=)", line)
            if m:
                targets.add(m.group(1))
    return targets


def make_calls(run: str) -> list[str]:
    """Targets of every `make ...` invocation in a run script (VAR=value and -flags skipped)."""
    calls = []
    for cmd in re.split(r"&&|\|\||;|\n", run):
        words = cmd.split()
        while words and re.fullmatch(r"[A-Z_][A-Z0-9_]*=\S*", words[0]):
            words = words[1:]
        if words and words[0] == "make":
            calls += [w for w in words[1:] if not w.startswith("-") and "=" not in w]
    return calls


def problems(text: str, targets: set[str]) -> list[str]:
    wf = parse_block_yaml(text)
    out = []
    on = wf.get("on") or {}
    for event in ("push", "pull_request"):
        if (on.get(event) or {}).get("branches") != ["main"]:
            out.append(f"trigger {event}: branches must be [main]")
    if set(on) - {"push", "pull_request", "workflow_dispatch"}:
        out.append(f"unexpected triggers: {sorted(set(on) - {'push', 'pull_request', 'workflow_dispatch'})}")
    if wf.get("permissions") != {"contents": "read"}:
        out.append("top-level permissions must be exactly {contents: read}")
    jobs = wf.get("jobs") or {}
    if not jobs:
        out.append("no jobs")
    called = set()
    for name, job in jobs.items():
        if "permissions" in job and job["permissions"] != {"contents": "read"}:
            out.append(f"job {name}: permissions wider than contents: read")
        if job.get("runs-on") != "ubuntu-latest":
            out.append(f"job {name}: runs-on must be ubuntu-latest")
        for step in job.get("steps") or []:
            if "uses" in step:
                uses = step["uses"]
                if not SHA_PIN.match(uses):
                    out.append(f"job {name}: {uses} is not pinned to a full commit SHA")
                line = next((l for l in text.splitlines() if f"uses: {uses}" in l), "")
                if not re.search(r"#\s*v\d+(\.\d+)*\s*$", line):
                    out.append(f"job {name}: {uses} has no '# vX.Y.Z' tag comment")
            if "run" in step:
                for t in make_calls(str(step["run"])):
                    called.add(t)
                    if t not in targets:
                        out.append(f"job {name}: make target {t!r} does not exist")
    missing = REQUIRED_TARGETS - called
    if missing:
        out.append(f"targets never run: {sorted(missing)}")
    if re.search(r"\bsecrets\.\w", text):
        out.append("the workflow reads repository secrets (secrets.*); it must run without any")
    for name, job in jobs.items():
        for step in job.get("steps") or []:
            if str(step.get("uses", "")).startswith("actions/checkout@") and \
                    (step.get("with") or {}).get("persist-credentials") is not False:
                out.append(f"job {name}: checkout without persist-credentials: false")
    out += gitleaks_problems(jobs.get("check") or {})
    slither_runs = [str(s.get("run", "")).strip() for s in (jobs.get("slither") or {}).get("steps") or []]
    after = slither_runs[slither_runs.index("make slither") + 1:] if "make slither" in slither_runs else []
    if SLITHER_DIFF not in after:
        out.append(f"slither job: no '{SLITHER_DIFF}' step after make slither (regenerated report must match)")
    return out


SLITHER_DIFF = "git diff --exit-code -- contracts/slither.md"


def gitleaks_problems(job: dict) -> list[str]:
    """The check job installs a pinned gitleaks release, SHA-256 checked, before make check (so make secrets scans
    instead of skipping), and fetches the full history for it."""
    steps = job.get("steps") or []
    runs = [str(s.get("run", "")) for s in steps]
    install = [i for i, r in enumerate(runs) if "gitleaks" in r and "releases/download/" in r]
    check = [i for i, r in enumerate(runs) if "check" in make_calls(r)]
    if not install:
        return ["check job: gitleaks is not installed, so make secrets would skip"]
    i = install[0]
    env, out = steps[i].get("env") or {}, []
    if not re.fullmatch(r"\d+\.\d+\.\d+", str(env.get("GITLEAKS_VERSION", ""))):
        out.append("check job: gitleaks version must be pinned as GITLEAKS_VERSION: X.Y.Z")
    if not re.fullmatch(r"[0-9a-f]{64}", str(env.get("GITLEAKS_SHA256", ""))):
        out.append("check job: gitleaks checksum must be pinned as GITLEAKS_SHA256 (64 hex)")
    if "sha256sum -c" not in runs[i] or "${GITLEAKS_SHA256}" not in runs[i]:
        out.append("check job: the gitleaks download is not verified with sha256sum -c")
    if not check or i > check[0]:
        out.append("check job: gitleaks must be installed before make check")
    co = next((s for s in steps if str(s.get("uses", "")).startswith("actions/checkout@")), {})
    if (co.get("with") or {}).get("fetch-depth") != 0:
        out.append("check job: checkout needs fetch-depth: 0 so gitleaks scans the whole history")
    return out


def recorded_versions() -> dict[str, str]:
    foundry = re.search(r"Foundry (\d+\.\d+\.\d+)", (ROOT / "contracts" / "README.md").read_text())
    slither = re.search(r"Slither (\d+\.\d+\.\d+)", (ROOT / "contracts" / "slither.md").read_text())
    assert foundry and slither, "Foundry / Slither version not recorded in contracts/README.md / contracts/slither.md"
    return {"foundry": foundry.group(1), "slither": slither.group(1)}


def version_problems(text: str, versions: dict[str, str]) -> list[str]:
    wf = parse_block_yaml(text)
    out = []
    steps = [s for job in wf["jobs"].values() for s in job.get("steps") or []]
    foundry = [s for s in steps if str(s.get("uses", "")).startswith("foundry-rs/foundry-toolchain@")]
    if not foundry or any((s.get("with") or {}).get("version") != "v" + versions["foundry"] for s in foundry):
        out.append(f"foundry-toolchain must install v{versions['foundry']} (contracts/README.md)")
    runs = "\n".join(str(s.get("run", "")) for s in steps)
    if f"slither-analyzer=={versions['slither']}" not in runs:
        out.append(f"Slither must be installed as slither-analyzer=={versions['slither']} (contracts/slither.md)")
    if re.search(r"playwright@|npx playwright install", runs):
        out.append("Playwright is installed by make e2e-setup (pinned in mk/lane-c.mk), not by the workflow")
    pythons = [(s.get("with") or {}).get("python-version") for s in steps if "python-version" in (s.get("with") or {})]
    if not pythons or any(str(p) != "3.12" for p in pythons):
        out.append("Python must be 3.12 (make setup's uv venv --python 3.12)")
    return out


# --- tests --------------------------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def text() -> str:
    assert WORKFLOW.is_file(), f"missing {WORKFLOW.relative_to(ROOT)}"
    return WORKFLOW.read_text()


def test_workflow_passes_every_check(text):
    assert problems(text, make_targets()) == []
    assert version_problems(text, recorded_versions()) == []


def test_three_independent_jobs_so_a_failure_names_its_part(text):
    jobs = parse_block_yaml(text)["jobs"]
    assert set(jobs) == {"check", "e2e", "slither"}
    assert all("needs" not in j for j in jobs.values())
    assert all(isinstance(j.get("timeout-minutes"), int) for j in jobs.values())


def test_subset_reader_agrees_with_a_real_yaml_parser(text):
    real = real_yaml(text)
    if real is None:
        pytest.skip("no PyYAML or ruby on this machine to cross-check the subset reader")
    for key in (True, "true"):  # YAML 1.1 reads the bare key `on` as a boolean (JSON from Ruby: "true");
        if key in real:         # GitHub reads it as the string "on"
            real["on"] = real.pop(key)
    assert parse_block_yaml(text) == real


@pytest.mark.parametrize("old,new,expect", [
    ("contents: read", "contents: write", "permissions"),
    ("  pull_request:\n    branches:\n      - main\n", "  pull_request:\n    branches:\n      - dev\n", "pull_request"),
    ("actions/checkout@", "actions/checkout@v7 #", "not pinned"),
    ("run: make e2e-setup", "run: make e2e-install", "does not exist"),
    ("run: make slither", "run: true", "never run"),
    ("    runs-on: ubuntu-latest\n", "    runs-on: ubuntu-latest\n    env:\n      TOKEN: ${{ secrets.X }}\n", "secrets"),
    ("persist-credentials: false", "persist-credentials: true", "persist-credentials"),
    ("          persist-credentials: false\n", "", "persist-credentials"),
    ("run: " + SLITHER_DIFF, "run: true", "slither.md"),
    ("GITLEAKS_VERSION: 8.", "GITLEAKS_VERSION: latest-8.", "version must be pinned"),
    ("GITLEAKS_SHA256: ", "GITLEAKS_SHA256: x", "checksum must be pinned"),
    ("sha256sum -c", "cat", "not verified"),
    ("releases/download/", "releases/latest/", "not installed"),
    ("fetch-depth: 0", "fetch-depth: 1", "fetch-depth"),
])
def test_each_check_rejects_a_broken_copy(text, old, new, expect):
    assert old in text
    broken = text.replace(old, new, 1)
    try:
        found = problems(broken, make_targets())
    except YamlSubsetError as e:
        found = [f"unsupported YAML: {e}"]
    assert any(expect in p for p in found), found


def test_check_job_installs_pinned_gitleaks_before_make_check(text):
    job = parse_block_yaml(text)["jobs"]["check"]
    assert gitleaks_problems(job) == []
    env = next(s["env"] for s in job["steps"] if "GITLEAKS_VERSION" in (s.get("env") or {}))
    assert re.fullmatch(r"\d+\.\d+\.\d+", env["GITLEAKS_VERSION"]) and len(env["GITLEAKS_SHA256"]) == 64
    reordered = dict(job, steps=list(reversed(job["steps"])))  # install after make check: rejected
    assert any("before make check" in p for p in gitleaks_problems(reordered))
    without = dict(job, steps=[s for s in job["steps"] if "gitleaks" not in str(s.get("run", ""))])
    assert any("not installed" in p for p in gitleaks_problems(without))


def test_flow_syntax_is_refused_not_guessed():
    with pytest.raises(YamlSubsetError):
        parse_block_yaml("on:\n  push:\n    branches: [main]\n")


def test_version_checks_reject_a_drifted_pin(text):
    versions = recorded_versions()
    drifted = text.replace(f"version: v{versions['foundry']}", "version: stable")
    assert any("foundry-toolchain" in p for p in version_problems(drifted, versions))
    drifted = text.replace(f"slither-analyzer=={versions['slither']}", "slither-analyzer")
    assert any("Slither" in p for p in version_problems(drifted, versions))
    drifted = text.replace('python-version: "3.12"', 'python-version: "3.13"')
    assert any("3.12" in p for p in version_problems(drifted, versions))


def test_make_calls_reads_env_prefixes_and_chains():
    assert make_calls("ANVIL_PORT=8631 make check") == ["check"]
    assert make_calls("make e2e-setup && make e2e") == ["e2e-setup", "e2e"]
    assert make_calls("uv tool install x==1\nmake -s slither STRICT=1") == ["slither"]
