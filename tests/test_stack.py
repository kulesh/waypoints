"""Tests for technology stack detection."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from waypoints.fly.receipt_finalizer import ReceiptFinalizer
from waypoints.fly.stack import (
    STACK_COMMANDS,
    StackConfig,
    StackType,
    ValidationCommand,
    build_validation_section,
    detect_stack,
    detect_stack_from_spec,
)
from waypoints.models.waypoint import Waypoint


class TestStackType:
    """Tests for StackType enum."""

    def test_stack_type_values(self) -> None:
        """Verify all expected stack types exist."""
        assert StackType.PYTHON.value == "python"
        assert StackType.TYPESCRIPT.value == "typescript"
        assert StackType.JAVASCRIPT.value == "javascript"
        assert StackType.GO.value == "go"
        assert StackType.RUST.value == "rust"
        assert StackType.SWIFT.value == "swift"


class TestValidationCommand:
    """Tests for ValidationCommand dataclass."""

    def test_validation_command_creation(self) -> None:
        """Test creating a validation command."""
        cmd = ValidationCommand("linting", "ruff check .", "lint")
        assert cmd.name == "linting"
        assert cmd.command == "ruff check ."
        assert cmd.category == "lint"
        assert cmd.optional is False

    def test_optional_command(self) -> None:
        """Test optional validation command."""
        cmd = ValidationCommand(
            "formatting", "prettier --check .", "format", optional=True
        )
        assert cmd.optional is True

    def test_default_cwd_is_none(self) -> None:
        """ValidationCommand.cwd defaults to None."""
        cmd = ValidationCommand("tests", "pytest", "test")
        assert cmd.cwd is None

    def test_cwd_set_explicitly(self) -> None:
        """ValidationCommand.cwd can be set to a path."""
        p = Path("/some/path")
        cmd = ValidationCommand("tests", "cargo test", "test", cwd=p)
        assert cmd.cwd == p


class TestStackConfig:
    """Tests for StackConfig dataclass."""

    def test_default_root_path_is_none(self) -> None:
        """StackConfig.root_path defaults to None."""
        config = StackConfig(StackType.PYTHON)
        assert config.root_path is None
        assert config.commands == []

    def test_root_path_set_explicitly(self) -> None:
        """StackConfig.root_path can be set to a path."""
        p = Path("/project/backend")
        config = StackConfig(StackType.RUST, root_path=p)
        assert config.root_path == p


class TestStackCommands:
    """Tests for STACK_COMMANDS mapping."""

    def test_python_commands(self) -> None:
        """Verify Python stack has expected commands."""
        cmds = STACK_COMMANDS[StackType.PYTHON]
        categories = {c.category for c in cmds}
        assert "lint" in categories
        assert "test" in categories
        assert "type" in categories

    def test_typescript_commands(self) -> None:
        """Verify TypeScript stack has expected commands."""
        cmds = STACK_COMMANDS[StackType.TYPESCRIPT]
        categories = {c.category for c in cmds}
        assert "lint" in categories
        assert "test" in categories
        assert "type" in categories

    def test_go_commands(self) -> None:
        """Verify Go stack has expected commands."""
        cmds = STACK_COMMANDS[StackType.GO]
        categories = {c.category for c in cmds}
        assert "test" in categories
        assert "lint" in categories

    def test_rust_commands(self) -> None:
        """Verify Rust stack has expected commands."""
        cmds = STACK_COMMANDS[StackType.RUST]
        categories = {c.category for c in cmds}
        assert "test" in categories
        assert "lint" in categories

    def test_swift_commands(self) -> None:
        """Verify Swift stack has expected commands."""
        cmds = STACK_COMMANDS[StackType.SWIFT]
        categories = {c.category for c in cmds}
        assert "build" in categories
        assert "test" in categories


class TestDetectStack:
    """Tests for detect_stack function."""

    def test_detect_python_pyproject(self, tmp_path: Path) -> None:
        """Detect Python from pyproject.toml."""
        (tmp_path / "pyproject.toml").write_text("[project]\nname = 'test'")
        configs = detect_stack(tmp_path)
        assert len(configs) == 1
        assert configs[0].stack_type == StackType.PYTHON

    def test_detect_python_setup_py(self, tmp_path: Path) -> None:
        """Detect Python from setup.py."""
        (tmp_path / "setup.py").write_text("from setuptools import setup")
        configs = detect_stack(tmp_path)
        assert len(configs) == 1
        assert configs[0].stack_type == StackType.PYTHON

    def test_detect_python_requirements(self, tmp_path: Path) -> None:
        """Detect Python from requirements.txt."""
        (tmp_path / "requirements.txt").write_text("pytest>=7.0")
        configs = detect_stack(tmp_path)
        assert len(configs) == 1
        assert configs[0].stack_type == StackType.PYTHON

    def test_detect_typescript(self, tmp_path: Path) -> None:
        """Detect TypeScript from package.json + tsconfig.json."""
        (tmp_path / "package.json").write_text('{"name": "test"}')
        (tmp_path / "tsconfig.json").write_text('{"compilerOptions": {}}')
        configs = detect_stack(tmp_path)
        assert len(configs) == 1
        assert configs[0].stack_type == StackType.TYPESCRIPT

    def test_detect_javascript(self, tmp_path: Path) -> None:
        """Detect JavaScript from package.json only (no tsconfig)."""
        (tmp_path / "package.json").write_text('{"name": "test"}')
        configs = detect_stack(tmp_path)
        assert len(configs) == 1
        assert configs[0].stack_type == StackType.JAVASCRIPT

    def test_detect_go(self, tmp_path: Path) -> None:
        """Detect Go from go.mod."""
        (tmp_path / "go.mod").write_text("module example.com/test")
        configs = detect_stack(tmp_path)
        assert len(configs) == 1
        assert configs[0].stack_type == StackType.GO

    def test_detect_rust(self, tmp_path: Path) -> None:
        """Detect Rust from Cargo.toml."""
        (tmp_path / "Cargo.toml").write_text('[package]\nname = "test"')
        configs = detect_stack(tmp_path)
        assert len(configs) == 1
        assert configs[0].stack_type == StackType.RUST

    def test_detect_swift(self, tmp_path: Path) -> None:
        """Detect Swift from Package.swift."""
        (tmp_path / "Package.swift").write_text("// swift-tools-version:5.10")
        configs = detect_stack(tmp_path)
        assert len(configs) == 1
        assert configs[0].stack_type == StackType.SWIFT

    def test_detect_multiple_stacks(self, tmp_path: Path) -> None:
        """Detect multiple stacks in a monorepo."""
        (tmp_path / "pyproject.toml").write_text("[project]")
        (tmp_path / "package.json").write_text('{"name": "frontend"}')
        (tmp_path / "tsconfig.json").write_text("{}")
        configs = detect_stack(tmp_path)
        assert len(configs) == 2
        types = {c.stack_type for c in configs}
        assert StackType.PYTHON in types
        assert StackType.TYPESCRIPT in types

    def test_detect_empty_project(self, tmp_path: Path) -> None:
        """Empty project returns no stacks."""
        configs = detect_stack(tmp_path)
        assert configs == []

    def test_root_path_set_for_root_detection(self, tmp_path: Path) -> None:
        """Root detection populates root_path with the project path."""
        (tmp_path / "Cargo.toml").write_text('[package]\nname = "test"')
        configs = detect_stack(tmp_path)
        assert len(configs) == 1
        assert configs[0].root_path == tmp_path

    def test_detect_rust_in_subdirectory(self, tmp_path: Path) -> None:
        """Cargo.toml in a child directory detected with correct root_path."""
        subdir = tmp_path / "canopy"
        subdir.mkdir()
        (subdir / "Cargo.toml").write_text('[package]\nname = "canopy"')
        configs = detect_stack(tmp_path)
        assert len(configs) == 1
        assert configs[0].stack_type == StackType.RUST
        assert configs[0].root_path == subdir

    def test_detect_root_takes_precedence(self, tmp_path: Path) -> None:
        """Root manifest prevents subdirectory scan."""
        (tmp_path / "pyproject.toml").write_text("[project]")
        subdir = tmp_path / "backend"
        subdir.mkdir()
        (subdir / "Cargo.toml").write_text('[package]\nname = "backend"')
        configs = detect_stack(tmp_path)
        assert len(configs) == 1
        assert configs[0].stack_type == StackType.PYTHON
        assert configs[0].root_path == tmp_path

    def test_detect_multiple_stacks_in_subdirs(self, tmp_path: Path) -> None:
        """Monorepo with manifests only in subdirectories."""
        frontend = tmp_path / "frontend"
        frontend.mkdir()
        (frontend / "package.json").write_text('{"name": "frontend"}')
        (frontend / "tsconfig.json").write_text("{}")
        backend = tmp_path / "backend"
        backend.mkdir()
        (backend / "Cargo.toml").write_text('[package]\nname = "backend"')
        configs = detect_stack(tmp_path)
        assert len(configs) == 2
        types = {c.stack_type for c in configs}
        assert StackType.TYPESCRIPT in types
        assert StackType.RUST in types
        # Verify root_paths point to correct subdirectories
        by_type = {c.stack_type: c for c in configs}
        assert by_type[StackType.TYPESCRIPT].root_path == frontend
        assert by_type[StackType.RUST].root_path == backend

    def test_hidden_dirs_skipped(self, tmp_path: Path) -> None:
        """Hidden directories are not searched."""
        hidden = tmp_path / ".hidden"
        hidden.mkdir()
        (hidden / "Cargo.toml").write_text('[package]\nname = "hidden"')
        configs = detect_stack(tmp_path)
        assert configs == []


class TestDetectStackFromSpec:
    """Tests for detect_stack_from_spec function."""

    def test_detect_python_keywords(self) -> None:
        """Detect Python from spec keywords."""
        spec = "We'll use Python with FastAPI for the backend"
        stacks = detect_stack_from_spec(spec)
        assert StackType.PYTHON in stacks

    def test_detect_typescript_keywords(self) -> None:
        """Detect TypeScript from spec keywords."""
        spec = "Frontend will be built with React and TypeScript"
        stacks = detect_stack_from_spec(spec)
        assert StackType.TYPESCRIPT in stacks

    def test_detect_go_keywords(self) -> None:
        """Detect Go from spec keywords."""
        spec = "The service is written in Golang for performance"
        stacks = detect_stack_from_spec(spec)
        assert StackType.GO in stacks

    def test_detect_rust_keywords(self) -> None:
        """Detect Rust from spec keywords."""
        spec = "Using Rust with Tokio for async runtime"
        stacks = detect_stack_from_spec(spec)
        assert StackType.RUST in stacks

    def test_detect_swift_keywords(self) -> None:
        """Detect Swift package workflows from spec keywords."""
        spec = "Use Swift Package Manager with Package.swift and swift test"
        stacks = detect_stack_from_spec(spec)
        assert StackType.SWIFT in stacks

    def test_detect_multiple_from_spec(self) -> None:
        """Detect multiple stacks from spec."""
        spec = "Python backend with React TypeScript frontend"
        stacks = detect_stack_from_spec(spec)
        assert StackType.PYTHON in stacks
        assert StackType.TYPESCRIPT in stacks

    def test_no_stack_detected(self) -> None:
        """No stacks detected from generic spec."""
        spec = "Build a web application that manages user data"
        stacks = detect_stack_from_spec(spec)
        assert stacks == []

    def test_case_insensitive(self) -> None:
        """Stack detection is case insensitive."""
        spec = "PYTHON backend with TYPESCRIPT frontend"
        stacks = detect_stack_from_spec(spec)
        assert StackType.PYTHON in stacks
        assert StackType.TYPESCRIPT in stacks


class TestBuildValidationSection:
    """Tests for build_validation_section function."""

    def test_no_stacks(self) -> None:
        """No stacks returns fallback message."""
        result = build_validation_section([])
        assert "Interpret checklist items" in result

    def test_python_stack(self) -> None:
        """Python stack generates appropriate commands."""
        config = StackConfig(StackType.PYTHON, list(STACK_COMMANDS[StackType.PYTHON]))
        result = build_validation_section([config])
        assert "Python" in result
        assert "ruff check" in result
        assert "pytest" in result
        assert "mypy" in result

    def test_with_overrides(self) -> None:
        """Validation overrides are respected."""
        config = StackConfig(StackType.PYTHON, list(STACK_COMMANDS[StackType.PYTHON]))
        overrides = {"lint": "uv run ruff check .", "test": "uv run pytest -v"}
        result = build_validation_section([config], overrides)
        assert "uv run ruff check" in result
        assert "uv run pytest" in result

    def test_multiple_stacks(self) -> None:
        """Multiple stacks are all included."""
        configs = [
            StackConfig(StackType.PYTHON, list(STACK_COMMANDS[StackType.PYTHON])),
            StackConfig(
                StackType.TYPESCRIPT, list(STACK_COMMANDS[StackType.TYPESCRIPT])
            ),
        ]
        result = build_validation_section(configs)
        assert "Python" in result
        assert "Typescript" in result
        assert "ruff" in result
        assert "npm" in result

    def test_optional_command_noted(self) -> None:
        """Optional commands are marked."""
        cmd = ValidationCommand("format", "prettier", "format", optional=True)
        config = StackConfig(StackType.JAVASCRIPT, [cmd])
        result = build_validation_section([config])
        assert "(optional)" in result


class TestRunValidationCwd:
    """Tests for cwd threading in run_validation_commands."""

    def _make_finalizer(self, tmp_path: Path) -> ReceiptFinalizer:
        project = SimpleNamespace(get_path=lambda: tmp_path)
        waypoint = Waypoint(
            id="WP-CWD",
            title="CWD test",
            objective="Verify cwd threading",
            acceptance_criteria=[],
        )
        log_writer = MagicMock()
        return ReceiptFinalizer(
            project=project,
            waypoint=waypoint,
            log_writer=log_writer,
        )

    def test_run_validation_uses_command_cwd(self, tmp_path: Path) -> None:
        """Command with cwd set runs subprocess from that directory."""
        subdir = tmp_path / "subproject"
        subdir.mkdir()

        cmd = ValidationCommand(
            name="tests",
            command="pwd",
            category="test",
            cwd=subdir,
        )

        finalizer = self._make_finalizer(tmp_path)
        evidence = finalizer.run_validation_commands(tmp_path, [cmd])

        assert "tests" in evidence
        assert evidence["tests"].exit_code == 0
        # pwd output should contain the subdir path, not project root
        assert str(subdir) in evidence["tests"].stdout.strip()

        finalizer._log_writer.log_finalize_tool_call.assert_called()
        tool_input = finalizer._log_writer.log_finalize_tool_call.call_args.args[1]
        assert "timeout_events" in tool_input
        assert isinstance(tool_input["timeout_events"], list)

    def test_run_validation_falls_back_to_project_path(self, tmp_path: Path) -> None:
        """Command without cwd uses project_path as working directory."""
        cmd = ValidationCommand(
            name="tests",
            command="pwd",
            category="test",
        )

        finalizer = self._make_finalizer(tmp_path)
        evidence = finalizer.run_validation_commands(tmp_path, [cmd])

        assert "tests" in evidence
        assert evidence["tests"].exit_code == 0
        assert str(tmp_path) in evidence["tests"].stdout.strip()
