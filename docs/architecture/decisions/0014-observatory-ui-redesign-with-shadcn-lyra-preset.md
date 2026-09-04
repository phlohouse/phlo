# 14. Redesign Observatory UI with shadcn Lyra preset (fixed design system)

Date: 2025-12-14

## Status

Accepted

## Context

The Observatory UI currently uses ad-hoc Tailwind styling and bespoke components, making it hard to
iterate quickly and keep the experience cohesive as the product grows.

Shadcn/ui now supports generating a consistent component + styling baseline via presets, including
the Lyra style and modern CSS-variable theming. We want a full redesign of the Observatory UI
using that baseline.

This decision is tracked by bead `phlo-bms` (closed).

## Decision

- Use shadcn/ui as the Observatory UI component system and adopt the Lyra preset baseline.
- Generate the baseline in a throwaway workspace via:
  - `npx shadcn@latest create --preset "https://ui.shadcn.com/init?base=base&style=lyra&baseColor=neutral&theme=amber&iconLibrary=lucide&font=jetbrains-mono&menuAccent=subtle&menuColor=default&radius=default&template=start" --template start`
- Copy the relevant artifacts into `packages/phlo-observatory/src/phlo_observatory/` (CSS variables, `components.json`,
  utility helpers, and shadcn component primitives) and use them as the source of truth going
  forward.
- Ship a fixed design system:
  - No in-app controls for changing palettes, radii, or density.

## Consequences

- Observatory screens share consistent layout, typography, and interaction primitives.
- UI development accelerates (standard components + predictable tokens), and future work (command
  palette, tables, settings) builds on the same foundation.
- The frontend gains a small set of additional dependencies (shadcn + utilities + font package).
