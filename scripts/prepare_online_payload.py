"""Map a reviewed frozen tree to pinned wheels/model files, without installing them.

The initial manifest is a download plan. --verify-cache must pass before the
installer is compiled: it checks whole archives and every selected member.
Unprovable origins and installer-generated metadata stay in the embedded core.
"""

from __future__ import annotations

import argparse
import ast
import base64
import csv
import hashlib
import importlib.metadata
import io
import json
import os
import re
import stat
import sys
import tomllib
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote, urlsplit

from packaging.utils import InvalidWheelFilename, canonicalize_name, parse_wheel_filename


class PayloadError(ValueError):
    """A build input cannot safely reproduce the reviewed distribution."""


def _safe_path(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise PayloadError("Unsafe relative path")
    normalized = value.replace("\\", "/")
    parts = normalized.split("/")
    if any(
        not part
        or part in (".", "..")
        or part[-1:] in (".", " ")
        or re.search(r'[<>:"|?*{}\x00-\x1f]', part)
        or re.fullmatch(r"(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", part)
        for part in parts
    ):
        raise PayloadError("Unsafe relative path")
    return "/".join(parts)


def _size(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise PayloadError("Invalid file size")
    return value


def _sha(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", value):
        raise PayloadError("Invalid SHA256 digest")
    return value.lower()


def _digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _check_regular(path: Path) -> None:
    info = path.lstat()
    if (
        path.is_symlink()
        or getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        or not stat.S_ISREG(info.st_mode)
    ):
        raise PayloadError("Input path is not a regular file")


def _url(value: Any, host: str) -> str:
    if not isinstance(value, str):
        raise PayloadError("Invalid artifact URL")
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or parsed.netloc != host
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or re.search(r"[\s\x00-\x1f]", value)
        or not parsed.path.startswith("/")
    ):
        raise PayloadError("Unapproved artifact URL")
    return value


def _json(path: Path) -> dict[str, Any]:
    _check_regular(path)
    result = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(result, dict) or result.get("schema_version") != 1:
        raise PayloadError("Unsupported manifest schema")
    return result


def _inventory(dist: Path, inventory: Path) -> list[dict[str, Any]]:
    document = _json(inventory)
    if document.get("application") != "InterviewAssistant" or not document.get("files"):
        raise PayloadError("Invalid inventory")
    expected: dict[str, dict[str, Any]] = {}
    for entry in document["files"]:
        name = _safe_path(entry["path"])
        if name.casefold() in expected:
            raise PayloadError("Duplicate inventory path")
        expected[name.casefold()] = {
            "path": name,
            "size": _size(entry["size"]),
            "sha256": _sha(entry["sha256"]),
        }
    observed: dict[str, Path] = {}
    for directory, child_dirs, child_files in os.walk(dist, followlinks=False):
        for name in child_dirs:
            child = Path(directory) / name
            info = child.lstat()
            if child.is_symlink() or getattr(info, "st_file_attributes", 0) & getattr(
                stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0
            ):
                raise PayloadError("Reparse directory in inventory")
        for name in child_files:
            child = Path(directory) / name
            relative = _safe_path(child.relative_to(dist).as_posix())
            _check_regular(child)
            if relative.casefold() in observed:
                raise PayloadError("Duplicate inventory file")
            observed[relative.casefold()] = child
    if set(observed) != set(expected):
        raise PayloadError("Frozen files differ from inventory paths")
    for key, entry in expected.items():
        path = observed[key]
        if path.stat().st_size != entry["size"] or _digest(path) != entry["sha256"]:
            raise PayloadError("Frozen file differs from inventory hash or size")
    return sorted(expected.values(), key=lambda entry: entry["path"].casefold())


def _origins(collect: Path) -> dict[str, Path]:
    _check_regular(collect)
    document = ast.literal_eval(collect.read_text(encoding="utf-8"))
    if not isinstance(document, tuple) or len(document) != 1 or not isinstance(document[0], list):
        raise PayloadError("Invalid COLLECT manifest")
    origins = {}
    for row in document[0]:
        if not isinstance(row, tuple) or len(row) != 3:
            raise PayloadError("Invalid COLLECT row")
        name, source, kind = row
        relative = _safe_path(name)
        if kind != "EXECUTABLE":
            relative = "_internal/" + relative
        if relative.casefold() in origins:
            raise PayloadError("Duplicate COLLECT path")
        origins[relative.casefold()] = Path(source).resolve()
    return origins


def _record_sha(value: str) -> str | None:
    if not value.startswith("sha256="):
        return None
    encoded = value.partition("=")[2]
    # qasync 0.28.0's installed RECORD uses hex instead of base64url. Do not
    # interpret that as a different hash; final archive-member checks still run.
    if re.fullmatch(r"[0-9a-fA-F]{64}", encoded):
        return encoded.lower()
    try:
        raw = base64.b64decode(encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True)
    except ValueError:
        return None
    return raw.hex() if len(raw) == 32 else None


def _source_key(path: Path) -> str:
    return os.path.normcase(os.path.abspath(path))


def _wheel_candidates(
    site_packages: Path, lock: Path, origins: dict[str, Path]
) -> dict[str, list[dict[str, Any]]]:
    packages = tomllib.loads(lock.read_text(encoding="utf-8")).get("package", [])
    locked = {(p["name"], p["version"]): p for p in packages}
    wanted = {_source_key(source) for source in origins.values()}
    matches: dict[str, list[dict[str, Any]]] = {}
    for dist in importlib.metadata.distributions(path=[str(site_packages)]):
        package = locked.get((canonicalize_name(dist.metadata["Name"]), dist.version))
        if package is None:
            continue
        relevant = []
        for row in csv.reader(io.StringIO(dist.read_text("RECORD") or "")):
            if len(row) != 3:
                raise PayloadError("Invalid installed RECORD")
            relative, digest, size = row
            if PurePosixPath(relative.replace("\\", "/")).name in {
                "RECORD",
                "INSTALLER",
                "REQUESTED",
                "direct_url.json",
            } and ".dist-info/" in relative.replace("\\", "/"):
                continue
            source = _source_key(Path(str(dist.locate_file(relative))))
            if source not in wanted:
                continue
            member = _safe_path(relative)
            sha = _record_sha(digest)
            if sha is not None and size.isdecimal():
                relevant.append((source, member, sha, int(size)))
        if not relevant:
            continue
        tags = {
            line[5:]
            for line in (dist.read_text("WHEEL") or "").splitlines()
            if line.startswith("Tag: ")
        }
        wheels = []
        for wheel in package.get("wheels", []):
            # Validate before parsing so errors never expose credentials in URLs.
            url = _url(wheel["url"], "files.pythonhosted.org")
            filename = unquote(urlsplit(url).path.rsplit("/", 1)[-1])
            try:
                name, version, _, wheel_tags = parse_wheel_filename(filename)
            except InvalidWheelFilename as error:
                raise PayloadError("Invalid pinned wheel filename") from error
            if name != canonicalize_name(dist.metadata["Name"]) or str(version) != dist.version:
                raise PayloadError("Pinned wheel identity mismatch")
            if {str(tag) for tag in wheel_tags} == tags:
                if package.get("source") != {"registry": "https://pypi.org/simple"}:
                    raise PayloadError("Unapproved wheel registry URL")
                if not wheel["hash"].startswith("sha256=") and not wheel["hash"].startswith(
                    "sha256:"
                ):
                    raise PayloadError("Unpinned wheel hash")
                wheels.append(
                    {
                        "url": url,
                        "sha256": _sha(wheel["hash"][7:]),
                        "size": _size(wheel["size"]),
                        "display_name": filename,
                    }
                )
        if len(wheels) != 1:
            continue
        for source, member, sha, file_size in relevant:
            matches.setdefault(source, []).append(
                {"member": member, "sha256": sha, "size": file_size, "wheel": wheels[0]}
            )
    return matches


def _models(manifest: Path, inventory: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    document = _json(manifest)
    repository = document.get("repository", "")
    revision = document.get("revision", "")
    if not re.fullmatch(r"[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+", repository):
        raise PayloadError("Invalid model repository")
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise PayloadError("Model revision must be a pinned commit")
    bundle = "_internal/" + _safe_path(document["bundle_subdirectory"])
    entries = {}
    for entry in document["files"]:
        member = _safe_path(entry["path"])
        name = f"{bundle}/{member}"
        if name.casefold() in entries:
            raise PayloadError("Duplicate model path")
        entries[name.casefold()] = {
            "url": _url(
                f"https://huggingface.co/{repository}/resolve/{revision}/{member}", "huggingface.co"
            ),
            "sha256": _sha(entry["sha256"]),
            "size": _size(entry["size"]),
            "display_name": f"STT model: {member}",
        }
    found = {
        entry["path"].casefold(): entry
        for entry in inventory
        if entry["path"].casefold().startswith(bundle.casefold() + "/")
    }
    if not entries or set(found) != set(entries):
        raise PayloadError("Model inventory does not match pinned manifest")
    for name, artifact in entries.items():
        if any(found[name][key] != artifact[key] for key in ("size", "sha256")):
            raise PayloadError("Model inventory hash mismatch")
    return entries


def verify_cached_artifacts(payload: dict[str, Any], cache: Path) -> None:
    """Verify every pinned download and every selected member; never extract."""
    for artifact in payload["artifacts"]:
        path = cache / artifact["cache_name"]
        if not path.is_file():
            raise PayloadError("Required artifact missing from cache")
        _check_regular(path)
        if path.stat().st_size != artifact["size"] or _digest(path) != artifact["sha256"]:
            raise PayloadError("Artifact cache hash or size mismatch")
        if artifact["kind"] == "file":
            continue
        try:
            with zipfile.ZipFile(path) as archive:
                members = {}
                artifact["expanded_size"] = sum(
                    info.file_size for info in archive.infolist() if not info.is_dir()
                )
                for info in archive.infolist():
                    normalized = _safe_path(info.filename.rstrip("/"))
                    if normalized.casefold() in members:
                        raise PayloadError("Duplicate archive member path")
                    if stat.S_ISLNK(info.external_attr >> 16) or info.flag_bits & 1:
                        raise PayloadError("Unsupported archive member type")
                    members[normalized.casefold()] = info
                for entry in payload["files"]:
                    if entry["artifact"] != artifact["id"]:
                        continue
                    selected_info = members.get(entry["member"].casefold())
                    # Windows accepts either separator but the manifest records
                    # the archive's actual member spelling for review/identity.
                    if (
                        selected_info is None
                        or selected_info.is_dir()
                        or selected_info.file_size != entry["size"]
                    ):
                        raise PayloadError("Selected archive member missing or size mismatch")
                    hasher = hashlib.sha256()
                    with archive.open(selected_info) as stream:
                        while chunk := stream.read(1024 * 1024):
                            hasher.update(chunk)
                    digest = hasher.hexdigest()
                    if digest != entry["sha256"]:
                        raise PayloadError("Selected archive member hash mismatch")
                    entry["member"] = selected_info.filename
        except (zipfile.BadZipFile, RuntimeError, OSError) as error:
            raise PayloadError("Invalid cached wheel archive") from error


def prepare_payload(
    *,
    dist: Path,
    inventory: Path,
    collect: Path,
    lock: Path,
    site_packages: Path,
    model_manifest: Path,
    verify_cache: Path | None = None,
) -> dict[str, Any]:
    files = _inventory(dist, inventory)
    origins = _origins(collect)
    candidates = _wheel_candidates(site_packages.resolve(), lock, origins)
    models = _models(model_manifest, files)
    selected: dict[str, dict[str, Any]] = {}
    source_digests: dict[Path, str] = {}
    for entry in files:
        entry.update(artifact=None, member=None)
        model = models.get(entry["path"].casefold())
        if model:
            key = "model:" + model["sha256"]
            selected[key] = {**model, "kind": "file"}
            entry["artifact"] = key
            continue
        source = origins.get(entry["path"].casefold())
        if source is None or not source.is_file():
            continue
        matching = [
            candidate
            for candidate in candidates.get(_source_key(source), [])
            if candidate["size"] == entry["size"] and candidate["sha256"] == entry["sha256"]
        ]
        if len(matching) != 1:
            continue
        if source not in source_digests:
            _check_regular(source)
            source_digests[source] = _digest(source)
        if source_digests[source] != entry["sha256"]:
            continue
        match = matching[0]
        key = "wheel:" + match["wheel"]["sha256"]
        selected[key] = {**match["wheel"], "kind": "wheel"}
        entry.update(artifact=key, member=match["member"])
    artifacts = []
    ids = {}
    counters = {"wheel": 0, "file": 0}
    for key, artifact in sorted(
        selected.items(), key=lambda pair: (pair[1]["kind"] != "wheel", pair[1]["display_name"])
    ):
        kind = artifact["kind"]
        artifact_id = f"{'wheel' if kind == 'wheel' else 'model'}-{counters[kind]}"
        counters[kind] += 1
        ids[key] = artifact_id
        artifacts.append(
            {
                "id": artifact_id,
                **artifact,
                "expanded_size": artifact["size"] if kind == "file" else 0,
                "cache_name": artifact["sha256"] + (".zip" if kind == "wheel" else ".bin"),
            }
        )
    for entry in files:
        if entry["artifact"] is not None:
            entry["artifact"] = ids[entry["artifact"]]
    payload = {
        "schema_version": 1,
        "application": "InterviewAssistant",
        "inventory_sha256": _digest(inventory),
        "lock_sha256": _digest(lock),
        "collect_sha256": _digest(collect),
        "model_manifest_sha256": _digest(model_manifest),
        "wheel_archives_verified": False,
        "artifacts_verified": False,
        "artifacts": artifacts,
        "files": files,
    }
    if verify_cache is not None:
        verify_cached_artifacts(payload, verify_cache)
        payload["wheel_archives_verified"] = True
        payload["artifacts_verified"] = True
    payload["summary"] = {
        "total_files": len(files),
        "total_bytes": sum(e["size"] for e in files),
        "embedded_files": sum(e["artifact"] is None for e in files),
        "embedded_bytes": sum(e["size"] for e in files if e["artifact"] is None),
        "external_files": sum(e["artifact"] is not None for e in files),
        "external_bytes": sum(e["size"] for e in files if e["artifact"] is not None),
        "download_bytes": sum(a["size"] for a in artifacts),
    }
    return payload


def _pascal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _inno(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def write_outputs(payload: dict[str, Any], output: Path) -> None:
    """Write a complete manifest and corresponding Inno includes atomically per file."""
    serialized = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    comment = "payload_manifest_sha256=" + digest
    files_lines = ["; " + comment]
    data_lines = [
        "// " + comment,
        "procedure LoadOnlinePayload;",
        "begin",
        f"  SetArrayLength(Artifacts, {len(payload['artifacts'])});",
    ]
    indexes = {}
    for index, artifact in enumerate(payload["artifacts"]):
        indexes[artifact["id"]] = index
        for key, field in (
            ("id", "Id"),
            ("kind", "Kind"),
            ("url", "Url"),
            ("sha256", "SHA256"),
            ("display_name", "DisplayName"),
            ("cache_name", "CacheName"),
        ):
            data_lines.append(f"  Artifacts[{index}].{field} := {_pascal(artifact[key])};")
        data_lines.append(f"  Artifacts[{index}].Size := {artifact['size']};")
        data_lines.append(f"  Artifacts[{index}].ExpandedSize := {artifact['expanded_size']};")
    external = [entry for entry in payload["files"] if entry["artifact"] is not None]
    data_lines.append(f"  SetArrayLength(PayloadFiles, {len(external)});")
    external_index = 0
    for entry in payload["files"]:
        relative = _safe_path(entry["path"]).replace("/", "\\")
        parent, _, name = relative.rpartition("\\")
        dest = "{app}" + ("\\" + parent if parent else "")
        if entry["artifact"] is None:
            source = "{#DistPath}\\" + relative
            flags = "ignoreversion"
            extra = ""
        else:
            source = f"{{code:GetPreparedFile|{external_index}}}"
            flags = "external ignoreversion"
            extra = f"; ExternalSize: {entry['size']}"
            values = {
                "RelativePath": relative,
                "MemberPath": (entry["member"] or "").replace("/", "\\"),
                "SHA256": entry["sha256"],
            }
            for field, value in values.items():
                data_lines.append(f"  PayloadFiles[{external_index}].{field} := {_pascal(value)};")
            data_lines.append(
                f"  PayloadFiles[{external_index}].ArtifactIndex := {indexes[entry['artifact']]};"
            )
            data_lines.append(f"  PayloadFiles[{external_index}].Size := {entry['size']};")
            external_index += 1
        files_lines.append(
            f"Source: {_inno(source)}; DestDir: {_inno(dest)}; DestName: {_inno(name)}; Hash: {_inno(entry['sha256'])}{extra}; Flags: {flags}"
        )
    data_lines.append("end;")
    output.mkdir(parents=True, exist_ok=True)
    for name, text in (
        ("online_payload.json", serialized),
        ("payload_files.iss", "\n".join(files_lines) + "\n"),
        ("payload_data.iss", "\n".join(data_lines) + "\n"),
    ):
        target = output / name
        temporary = output / (name + ".tmp")
        temporary.write_text(text, encoding="utf-8", newline="\n")
        temporary.replace(target)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in (
        "dist",
        "inventory",
        "collect",
        "lock",
        "site-packages",
        "model-manifest",
        "output",
    ):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--verify-cache", type=Path)
    args = vars(parser.parse_args(argv))
    output = args.pop("output")
    try:
        payload = prepare_payload(**args)
        write_outputs(payload, output)
    except (PayloadError, OSError, ValueError, KeyError, TypeError) as error:
        message = (
            str(error) if isinstance(error, PayloadError) else "Invalid or unreadable build input"
        )
        print("Payload preparation failed: " + message, file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "status": "verified" if payload["artifacts_verified"] else "pending_verification",
                **payload["summary"],
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
