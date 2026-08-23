# Contributing to Phlo

Thank you for contributing to Phlo. Please discuss substantial changes in an
issue before opening a pull request, and run `make check` locally before
submitting it.

## Contribution licence

By submitting a contribution, you confirm that you have the right to submit it
and agree to license it under AGPL-3.0-or-later.

For substantial external contributions, we may ask you to sign our Contributor
Licence Agreement so that Phlo can also offer commercial licences.

## Comments and docstrings

Phlo follows a concise, technically direct commenting style inspired by the
Redis source tree: comments explain why, contracts state guarantees, and
obvious code stays uncommented. The full style guide lives in the
`redis-style-code-comments` agent skill; the binding rules are below.

### Module headers (enforced)

Every tracked `.py`, `.ts`, `.tsx`, `.js`, `.sql`, and `.sh` file starts with
a top-of-file block. For Python this is a module docstring as the first
statement; for TypeScript a `/** ... */` block; for SQL `--` lines; for shell
a `#` block directly after the shebang.

- One sentence of purpose, then only facts evident from the code: contracts,
  invariants, ownership, ordering or failure semantics. Never invent intent.
- Two to six lines for substantive modules; one honest line for thin ones.
- Vendored or generated files may opt out with `phlo: no-header` on line 1.

The `check-file-headers` pre-commit hook enforces presence on changed files.

### Function and class docstrings

Write a one-line imperative contract as the summary line. Add short body
lines only when they carry information the signature does not: non-obvious
argument semantics, return and error behaviour, ordering guarantees,
lifecycle rules, ownership transfer.

- Do not use `Args:`/`Returns:`/`Parameters:` sections that restate
  parameter names or types; these ceremonial blocks are not used in Phlo.
- State raised errors inline as `Raises: SomeError when <condition>.`
- Executable doctest examples are welcome and run in CI via
  `tests/test_doc_examples.py`.
- Private helpers (`_`-prefixed) and self-evident one-liners may stay bare;
  never add a docstring that merely paraphrases the name.
- Test functions are exempt: the test name is the scenario description.

## Contributor Licence Agreement

The [Contributor Licence Agreement](CLA.md) describes the additional rights
that may be requested for substantial external contributions. It is not
required unless a Phlo maintainer asks you to complete it.
