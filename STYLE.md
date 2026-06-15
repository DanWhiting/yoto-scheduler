# Yoto Scheduler — Style Guide

## Vision

A **warm, friendly, mobile-first** parent-facing tool that feels at home next to the official Yoto app, with foundations borrowed from **Material Design 3** (the Android-native design language) and accessibility minimums from **WCAG 2.1 AA**.

The product is operated mostly on a phone, briefly, throughout the day. Decisions should favour:

- **Touch ergonomics** over information density.
- **Clarity** over decoration.
- **Forgiveness** (autosave, easy undo, soft destructive cues) over confirmation modals.
- **Consistency**: one design language across all pages so adding features doesn't add visual debt.

## Principles (used to decide between options later)

1. **One job per surface.** A card / a row / a screen shows one thing well rather than four things at once.
2. **Earn every pixel.** Decoration that isn't doing semantic work (separating, indicating state, drawing the eye) gets cut.
3. **Components, not pages.** Patterns are designed once and reused — a routine card, a chip, an action menu — so visual mistakes only need fixing in one place.
4. **State changes are visible.** Save / loading / error / success / focus / hover / disabled all have distinct styling. Never a silent wedge state.
5. **Light first, dark-ready.** Authoring in CSS variables so a `@media (prefers-color-scheme: dark)` swap is a future one-file change, not a rewrite.

## Foundations

### Colour

Warm coral primary on a paper-cream surface. All colours expressed as CSS variables in `app.css :root` so a future dark theme overrides one block.

| Role | Token | Value | Use |
|---|---|---|---|
| Primary | `--ys-primary` | `#e95b3c` | Primary buttons, active nav, focus ring tint |
| Primary deep | `--ys-primary-deep` | `#d04f33` | Hover/active state on primary |
| Primary soft | `--ys-primary-soft` | `rgba(233, 91, 60, 0.12)` | Soft backgrounds (chips, hover bg) |
| On-primary | — | `#ffffff` | Text on primary fills |
| Background | `--ys-bg` | `#faf8f4` | Page background |
| Surface | `--ys-surface` | `#ffffff` | Cards, dialogs, inputs |
| Text | `--ys-text` | `#2c2a26` | Body text |
| Muted text | `--ys-muted` | `#7c7670` | Captions, labels, placeholders |
| Border | `--ys-border` | `#ebe6da` | Card edges, dividers |
| Border strong | `--ys-border-strong` | `#d6d0c2` | Input outlines, prominent edges |
| Success | `--ys-success` | `#2e7d32` | "Saved", positive status |
| Error | `--ys-error` | `#c62828` | Destructive actions, validation errors |
| Track | `--ys-track-bg` | `#ece6d7` | Slider/progress unfilled track |

**Rules.** Never use pure black or pure white for text or backgrounds — both are jarring next to a warm palette. Always pair a colour with its `on-X` (text-on-colour) variant so contrast holds. Aim for **WCAG AA**: body text ≥ 4.5:1 against background, UI/large text ≥ 3:1.

### Typography

System font stack. No web fonts (faster on LAN, no licensing surface, scales nicely with OS settings).

```
-apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, "Helvetica Neue", Arial, sans-serif
```

Scale (CSS values; Material-3-inspired, pragmatic for web):

| Role | Size | Line | Weight | Letter | Use |
|---|---:|---:|---:|---:|---|
| Display | 28px | 36px | 700 | −0.01em | Page title (e.g. "Yoto Scheduler") |
| Heading | 22px | 30px | 700 | −0.01em | Section headers ("Routines") |
| Title | 17px | 24px | 600 | 0 | Card titles (routine name) |
| Body | 16px | 24px | 400 | 0 | Default body text |
| Body-sm | 14px | 20px | 400 | 0 | Secondary text |
| Caption | 12px | 16px | 500 | 0.05em | Uppercase label captions |

**Rules.** Maximum 3 weights on a page (400, 600, 700). Letter-spacing reserved for uppercase captions only — never on body text.

### Shape (border radius)

| Token | Value | Use |
|---|---:|---|
| `--ys-radius-xs` | 4px | Tags, chips, small badges |
| `--ys-radius-sm` | 10px | Inputs, small buttons |
| `--ys-radius` (md) | 14px | Cards, dialog panels, dropdowns |
| `--ys-radius-lg` | 20px | Hero / large modal panels |
| `--ys-radius-pill` | 999px | Pill buttons, nav chips, status badges |

**Rule.** Rounded everywhere; no sharp 90° corners except dividers. Pill radius reserved for buttons and status badges (round CTAs). Small radius for inputs and nav items (rounded boxes, not pills). Card radius for cards.

### Spacing (4px grid)

All margins, padding, and gaps snap to multiples of 4px.

| Step | Value | Common use |
|---:|---:|---|
| 1 | 4px | Icon → label gap |
| 2 | 8px | Tight grid gap, button internal padding (vertical) |
| 3 | 12px | Field padding, small grid gap |
| 4 | 16px | Card internal padding (horizontal), standard grid gap |
| 6 | 24px | Section spacing |
| 8 | 32px | Page section breaks |
| 12 | 48px | Min touch target |

**Rule.** Never invent a one-off `padding: 11px`. Snap to the grid.

### Elevation (shadow)

Just three levels — more than that and the page looks chaotic.

| Level | Token | Use |
|---|---|---|
| 0 | (none) | Flat surfaces, background |
| 1 | `--ys-shadow` | Cards at rest |
| 2 | `--ys-shadow-hover` | Cards on hover, dropdowns, popovers |
| 3 | (TBD) | Modals, toasts |

Material 3 uses a *surface tint* (tinting the surface slightly with primary) on elevated surfaces. We do this implicitly via the warm cream background — anything lifted onto pure white surface naturally separates.

### Motion

| Token | Value | Use |
|---|---|---|
| Duration short | 120–150ms | Hover, focus, simple state changes |
| Duration medium | 200–250ms | Card lift, dropdown open |
| Duration long | 300–400ms | Modal open/close, page transitions (when added) |
| Standard easing | `cubic-bezier(0.2, 0, 0, 1)` | Material 3 "standard" easing |

**Rule.** Default to 150ms standard easing. Reserve long durations for full-screen state changes.

### Accessibility (non-negotiable)

- **Touch targets**: 44×44 px minimum (iOS HIG), 48×48 preferred (Material).
- **Contrast**: WCAG AA — text 4.5:1, large text/UI 3:1.
- **Focus ring**: 3px `--ys-primary-soft` outline, never `outline: none` without a visible replacement.
- **Keyboard**: every action reachable via Tab; Enter/Space activate; Esc dismisses.
- **Reduced motion**: respect `@media (prefers-reduced-motion: reduce)` — kill transform animations.
- **Labels**: every form control has a visible or `aria-label` label.

## Component patterns

### Buttons (Shoelace)

| Variant | When | Token |
|---|---|---|
| `primary` | Primary CTA on a page (one at a time) | `--ys-primary` fill, white text |
| `default` | Secondary action | `--ys-surface` fill, `--ys-border-strong` border |
| `text` | Tertiary / inline | No fill, primary text |
| `danger` | Destructive — confirm-style buttons inside menus or modals | `--ys-error` fill |

Always **pill-shaped** (`--ys-radius-pill`). Min height 36px / min width 40px. Generous horizontal padding (~18px).

### Inputs (Shoelace `<sl-input>`, `<sl-range>`)

- Surface fill, `--ys-border-strong` border, `--ys-radius-sm` corners.
- Focused state: border → `--ys-primary`, 3px `--ys-primary-soft` ring.
- Label sits above the input, body-sm weight.
- Time inputs are native (`<sl-input type="time">`) — defer to the OS picker on mobile, don't custom-build one.

### Cards (routine card pattern, reusable for Collections / Events later)

- Surface white, 14px radius, 1px `--ys-border`, level-1 shadow.
- Hover lifts to level-2 shadow.
- Internal padding 16/20px depending on density.
- Primary content on top row (name + main control), secondary content below a divider.
- Destructive action absolutely top-right (icon button, muted by default, error on hover).

### Navigation

- Top of page: app title + horizontal nav strip below it.
- Each item is a small rounded box (`--ys-radius-sm`) with a subtle track background — visually distinct as a "button" at rest.
- Active page: primary fill, white text.
- Hover (non-active): soft-primary background, deeper text colour.
- Stub/coming-soon: 40% opacity, `pointer-events: none`, tooltip on hover.

### Badges / chips

- Pill-shaped, body-sm, no bold.
- Status badges (`saving`, `saved`, `error`) use semantic-soft backgrounds — never solid alarm colours.

### Icons

- Bootstrap Icons (Shoelace's bundled set). Outline style by default; filled only for emphatic state (e.g. `trash3-fill` for "remove" confirmation step inside a dropdown).
- Icon-only buttons must have an `aria-label` (Shoelace `<sl-icon-button label="...">` handles this).

## Empty / loading / error states

Three states each page must implement explicitly:

- **Loading**: `<sl-spinner>` + brief label. Never just a blank.
- **Empty**: dashed-bordered card with a single sentence inviting the next action. Always concrete (*"Add one to set a volume limit"*), never generic (*"Nothing here yet"*).
- **Error**: `<sl-alert variant="danger">` with the actual error message — never just a spinner that gets stuck.

## Copy

- **Title Case for interactive elements**: button labels, menu items, form-field labels, section headings. ("Add Routine", "Volume Limit", "Connect Yoto Account".)
- **Sentence case for body copy**: paragraphs, help text, status messages, placeholders, empty-state copy. ("Set the volume limits that apply at different times of day.")
- Conversational, not technical. ("Set the volume limits that apply at different times of day.", not "Configure cap thresholds per period.")
- Concrete, not generic. Mention the actual nouns (routine, player, card) instead of "item".
- No exclamation marks; no emoji unless the user explicitly wants them.

## What's already aligned

The current `app.css` implements:

- The colour tokens (just under different names — `--ys-*` here).
- Pill nav, 14px card radius, soft shadows.
- Pico + Shoelace theming using these tokens.
- Empty-state, save-state, loading states on the schedule page.

## What to migrate (not done yet)

- Add `--ys-radius-xs` (currently we mix 4 and 10).
- Add `--ys-shadow-3` for future modals.
- Add a `@media (prefers-reduced-motion: reduce)` block in `app.css`.
- Move uppercase caption styling into a reusable `.caption` class instead of `label > small`.
- Audit touch targets: `<sl-icon-button>` defaults to ~32px which is below the 44px ideal; bump via CSS.
