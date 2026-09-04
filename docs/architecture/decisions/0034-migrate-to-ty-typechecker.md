# ADR 0034: Migrate to TY Type Checker

## Status

**Proposed**

## Context

Phlo currently uses [basedpyright](https://github.com/DetachHead/basedpyright) for Python type checking,
configured in `pyproject.toml` under `[tool.basedpyright]`. The CI pipeline runs `basedpyright src/phlo`
on every PR.

Astral released [ty](https://docs.astral.sh/ty/), a new Python type checker written in Rust, into
public beta on December 16, 2025. Key advantages:

- **10x-100x faster** than mypy and Pyright (2.19s vs 45.66s for home-assistant)
- **Incremental analysis** with fine-grained updates (up to 80x faster for editor changes)
- **Rich contextual diagnostics** inspired by Rust's compiler error messages
- **First-class Language Server** with Go-to-Definition, Symbol Rename, Auto-Complete, inlay hints
- **Gradual adoption support** with redeclarations and partially-typed code handling
- **Unified Astral ecosystem** alongside Ruff and uv

Since we already use Ruff for linting and uv for package management, adopting ty completes our
Astral-based Python toolchain. `ty` is already in our dev dependencies (`pyproject.toml` line 120).

### Current Type Checking Configuration

```toml
[tool.basedpyright]
include = ["src/phlo"]
exclude = ["src/phlo/testing"]
stubPath = "typings"
pythonVersion = "3.11"
typeCheckingMode = "standard"
# Many rules disabled for pandas/dagster compatibility
reportUnknownMemberType = false
reportUnknownArgumentType = false
# ... (12 rules disabled)
```

## Decision

Replace basedpyright with ty as the primary Python type checker for the Phlo codebase.

Key decisions:

1. **Remove basedpyright** from dev dependencies; keep **ty** (already present).
2. **Add `[tool.ty]`** configuration to `pyproject.toml` matching current scope.
3. **Update CI** to run `uv run ty check` instead of `uv run basedpyright`.
4. **Configure rule equivalents** for currently disabled basedpyright rules.
5. **Run in parallel initially** (if needed) to validate parity before full cutover.
6. **Remove `[tool.basedpyright]`** section after successful migration.

## Implementation

### Phase 1: Add TY Configuration

#### [MODIFY] [pyproject.toml](file:///Users/garethprice/Developer/phlo/pyproject.toml)

Add `[tool.ty]` section matching current type checking scope:

```toml
[tool.ty]
include = ["src/phlo"]
exclude = ["src/phlo/testing"]
python-version = "3.11"

[tool.ty.rules]
# Disable noisy rules for pandas/dagster compatibility (equivalent to basedpyright settings)
# ty uses rule names like "possibly-unbound-attribute", "unknown-argument", etc.
# We will start with default rules and refine based on initial run results
```

---

### Phase 2: Update CI Pipeline

#### [MODIFY] [ci.yml](file:///Users/garethprice/Developer/phlo/.github/workflows/ci.yml)

Replace the type-check job:

```diff
   type-check:
     runs-on: ubuntu-latest
     steps:
       - uses: actions/checkout@v4

       - name: Install uv
         uses: astral-sh/setup-uv@v7
         with:
           version: "latest"

       - name: Set up Python
         run: uv python install 3.11

       - name: Install dependencies
         run: uv sync --dev

-      - name: Type check with basedpyright
-        run: uv run basedpyright src/phlo
+      - name: Type check with ty
+        run: uv run ty check src/phlo
```

---

### Phase 3: Update Dependencies

#### [MODIFY] [pyproject.toml](file:///Users/garethprice/Developer/phlo/pyproject.toml)

Remove basedpyright from dev dependencies:

```diff
 [dependency-groups]
 dev = [
     "pytest>=8.4.2",
     "sqlfluff>=3.5.0",
     ...
     "ruff",
-    "basedpyright",
     "ty",
 ]
```

---

### Phase 4: Cleanup

#### [DELETE] `[tool.basedpyright]` section from pyproject.toml

Remove the entire basedpyright configuration block (lines 160-182).

## Consequences

### Positive

- **Faster CI**: Type checking will complete in seconds instead of minutes.
- **Faster development**: Incremental editor checks will be near-instant.
- **Unified toolchain**: Ruff, uv, and ty form a cohesive Astral ecosystem.
- **Better diagnostics**: Rich, contextual error messages improve DX.
- **Active development**: ty is actively maintained by Astral with planned stable release in 2026.

### Negative

- **Beta software**: ty is still in beta; edge cases may exist.
- **Rule mapping**: Some basedpyright rules may not have exact ty equivalents.
- **Configuration learning curve**: Different configuration syntax from pyright.

### Risks & Mitigations

| Risk                                 | Mitigation                                |
| ------------------------------------ | ----------------------------------------- |
| Missing type issues not caught by ty | Run both checkers in CI during transition |
| Beta instability                     | Pin ty version; update carefully          |
| Editor integration gaps              | ty has VS Code, PyCharm, Neovim support   |

## Verification

> [!IMPORTANT]
> Please suggest any additional manual verification steps you'd like me to perform.

### Automated Tests

```bash
# Run ty locally to verify configuration
uv run ty check src/phlo

# Compare output with basedpyright (optional during transition)
uv run basedpyright src/phlo
```

### CI Validation

After merging, verify the `type-check` job passes in GitHub Actions.

## Related

- ty documentation: https://docs.astral.sh/ty/
- Astral announcement: https://astral.sh/blog/ty
- Prior: [ADR 0006 - Public API and Structured Logging](./0006-public-api-and-structured-logging.md)
