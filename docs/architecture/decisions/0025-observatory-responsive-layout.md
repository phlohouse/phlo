# 25. Observatory responsive layout

Date: 2025-12-19

## Status

Accepted

## Context

Observatory currently uses a fixed desktop layout with shadcn's `SidebarProvider` which already includes mobile infrastructure (Sheet overlay, `useIsMobile()` hook). However, the app is not fully optimized for smaller screens:

1. **Tablet breakpoints**: The md (768px) breakpoint jumps directly from mobile to desktop without tablet-optimized layouts.
2. **Touch targets**: Some interactive elements (table cells, buttons) are too small for comfortable touch interaction.
3. **Content overflow**: Data tables and code blocks can overflow horizontally on narrow viewports.
4. **Header density**: The header wastes space on mobile with redundant labels.

Related bead: `phlo-wq1`

## Decision

We will enhance the existing responsive infrastructure with targeted improvements for tablets (md-lg) and mobile (sm) breakpoints.

### 1. Breakpoint Strategy

Use Tailwind's default breakpoints consistently:

| Breakpoint | Width     | Layout                                    |
| ---------- | --------- | ----------------------------------------- |
| `sm`       | < 640px   | Mobile: Sheet sidebar, compact header     |
| `md`       | 640-768px | Tablet: Icon-collapsed sidebar by default |
| `lg`       | > 1024px  | Desktop: Full sidebar                     |

### 2. Changes by Component

#### Root Layout (`__root.tsx`)

- Reduce header padding on mobile: `px-4 md:px-4` → `px-2 sm:px-4`
- Hide "Search" label on mobile, show only icon + ⌘K shortcut
- Make sidebar default to collapsed state on tablet (md)

#### AppSidebar (`AppSidebar.tsx`)

- Already uses `collapsible="icon"` - no changes needed
- Sidebar header already compact

#### Data Tables (`DataPreview.tsx`, `QueryResults.tsx`)

- Add horizontal scroll wrapper with `-webkit-overflow-scrolling: touch`
- Increase touch targets: min cell height 44px on mobile
- Use `whitespace-nowrap` on table headers to prevent wrapping

#### Table Browser (`TableBrowser.tsx`)

- Already virtualized - ensure touch scrolling works
- Increase row height on mobile: `h-8` → `h-10 sm:h-8`

#### Query Editor (`QueryEditor.tsx`)

- Make editor height responsive: `min-h-[200px] md:min-h-[300px]`
- Stack action buttons vertically on mobile

### 3. CSS Additions

Add to `styles.css`:

```css
/* Touch-friendly scrolling */
@layer base {
  .touch-scroll {
    -webkit-overflow-scrolling: touch;
    overscroll-behavior: contain;
  }
}

/* Mobile-first table density */
@media (max-width: 640px) {
  :root {
    --table-cell-py: 0.625rem; /* 10px - larger touch targets */
  }
}
```

### 4. Modified Files

| File                               | Changes                                   |
| ---------------------------------- | ----------------------------------------- |
| `routes/__root.tsx`                | Responsive header, tablet sidebar default |
| `components/data/DataPreview.tsx`  | Scroll wrapper, responsive cell sizing    |
| `components/data/QueryResults.tsx` | Scroll wrapper, touch targets             |
| `components/data/QueryEditor.tsx`  | Responsive height, button stacking        |
| `components/data/TableBrowser.tsx` | Responsive row height                     |
| `styles.css`                       | Touch scroll utilities, mobile density    |

## Consequences

### Positive

- Observatory usable on tablets and phones
- Touch-friendly controls improve accessibility
- Minimal changes to existing architecture (leverages shadcn mobile infrastructure)
- No breaking changes to desktop experience

### Negative

- Slightly increased CSS complexity
- Some features may be harder to use on very small screens (< 375px)

### Risks

- Testing on real devices needed (simulator may not catch touch issues)

## Verification Plan

### Browser Testing

1. Open Observatory at http://localhost:3001
2. Use browser DevTools (F12) → Toggle device toolbar (Ctrl+Shift+M)
3. Test at these viewports:
   - iPhone SE (375×667)
   - iPad Mini (768×1024)
   - Desktop (1920×1080)

### Manual Verification Checklist

1. **Mobile (375px)**:
   - [ ] Sidebar opens as sheet overlay when hamburger tapped
   - [ ] Header shows search icon only (no "Search" text)
   - [ ] Data tables scroll horizontally without page scroll
   - [ ] Table rows are tappable (44px+ height)

2. **Tablet (768px)**:
   - [ ] Sidebar starts collapsed (icon-only)
   - [ ] Can expand sidebar with trigger button
   - [ ] Query editor has reasonable height

3. **Desktop (1024px+)**:
   - [ ] Full sidebar visible
   - [ ] No visual regressions from current layout

### TypeScript/Lint

```bash
cd packages/phlo-observatory/src/phlo_observatory && npm run lint && npm run typecheck
```

## Beads

- phlo-wq1: Observatory: Responsive/mobile layout (complete)
