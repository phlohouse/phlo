---
name: phlo-refactoring-docs
description: "Rewrites Phlo documentation while preserving source-verified behaviour and navigation. Use when reorganising, splitting, shortening, or updating existing docs pages."
---

# Refactor documentation without losing facts

Read the target page and its entry points. Use the
[engineering map](../../../docs/contributing/engineering-map.md) to find the
implemented authority. Use the [domain glossary](../../../docs/concepts/glossary.md)
for terminology and [CONTRIBUTING.md](../../../CONTRIBUTING.md) for repository
conventions.

## 1. Define the reader's task

Name the intended audience, outcome, and page type: tutorial, how-to guide,
reference, or explanation. For a mixed page, choose its primary purpose and
identify destinations for the other material before editing. Finish when the
recommended path and the proposed page boundaries are explicit.

## 2. Inventory and verify facts

Create a scratch inventory of the existing commands, flags, configuration
fields, defaults, supported versions, permissions, precedence, failure modes,
recovery steps, examples, and links. For every fact, record one disposition:

- Keep it, with its authoritative source.
- Move it to a named page and section, with a link from the original context.
- Correct or retire it because current source proves it inaccurate or obsolete.
- Mark it unresolved and report the missing evidence.

Use CLI help and command tests for commands, schemas and settings for fields,
and runtime code and tests for behaviour. For release and support claims, use
the release procedure and support manifest rather than inferred maturity.
Do not turn an unverified claim into a confident statement during a rewrite.
Finish the inventory when every behaviour-sensitive claim has a disposition.

## 3. Rewrite the source page

Put the common supported path first. Move exhaustive details into reference
pages and symptom-led diagnostics into troubleshooting pages. Preserve the
links that let readers reach moved facts. Use British English, concrete Phlo
terms, and runnable commands with their working directory and prerequisites.

Read `pymdx.toml` and neighbouring `meta.json` files when changing navigation.
The public docs source is `docs/`; `docs-site/` is generated output. For
generated CLI or API reference, change the owning source or
`scripts/generate_reference_docs.py`, then regenerate. Preserve generated-file
boundaries instead of hand-editing their output.

## 4. Verify preservation and rendering

Compare the old and new text against the inventory. Confirm that every retained
fact remains reachable and each deletion has a source-backed reason. Check
incoming links and heading anchors when moving or renaming content.

Run the documentation checks in the
[verification matrix](../../../docs/contributing/verification-matrix.md):

```bash
uvx --from codespell==2.4.1 codespell --config=.codespellrc <changed-files>
python3 scripts/check_markdown_links.py
make docs-build
git diff --check
```

Run `make setup` before repository-wide checks. Exercise changed commands in a
disposable project when they write data. For commands that would publish, deploy,
or mutate shared state, inspect the implementation and available dry-run instead;
state that live execution remains unverified.

When layout, navigation, or rendering changes, inspect the generated pages in
the browser using the root Makefile's docs preview targets. Capture and inspect
affected states. A successful build alone does not prove readable rendering.

## 5. Report the preserved contract

Summarise the new structure, where moved facts now live, intentional removals,
verification results, and unresolved claims. Finish when every inventory item
is accounted for and the documented user path is verified to the stated scope.
