"""Viewer for genspec JSONL files and bundle zips."""

from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass
from pathlib import Path

from waypoints.genspec.importer import import_from_lines
from waypoints.genspec.spec import BundleChecksums, BundleMetadata, GenerativeSpec


@dataclass(frozen=True)
class ViewOptions:
    """Options for rendering a genspec view."""

    show_steps: bool = True
    steps_limit: int = 50
    show_preview: bool = True
    preview_lines: int = 8


def load_genspec(
    path: Path,
) -> tuple[GenerativeSpec, BundleMetadata | None, BundleChecksums | None]:
    """Load a genspec from JSONL file or bundle zip."""
    if not path.exists():
        raise FileNotFoundError(f"Genspec not found: {path}")

    if zipfile.is_zipfile(path):
        return _load_from_bundle(path)

    with open(path, encoding="utf-8") as handle:
        spec = import_from_lines(handle, source=str(path))
    return spec, None, None


def render_view(
    spec: GenerativeSpec,
    metadata: BundleMetadata | None,
    checksums: BundleChecksums | None,
    options: ViewOptions,
) -> str:
    """Render a genspec view for terminal output."""
    lines: list[str] = []
    lines.append("GenSpec View")
    lines.append(f"Source: {'bundle' if metadata else 'jsonl'}")
    lines.append(f"Project: {spec.source_project}")
    lines.append(f"Created: {spec.created_at.isoformat()}")
    lines.append(f"Waypoints Version: {spec.waypoints_version}")
    if spec.model:
        lines.append(f"Model: {spec.model}")
    if spec.model_version:
        lines.append(f"Model Version: {spec.model_version}")
    lines.append(
        "Steps: "
        f"{len(spec.steps)}  Decisions: {len(spec.decisions)}  "
        f"Artifacts: {len(spec.artifacts)}"
    )
    if metadata:
        lines.append(f"Bundle: {metadata.schema} v{metadata.version}")
    if checksums:
        lines.append(
            f"Checksums: {checksums.algorithm} ({len(checksums.files)} files)"
        )

    if options.show_steps:
        lines.append("")
        lines.append("Steps")
        lines.extend(_render_steps(spec, options.steps_limit))

    lines.append("")
    lines.append("Artifacts")
    lines.extend(_render_artifacts(spec, options))

    return "\n".join(lines)


def _load_from_bundle(
    path: Path,
) -> tuple[GenerativeSpec, BundleMetadata | None, BundleChecksums | None]:
    metadata: BundleMetadata | None = None
    checksums: BundleChecksums | None = None

    with zipfile.ZipFile(path) as archive:
        metadata = _read_bundle_metadata(archive)
        checksums = _read_bundle_checksums(archive)
        genspec_path = metadata.genspec_path if metadata else "genspec.jsonl"
        try:
            content = archive.read(genspec_path).decode("utf-8")
        except KeyError as exc:
            raise ValueError(f"Missing genspec in bundle: {genspec_path}") from exc

    spec = import_from_lines(content.splitlines(), source=f"{path}::{genspec_path}")
    return spec, metadata, checksums


def _read_bundle_metadata(archive: zipfile.ZipFile) -> BundleMetadata | None:
    try:
        payload = json.loads(archive.read("metadata.json").decode("utf-8"))
    except KeyError:
        return None
    return BundleMetadata.from_dict(payload)


def _read_bundle_checksums(archive: zipfile.ZipFile) -> BundleChecksums | None:
    try:
        payload = json.loads(archive.read("checksums.json").decode("utf-8"))
    except KeyError:
        return None
    return BundleChecksums.from_dict(payload)


def _render_steps(spec: GenerativeSpec, limit: int) -> list[str]:
    if not spec.steps:
        return ["  (none)"]

    steps = spec.steps if limit <= 0 else spec.steps[:limit]
    rendered: list[str] = []
    for step in steps:
        cost = (
            f"${step.metadata.cost_usd:.2f}"
            if step.metadata.cost_usd is not None
            else "-"
        )
        rendered.append(
            f"  {step.step_id}  {step.phase.value}  "
            f"{step.timestamp.isoformat()}  {cost}"
        )

    if limit > 0 and len(spec.steps) > limit:
        rendered.append(
            f"  ... ({len(spec.steps) - limit} more; use --steps-limit=0 for all)"
        )
    return rendered


def _render_artifacts(spec: GenerativeSpec, options: ViewOptions) -> list[str]:
    if not spec.artifacts:
        return ["  (none)"]

    rendered: list[str] = []
    for artifact in spec.artifacts:
        name = artifact.artifact_type.value
        size = len(artifact.content)
        path = artifact.file_path or "-"
        rendered.append(f"  {name}  {size} chars  {path}")

        if options.show_preview and options.preview_lines > 0:
            preview_lines = artifact.content.splitlines()[: options.preview_lines]
            for line in preview_lines:
                rendered.append(f"    {line[:200]}")
            if len(artifact.content.splitlines()) > options.preview_lines:
                rendered.append("    ...")

    return rendered
