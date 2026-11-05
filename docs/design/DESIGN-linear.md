# Linear — Style Reference
> midnight precision instrument

**Theme:** dark

Source measurements are normalized; roles and recommendations are interpreted. Font summary lists are independent, not paired by position. HTML examples are reconstructions, not source components.

Linear's design system is a midnight command center built on near-black surfaces (#08090a) with paper-white type and one electric acid-lime accent (#e4f222) that functions as a functional flashlight — small, high-contrast, and used sparingly to signal action. The interface treats darkness as a substrate rather than a theme: text is crisp white at tight tracking (-0.022em), weights sit in a low 400–510 band rather than bold, and borders are hairline-thin (0.5px) to let geometry do the work that shadows usually would. Components feel precision-machined — 6px and 12px radii, compact 8–12px paddings, and almost no decorative ornament — letting the product UI (issue cards, kanban boards, AI agent panels) be the only visual texture in an otherwise quiet system.



## Colors

| Name | Value | Role |
|------|-------|------|
| Void | `#08090a` | Page canvas, full-bleed backgrounds — the default everything sits on |
| Carbon | `#0f1011` | Card surfaces, nav bars — one step above canvas for contained content |
| Obsidian | `#161718` | Elevated surfaces, deeper card panels |
| Graphite | `#23252a` | Subtle borders, dividers, ghost button outlines — low-contrast structural edges |
| Smoke | `#383b3f` | Hairline borders at higher contrast than graphite — section separators |
| Ash | `#62666d` | Muted body text, inactive icons, secondary metadata |
| Fog | `#8a8f98` | Tertiary text, placeholder copy, icon fills |
| Mist | `#d0d6e0` | Secondary headings, button text on dark surfaces |
| Bone | `#e5e5e6` | Near-white surface fills, high-contrast button text |
| Paper | `#ffffff` | Primary headings, hero type, max-contrast emphasis text |
| Acid Lime | `#e4f222` | Primary action buttons, active nav indicators — electric accent that breaks the monochrome system |
| Pulse Green | `#27a644` | Green outline accent for tags, dividers, and focused UI edges. Use as a supporting accent, not as a status color |
| Coral Red | `#eb5757` | Red wash for highlight backgrounds, decorative bands, and soft emphasis behind content. Use as a supporting accent, not as a status color |
| Signal Teal | `#02b8cc` | Decorative accent, informational icon fills |
| Iris Violet | `#6366f1` | Tag/badge fills — soft chromatic punctuation on tags and labels |
| Lavender | `#8b5cf6` | Secondary tag fills, category indicators |

## Typography

### Inter Variable — Primary UI and heading typeface — used across nav, body, headings, buttons, cards
- **Substitute:** Inter (variable), or system-ui as fallback
- **Weights:** 300, 400, 510, 590
- **Sizes:** 10, 11, 12, 13, 14, 15, 16, 17, 20, 24, 32, 48, 64, 72
- **Line height:** 1.0–2.75
- **Letter spacing:** -0.022em at 48–72px, -0.012em at 20–32px, -0.011em at 15px, -0.010em at 13–16px
- **OpenType features:** `"cv01" on, "ss03" on, "zero" on`

### Berkeley Mono — Code-adjacent UI text — issue IDs (ENG-2703), keyboard shortcuts, monospaced metadata
- **Substitute:** JetBrains Mono, IBM Plex Mono, or ui-monospace
- **Weights:** 400
- **Sizes:** 12, 14
- **Line height:** 1.40–1.71
- **Letter spacing:** -0.013em
- **OpenType features:** `"cv01" on, "ss03" on`

### Type Scale

| Role | Family | Weight | Size | Line Height | Letter Spacing |
|------|--------|--------|------|-------------|----------------|
| caption | — | — | 13px | 1.2 | — |
| body-sm | — | — | 15px | 1.6 | -0.165px |
| body-lg | — | — | 20px | 1.33 | -0.24px |
| subheading | — | — | 24px | 1.33 | -0.288px |
| heading-sm | — | — | 32px | 1.13 | -0.704px |
| heading | — | — | 48px | 1 | -1.056px |
| heading-lg | — | — | 64px | 1 | -1.408px |
| display | — | — | 72px | 1 | -1.584px |

## Spacing & Layout

**Base unit:** 4px

**Density:** compact

- **Page max-width:** 1200px
- **Section gap:** 96px
- **Card padding:** 24px
- **Element gap:** 8px

### Border Radius

- **cards:** 12px
- **pills:** 9999px
- **small:** 2px
- **badges:** 4px
- **inputs:** 6px
- **buttons:** 6px

## Components

### Primary Action Button (Acid Lime)
**Role:** High-emphasis CTA — the one chromatic button in the system

Background #e4f222, text #08090a, border-radius 6px, padding 10px 16px, Inter 14px / weight 510, letter-spacing -0.011em. Sits as the sole filled chromatic element — every other button on the site is neutral.

### Nav Text Button
**Role:** Top navigation items

Transparent background, text #d0d6e0, padding 8px 12px, Inter 13px / weight 400. No border, no fill — pure typographic nav with underline on hover.

### Pill Button
**Role:** Tag chips, status pills, compact action triggers

Background rgba(255,255,255,0.05), text #d0d6e0, border-radius 9999px, padding 4px 12px, Inter 12–13px / weight 400.

### Ghost / Outline Button
**Role:** Secondary actions, less prominent CTAs

Transparent background, border 1px #23252a, text #d0d6e0, border-radius 6px, padding 8px 12px, Inter 13px / weight 400.

### Sign-up Button (Rounded Pill, Neutral)
**Role:** High-emphasis nav CTA

Background #ffffff, text #08090a, border-radius 9999px, padding 8px 16px, Inter 13px / weight 510. White pill against the dark nav bar — the second highest-contrast element after the acid-lime CTA.

### Card (Product Screenshot Frame)
**Role:** Large showcase surface for product UI screenshots

Background #0f1011, border-radius 12px, inset shadow rgb(35,37,42) 0 0 0 1px, padding 24px. Hairline inner border defines the card edge — no outer shadow, no glow.

### Card (Subtle)
**Role:** Small content cards, nested panels

Background rgba(255,255,255,0.02), border-radius 6px, shadow rgba(0,0,0,0.4) 0 2px 4px, padding 8px. Almost invisible — the card barely separates from the canvas.

### Text Input
**Role:** Form fields, search inputs

Background rgba(255,255,255,0.02), border 1px rgba(255,255,255,0.08), text #d0d6e0, border-radius 6px, padding 12px 14px, Inter 14px / weight 400. Focus ring: border brightens to #d0d6e0.

### Badge / Status Tag
**Role:** Issue status, category labels, inline metadata

Background rgba(255,255,255,0.05), text #8a8f98, border-radius 4px, padding 0px 6px, Inter 12px / weight 400. Color-coded variants use Pulse Green, Coral Red, Iris Violet, or Lavender fills.

### Logo Mark
**Role:** Brand identification in nav

Linear wordmark + geometric glyph, Inter 16px / weight 510, color #ffffff. Glyph rendered as inline SVG in white.

### Logo Bar (Customer Strip)
**Role:** Social proof — customer logos in a horizontal row

Neutral grey logos (Vercel, Cursor, Oscar, OpenAI, Coinbase, Cash App, Boom, Ramp) at #8a8f98–#d0d6e0, evenly spaced with 48–64px gaps, no card backgrounds.

### Hero Gradient Floor
**Role:** Atmospheric base under the product screenshot

Linear gradient from rgb(8,9,10) at 10% to rgb(208,214,224) at 100% — a subtle light wash that grounds the floating product UI against the void.

## Do's and Don'ts

### Do
- Use Inter Variable with font-feature-settings 'cv01' on, 'ss03' on, 'zero' on — these alternate glyphs define Linear's typographic identity
- Use #e4f222 exclusively for the single primary action per view — never for decoration, never for secondary buttons
- Set body text at 16px Inter weight 400 with line-height 1.5 — larger reading sizes (17px+ at weight 590) are reserved for body emphasis blocks
- Use letter-spacing -0.022em at 48px and above — tight tracking is non-negotiable for display type
- Set card radius to 12px, button radius to 6px, pill radius to 9999px — three radii is the entire radius vocabulary
- Use 0.5px hairline borders (#23252a or #383b3f) instead of shadows for surface separation — Linear's elevation comes from borders and subtle inner shadows
- Keep section gaps at 96px and element gaps at 8px — the 8/12/24/96 spacing ladder is the rhythm

### Don't
- Do not use bold weights (700+) — Linear's type scale caps at weight 590, the system deliberately avoids heavy display weights
- Do not use decorative gradients on buttons, cards, or text — gradients are reserved for the hero atmospheric floor only
- Do not introduce additional chromatic accent colors as actions — the acid-lime button is the only chromatic UI element
- Do not use large radii (16px+) on cards or panels — 12px is the max card radius in this system
- Do not use shadows to separate cards from the canvas — use hairline borders (#23252a) and inner inset shadows instead
- Do not use chromatic text colors for body copy — all body text sits in the #d0d6e0 / #8a8f98 / #62666d grey scale
- Do not use Berkeley Mono for headings or marketing copy — it is reserved for issue IDs, keyboard shortcuts, and technical metadata

## Elevation

Elevation in Linear's system is achieved almost entirely through hairline borders (0.5px #23252a or 1px inset #23252a) and subtle dark drop shadows (rgba(0,0,0,0.4) 0 2px 4px) rather than layered shadow stacks. The visual hierarchy comes from the surface-level progression (#08090a → #0f1011 → #161718 → #23252a) and border definition, not from ambient shadow. The acid-lime CTA button uses an inset shadow stack (0px 5px 2px / 0px 3px 2px / 0px 1px 1px) — the only place in the system where a real shadow is applied to a chrome element.

## Surfaces

- **Void** (`#08090a`) — Page canvas — the default full-bleed background
- **Carbon** (`#0f1011`) — Card surfaces, product screenshot frames, nav containers
- **Obsidian** (`#161718`) — Elevated panels, deeper nested surfaces
- **Slate** (`#23252a`) — Interactive surface tint, ghost button fills, border-adjacent backgrounds

## Imagery

Linear's visual language is product-screenshot-first: the hero and section illustrations are real Linear app UI captured at full fidelity — issue cards, kanban boards, AI agent panels, command palettes — placed inside framed card containers with hairline borders. No stock photography, no lifestyle imagery, no abstract illustration. Logos appear as a customer strip in neutral grey (#8a8f98) at uniform size. Icons are minimal line-art SVGs in single-color grey scale. The hero screenshot floats on a subtle linear gradient (dark-to-light) that creates atmospheric depth without literal scenery. Every visual element is a functional artifact of the product itself.

## Layout

Layout is max-width contained at ~1200px, centered, with full-bleed dark backgrounds extending to viewport edges. The hero is a left-aligned oversized headline (64–72px) paired with a right-aligned link CTA, followed by a large product screenshot that bleeds beyond the max-width slightly. Section rhythm alternates between text-left/image-right 2-column compositions and full-width product showcase bands, separated by 96px vertical gaps. The customer logo strip is a single horizontal row. The page never uses 3-column card grids or masonry — information density stays low, with most sections using generous whitespace and a single focal point per screen. Navigation is a fixed top bar with left-aligned logo and right-aligned links, no sidebar, no mega-menu.

## Similar Brands

- **Vercel** — Same dark-canvas-first approach with hairline borders, tight Inter typography, and product-screenshot-as-hero layout — both treat the product UI as the visual content rather than illustration
- **Cursor** — Identical midnight dark mode with acid-lime accent CTA, compact Inter type at 400–510 weights, and product-screenshot showcase cards at 12px radius
- **Raycast** — Shared dark precision-instrument aesthetic — compact spacing, 6px button radius, monochromatic chrome with a single functional accent color for active states
- **Framer** — Same dark-canvas layout language with large 48–64px Inter headings at tight tracking, product-screenshot hero cards, and minimal ornament between sections
