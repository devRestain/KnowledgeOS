"""Build minimal isolated control roots from explicitly declared inputs."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import tomllib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from vaultops.paths import ResolvedPaths, resolve_paths

CONTROL_ROOT = Path(__file__).resolve().parents[3]
_FORBIDDEN_INPUT_ROOTS = frozenset({".git", "KnowledgeHub", "runtime"})


@dataclass(frozen=True)
class _SourceFileSnapshot:
    content: bytes
    mode: int
    atime_ns: int
    mtime_ns: int


@dataclass(frozen=True)
class _SourceDirectorySnapshot:
    relative: PurePosixPath
    mode: int
    atime_ns: int
    mtime_ns: int


@dataclass(frozen=True)
class _SourceTreeSnapshot:
    directories: tuple[_SourceDirectorySnapshot, ...]
    files: tuple[tuple[PurePosixPath, _SourceFileSnapshot], ...]


_SOURCE_FILE_CACHE: dict[Path, _SourceFileSnapshot] = {}
_SOURCE_TREE_CACHE: dict[Path, _SourceTreeSnapshot] = {}
DIAGNOSTIC_CONTROL_INPUTS = (
    "blueprint",
    "ops/actions",
    "ops/config",
    "ops/expected",
    "ops/policies",
    "ops/prompts",
    "ops/schemas",
    "ops/vaultops.toml",
)
APPLICATION_CONTROL_INPUTS = DIAGNOSTIC_CONTROL_INPUTS
PORTABLE_CONTROL_INPUTS = (
    "blueprint/blueprint.schema.json",
    "blueprint/blueprint.yaml",
    "ops/actions",
    "ops/policies",
    "ops/prompts",
    "ops/schemas",
    "ops/vaultops.toml",
)
PLUGIN_CONTROL_INPUTS = (
    "blueprint/blueprint.yaml",
    "ops/expected/Property_Dictionary.md",
    "ops/vaultops.toml",
)
C40_LOCAL_CONTROL_INPUTS = (
    "blueprint/blueprint.schema.json",
    "ops/actions",
    "ops/policies",
    "ops/prompts",
    "ops/schemas/answer.schema.json",
    "ops/schemas/proposal.schema.json",
    "ops/schemas/remote-authorization.schema.json",
    "ops/schemas/triage-result.schema.json",
)


def _validated_relative(relative: str) -> PurePosixPath:
    path = PurePosixPath(relative)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"test control input must be one safe relative path: {relative}")
    if path.parts[0] in _FORBIDDEN_INPUT_ROOTS:
        raise ValueError(f"test control input crosses a mutable boundary: {relative}")
    return path


def _copy_cached_source_file(
    source: str | os.PathLike[str],
    target: str | os.PathLike[str],
) -> str:
    source_path = Path(source)
    target_path = Path(target)
    snapshot = _source_file_snapshot(source_path)
    _materialize_file(snapshot, target_path)
    return str(target_path)


def _source_file_snapshot(source: Path) -> _SourceFileSnapshot:
    if source.is_symlink():
        raise ValueError(f"declared test input contains a symlink: {source.name}")
    snapshot = _SOURCE_FILE_CACHE.get(source)
    if snapshot is None:
        content = source.read_bytes()
        metadata = source.stat(follow_symlinks=False)
        snapshot = _SourceFileSnapshot(
            content=content,
            mode=stat.S_IMODE(metadata.st_mode),
            atime_ns=metadata.st_atime_ns,
            mtime_ns=metadata.st_mtime_ns,
        )
        _SOURCE_FILE_CACHE[source] = snapshot
    return snapshot


def _materialize_file(
    snapshot: _SourceFileSnapshot,
    target: Path,
    *,
    preserve_times: bool = False,
) -> None:
    if target.is_symlink():
        raise ValueError(f"temporary test target must not be a symlink: {target.name}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(snapshot.content)
    os.chmod(target, snapshot.mode, follow_symlinks=False)
    if preserve_times:
        os.utime(target, ns=(snapshot.atime_ns, snapshot.mtime_ns), follow_symlinks=False)


def _source_tree_snapshot(source: Path) -> _SourceTreeSnapshot:
    cached = _SOURCE_TREE_CACHE.get(source)
    if cached is not None:
        return cached

    directories: list[_SourceDirectorySnapshot] = []
    files: list[tuple[PurePosixPath, _SourceFileSnapshot]] = []
    for current_value, directory_names, file_names in os.walk(source, followlinks=False):
        current = Path(current_value)
        relative_directory = PurePosixPath(current.relative_to(source).as_posix())
        if relative_directory == PurePosixPath("."):
            relative_directory = PurePosixPath()
        metadata = current.stat(follow_symlinks=False)
        directories.append(
            _SourceDirectorySnapshot(
                relative=relative_directory,
                mode=stat.S_IMODE(metadata.st_mode),
                atime_ns=metadata.st_atime_ns,
                mtime_ns=metadata.st_mtime_ns,
            )
        )
        for name in directory_names:
            directory = current / name
            if directory.is_symlink():
                raise ValueError(f"declared test input contains a symlinked directory: {name}")
        for name in file_names:
            source_file = current / name
            if not source_file.is_file() or source_file.is_symlink():
                raise ValueError(f"declared test input is not a regular file: {name}")
            relative_file = PurePosixPath(source_file.relative_to(source).as_posix())
            files.append((relative_file, _source_file_snapshot(source_file)))

    snapshot = _SourceTreeSnapshot(tuple(directories), tuple(files))
    _SOURCE_TREE_CACHE[source] = snapshot
    return snapshot


def _copy_cached_tree(
    source: Path,
    target: Path,
    *,
    dirs_exist_ok: bool = False,
    preserve_times: bool = False,
) -> Path:
    if target.is_symlink():
        raise ValueError(f"temporary test tree must not be a symlink: {target.name}")
    if target.exists() and not dirs_exist_ok:
        raise FileExistsError(target)
    target.mkdir(parents=True, exist_ok=dirs_exist_ok)
    snapshot = _source_tree_snapshot(source)
    for directory in snapshot.directories:
        if not directory.relative.parts:
            continue
        destination = target.joinpath(*directory.relative.parts)
        destination.mkdir(parents=True, exist_ok=True)
    for relative_file, file_snapshot in snapshot.files:
        _materialize_file(
            file_snapshot,
            target.joinpath(*relative_file.parts),
            preserve_times=preserve_times,
        )
    for directory in reversed(snapshot.directories):
        destination = target.joinpath(*directory.relative.parts)
        os.chmod(destination, directory.mode, follow_symlinks=False)
        if preserve_times:
            os.utime(
                destination,
                ns=(directory.atime_ns, directory.mtime_ns),
                follow_symlinks=False,
            )
    return target


def make_control_root(
    tmp_path: Path,
    inputs: Iterable[str],
    *,
    vault_files: Mapping[str, bytes | str] | None = None,
    with_vault: bool = True,
    with_runtime: bool = False,
) -> Path:
    """Copy declared control inputs and synthesize disposable mutable roots."""

    root = tmp_path / "control"
    root.mkdir(parents=True)
    for relative_value in dict.fromkeys(inputs):
        relative = _validated_relative(relative_value)
        source = CONTROL_ROOT.joinpath(*relative.parts)
        target = root.joinpath(*relative.parts)
        if source.is_symlink() or not source.exists():
            raise ValueError(f"declared test control input is missing or unsafe: {relative_value}")
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            _copy_cached_tree(
                source,
                target,
                preserve_times=relative.parts[:3] == ("ops", "tests", "fixtures"),
            )
        else:
            _copy_cached_source_file(source, target)

    vault = tmp_path / "vault"
    vault.mkdir()
    if with_vault:
        for relative_value, payload in (vault_files or {}).items():
            relative = _validated_vault_relative(relative_value)
            target = vault.joinpath(*relative.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload.encode("utf-8") if isinstance(payload, str) else payload)
    elif vault_files:
        raise ValueError("vault_files require with_vault=True")
    core = tmp_path / "core"
    _copy_cached_tree(Path("/workspace/Core"), core)
    for relative in ("ops/config/core-adoption.json", "ops/policies/owner-control.json"):
        _copy_cached_source_file(CONTROL_ROOT / relative, root / relative)
    state = tmp_path / "state"
    runtime = tmp_path / "runtime"
    state.mkdir(mode=0o700)
    runtime.mkdir(mode=0o700)
    write_fixture_config(root, core=core, vault=vault, state=state, runtime=runtime)
    from vaultops.adapters.core import load_json
    from vaultops.adapters.owner_journal import OwnerJournal, utc_now

    journal = OwnerJournal(resolve_paths(root), load_json(root / "ops/config/core-adoption.json"))
    with journal.writer() as session:
        session.commit(utc_now())
    return root


def _validated_vault_relative(relative: str) -> PurePosixPath:
    path = PurePosixPath(relative)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"test Vault input must be one safe relative path: {relative}")
    return path


def populate_vault_from_fixture(root: Path, fixture_relative: str) -> None:
    """Copy one declared checked-in fixture tree into a disposable Vault."""

    relative = _validated_relative(fixture_relative)
    if relative.parts[:3] != ("ops", "tests", "fixtures"):
        raise ValueError(f"Vault fixture must live below ops/tests/fixtures: {fixture_relative}")
    source = CONTROL_ROOT.joinpath(*relative.parts)
    vault = fixture_path(root, "vault")
    if source.is_symlink() or not source.is_dir():
        raise ValueError(f"declared Vault fixture is missing or unsafe: {fixture_relative}")
    _copy_cached_tree(source, vault, dirs_exist_ok=True, preserve_times=True)


def make_portable_fixture_root(
    tmp_path: Path,
    *,
    extra_inputs: Iterable[str] = (),
    review_queues: bool = False,
) -> Path:
    """Build the shared C09-derived app fixture with only declared inputs."""

    inputs = tuple(
        item for item in (*PORTABLE_CONTROL_INPUTS, *extra_inputs) if item != "ops/vaultops.toml"
    )
    root = make_control_root(tmp_path, inputs)
    populate_vault_from_fixture(
        root, "ops/tests/fixtures/c09_portable_vault/guestbook-horror/input"
    )
    if review_queues:
        for relative in (
            "01_AI_Review/Pending",
            "01_AI_Review/Resolved",
            "01_AI_Review/Rejected",
        ):
            (fixture_path(root, "vault") / relative).mkdir(parents=True, exist_ok=True)
    return root


def write_fixture_config(root: Path, *, core: Path, vault: Path, state: Path, runtime: Path) -> None:
    config = root / "ops/vaultops.toml"
    config.parent.mkdir(parents=True, exist_ok=True)
    values = {"core": core, "control": root, "vault": vault, "state": state, "runtime": runtime}
    config.write_text('schema_version = 3\nproject_name = "KnowledgeOS"\n' + "".join(f"{kind}_root = {json.dumps(str(path), ensure_ascii=False)}\n" for kind, path in values.items()) + 'timezone = "Asia/Seoul"\n', encoding="utf-8")


def fixture_path(root: Path, kind: str) -> Path:
    """Read a disposable fixture locator, even while testing invalid bindings."""
    config = tomllib.loads((root / "ops/vaultops.toml").read_text())
    return Path(config[f"{kind}_root"])


def make_separate_portable_fixture_roots(
    tmp_path: Path, *, extra_inputs: Iterable[str] = (), review_queues: bool = False,
    schema_version: int = 3,
) -> ResolvedPaths:
    if schema_version != 3:
        raise ValueError("fixtures require config v3; legacy versions are rejection cases")
    root = make_portable_fixture_root(tmp_path, extra_inputs=extra_inputs, review_queues=review_queues)
    roots = {kind: fixture_path(root, kind) for kind in ("core", "vault", "state", "runtime")}
    for kind, old in list(roots.items()):
        destination = tmp_path / (kind + " Ω")
        if old.exists():
            old.rename(destination)
        elif kind in {"state", "runtime"}:
            destination.mkdir(mode=0o700)
        roots[kind] = destination
    destination = tmp_path / "control Ω"
    root.rename(destination)
    write_fixture_config(destination, **roots)
    return resolve_paths(destination)


def _git(root: Path, *arguments: str) -> None:
    completed = subprocess.run(
        ["git", "-c", "user.name=KnowledgeOS Test", "-c", "user.email=test@local.invalid", "-C", str(root), *arguments],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"test Git command failed: {' '.join(arguments)}: {completed.stderr}")


def _initialize_git_repository(root: Path) -> None:
    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "fixture")


def make_diagnostic_root(tmp_path: Path) -> Path:
    """Build a complete disposable doctor input without reading mutable roots."""

    from vaultops.runtime import RUNTIME_DIRECTORIES, STATE_DIRECTORIES

    inputs = tuple(item for item in DIAGNOSTIC_CONTROL_INPUTS if item != "ops/vaultops.toml")
    root = make_control_root(tmp_path, inputs)
    (root / ".gitignore").write_text("/KnowledgeHub/\n/runtime/\n", encoding="utf-8")
    runtime = fixture_path(root, "runtime")
    runtime.mkdir(mode=0o700, exist_ok=True)
    for relative in RUNTIME_DIRECTORIES:
        (runtime / relative).mkdir(mode=0o700)
    state = fixture_path(root, "state")
    state.mkdir(mode=0o700, exist_ok=True)
    for relative in STATE_DIRECTORIES:
        (state / relative).mkdir(mode=0o700)
    _initialize_git_repository(root)

    vault = fixture_path(root, "vault")
    (vault / ".fixture-anchor").write_text("temporary test repository\n", encoding="utf-8")
    _initialize_git_repository(vault)
    return root


def make_identity_root(tmp_path: Path) -> Path:
    """Build only the roots, Blueprint, and sentinel needed for identity probes."""

    from vaultops.vault_identity import canonical_json_bytes, remote_identity_sha256

    sentinel = {
        "schema_version": 1,
        "contract_id": "knowledgeos-vault-root-v1",
        "vault_uuid": "411602c1-5278-4a8b-8b96-9183fb6ef8c2",
        "canonical_vault_name": "KnowledgeHub",
        "remote_identity_sha256": remote_identity_sha256(
            "git@github.com:owner/knowledgehub.git"
        ),
        "expected_branch": "main",
    }
    root = make_control_root(
        tmp_path,
        ("blueprint/blueprint.yaml",),
        vault_files={".knowledgeos-root.json": canonical_json_bytes(sentinel) + b"\n"},
        with_runtime=True,
    )
    (root / ".gitignore").write_text(
        "/KnowledgeHub/\n/runtime/\n", encoding="utf-8"
    )
    (fixture_path(root, "runtime")).chmod(0o700)
    (fixture_path(root, "vault") / "99_System").mkdir()
    _initialize_git_repository(root)
    _initialize_git_repository(fixture_path(root, "vault"))
    return root


def bind_existing_control(root: Path) -> None:
    """Bind a manually synthesized control fixture to independent empty roots."""
    root.mkdir(parents=True, exist_ok=True)
    base = root.parent
    core = base / "core"
    if not core.exists():
        _copy_cached_tree(Path("/workspace/Core"), core)
    vault = base / "vault"
    state = base / "state"
    runtime = base / "runtime"
    vault.mkdir(exist_ok=True)
    state.mkdir(mode=0o700, exist_ok=True)
    runtime.mkdir(mode=0o700, exist_ok=True)
    for relative in ("ops/config/core-adoption.json", "ops/policies/owner-control.json"):
        _copy_cached_source_file(CONTROL_ROOT / relative, root / relative)
    write_fixture_config(root, core=core, vault=vault, state=state, runtime=runtime)
    from vaultops.adapters.core import load_json
    from vaultops.adapters.owner_journal import OwnerJournal, utc_now
    journal = OwnerJournal(resolve_paths(root), load_json(root / "ops/config/core-adoption.json"))
    if not journal.path.exists():
        with journal.writer() as session:
            session.commit(utc_now())


def fixture_artifact(root: Path, locator: str) -> Path:
    """Resolve an explicit test report locator by its declared ownership."""
    for kind in ("state", "runtime"):
        if locator.startswith(kind + "/"):
            return fixture_path(root, kind) / locator.split("/", 1)[1]
    return root / locator
