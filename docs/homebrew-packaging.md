# Homebrew Packaging

This project ships a Homebrew formula at `Formula/waypoints.rb`.

## User Install

```bash
brew tap kulesh/tap
brew install waypoints
```

Direct install without tapping:

```bash
brew install kulesh/tap/waypoints
```

## Maintainer Workflow

### 1. Prepare a release

1. Update version fields:
   - `pyproject.toml` (`[project].version`)
   - `src/waypoints/__init__.py` (`__version__`)
2. Commit and push to `main`.
3. Create and push a tag:

```bash
git tag vX.Y.Z
git push origin vX.Y.Z
```

### 2. Regenerate formula with release tarball SHA256

Run:

```bash
./scripts/update_homebrew_formula.sh
```

This script:
- reads the project version from `pyproject.toml`
- downloads `https://github.com/kulesh/waypoints/archive/refs/tags/vX.Y.Z.tar.gz`
- computes `sha256`
- rewrites `Formula/waypoints.rb`

### 3. Publish formula to tap

The canonical tap is `kulesh/homebrew-tap`.

1. Copy `Formula/waypoints.rb` into `homebrew-tap/Formula/waypoints.rb`.
2. Commit and push in `homebrew-tap`.

After push, `brew upgrade waypoints` will pick up the new version.

## Local Validation

```bash
cp Formula/waypoints.rb "$(brew --repository kulesh/tap)/Formula/waypoints.rb"
brew audit --strict waypoints
brew install waypoints
waypoints --help
brew uninstall waypoints
```
