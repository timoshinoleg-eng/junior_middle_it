# Interfere — Style Reference
> aurora haze behind a bug inbox. Build quiet white product pages where a lifelike issue-tracker window floats on a faint orange-to-blue glow and every control is a small hairline-lifted key.

**Theme:** light

Source measurements are normalized; roles and recommendations are interpreted. Font summary lists are independent, not paired by position. HTML examples are reconstructions, not source components.

Interfere is a near-monochrome light canvas that sells an issue tracker by showing it: the hero is a full, working-looking product window with a Berkeley Mono code diff, floating on a 20% orange-pink-violet-blue aurora haze. Everything else is restraint, with Ink #202020 text on Paper #ffffff, Mist Panel #f9f9f9 slabs, hairline rgba(0, 0, 0, 0.06) borders, and one Carbon Black #0d0d0d sign-up button. Inter at weight 500 carries a 56px display tracked to -2px, while 13px UI text and a Heldane Text serif testimonial give the page the calm of a well-made app rather than a launch poster.

## Colors

| Name | Value | Role |
|------|-------|------|
| Paper | `#ffffff` | Page canvas, navigation, product window, cards and secondary buttons |
| Ink | `#202020` | Headings, card titles, button labels and primary UI text — measured as 87.5% black, never pure black |
| Graphite | `#646464` | Navigation links, body paragraphs under headings and footer link lists at 60.8% black |
| Ash Gray | `#888888` | Not-yet-revealed words in the statement paragraph, timestamps and the second line of two-tone headings |
| Carbon Black | `#0d0d0d` | The single filled Sign up button in hero and footer — a 95% black fill is the only solid dark mass on an otherwise whisper-grey page |
| Mist Panel | `#f9f9f9` | Testimonial and compliance slabs, kanban board frame and footer top — a 2.4% black tint over Paper |
| Chip Gray | `#f1f1f1` | Keyboard-hint keys, neutral word highlights in the statement and soft pill fills |
| Hairline | `#f0f0f0` | Every 1px border on cards, dividers and tabs, drawn as rgba(0, 0, 0, 0.06) so it melts into any surface |
| Signal Blue | `#0588f0` | Status glyphs in the product mock and the highlighted verb finds in the statement paragraph — the one live-looking accent |
| Code Crimson | `#e93d82` | Keywords and function names in the Berkeley Mono code diff — syntax colour, never interface colour |
| Regression Tomato | `#e54d2e` | Removed-line diff markers, error-rate figures and the outlined illustration highlight — the colour of the bug being found |
| Patch Green | `#30a46c` | Added-line diff markers and the green row tint in code cards — the colour of the fix |
| Aurora Haze | `linear-gradient(90deg, rgba(255, 59, 0, 0.2) 0%, rgba(246, 0, 157, 0.2) 38%, rgba(151, 62, 198, 0.2) 71%, rgba(0, 142, 255, 0.2) 100%)` | Blurred glow behind the hero window, testimonial and footer CTA — the page's only colour field, held at 20% so it reads as light |
| Signal Wash | `#e6f4fe` | Pale chip behind the Signal Blue highlighted word in the scroll-revealed statement |
| Fog Gray | `#bbbbbb` | Code line numbers and the dimmest metadata in product mockups |
| Pressed Gray | `#eaeaea` | Active segmented tab fill and avatar placeholders, a 6% black tint |

## Typography

### Inter — The whole interface and every heading, self-hosted as InterVariable with ss02 and ss03 on. Display is 56px/500 at 56px line-height and -2px tracking; section headings 28px/500; UI runs at 13px with a 450 in-between weight for navigation. Medium rather than bold keeps the page feeling like product chrome.
- **Substitute:** Inter, system-ui, sans-serif
- **Licence:** open (SIL Open Font License); free to use and self-host
- **Weights:** 400, 450, 500
- **Sizes:** 10px, 11px, 12px, 13px, 15px, 24px, 28px, 56px
- **Line height:** 1.00, 1.23, 1.29, 1.33, 1.45, 1.54, 1.60
- **Letter spacing:** -2px at 56px on the hero (-0.56px also measured on 56px spans); -0.28px at 28px; -0.24px at 24px; +0.5px on 10px uppercase labels; 0 at UI sizes
- **OpenType features:** `"ss02", "ss03"`

### Berkeley Mono — Code diffs inside the product window and feature cards, always 12px/400 at 16px line-height with syntax colour. A custom Interfere cut of the commercial Berkeley Mono variable; its squared, slightly retro shapes make the mock code read as a real editor.
- **Substitute:** JetBrains Mono, ui-monospace, monospace
- **Licence:** commercial (the font file names it Berkeley Mono Variable Interfere); buy a licence from its foundry, or use the substitute
- **Weights:** 400
- **Sizes:** 12px
- **Line height:** 1.33
- **Letter spacing:** 0 at 12px

### Heldane Text — The customer testimonial only, at 28px/400 with 36px line-height and -0.28px tracking, centred. Klim's bookish serif is the page's single editorial voice and echoes the serif wordmark in the navigation.
- **Substitute:** Newsreader, Georgia, serif
- **Licence:** commercial; buy a licence from its foundry, or use the substitute
- **Weights:** 400
- **Sizes:** 28px
- **Line height:** 1.29
- **Letter spacing:** -0.28px at 28px
- **OpenType features:** `"ss03"`

### Departure Mono — A single pixel-grid uppercase label, RESET PASSWORD LINK, inside the first feature illustration, at 12px/400 and 12px line-height in Regression Tomato. A small bitmap wink that matches the checkered logo mark.
- **Substitute:** ui-monospace, monospace
- **Licence:** open (SIL Open Font License); free to use and self-host
- **Weights:** 400
- **Sizes:** 12px
- **Line height:** 1.00
- **Letter spacing:** 0 at 12px

### Type Scale

| Role | Family | Weight | Size | Line Height | Letter Spacing |
|------|--------|--------|------|-------------|----------------|
| label | Inter | 500 | 10px | 1.6 | 0.5px |
| code | Berkeley Mono | 400 | 12px | 1.33 | 0px |
| pixel-label | Departure Mono | 400 | 12px | 1 | 0px |
| date | Inter | 500 | 12px | 1 | 0px |
| nav | Inter | 450 | 13px | 1.23 | 0px |
| ui-body | Inter | 400 | 13px | 1.54 | 0px |
| button | Inter | 500 | 13px | 1.54 | 0px |
| body | Inter | 400 | 15px | 1.6 | 0px |
| card-title | Inter | 500 | 15px | 1.6 | 0px |
| heading | Inter | 500 | 28px | 1.29 | -0.28px |
| testimonial | Heldane Text | 400 | 28px | 1.29 | -0.28px |
| display | Inter | 500 | 56px | 1 | -2px |

## Spacing & Layout

**Base unit:** 4px

**Density:** comfortable

- **Page max-width:** 1244px
- **Section gap:** 144px
- **Card padding:** 24px
- **Element gap:** 6px

### Border Radius

- **kbd:** 4px
- **nav:** 6px
- **tabs:** 6px
- **buttons:** 8px
- **inner-panels:** 10px
- **cards:** 12px
- **panels:** 16px
- **pills:** 9999px

## Components

### Wordmark Navigation Bar
**Role:** Sticky top-level site navigation

A 64px Paper bar with a 1px Hairline bottom border. Checkered mark plus serif wordmark at left; centred 13px/450 Graphite links as 28px-tall ghost buttons with 0 8px padding and 6px radius; at right a Login text link and a white Sign up key with Hairline Lift, 6px radius and 0 6px 0 10px padding.

### Shortcut Kbd Badge
**Role:** Keyboard shortcut hint beside nav actions

A tiny Chip Gray #f1f1f1 key holding one uppercase letter (L, D) in Inter 11px/500 at 16px line-height, Graphite text, 4px radius and a 1px rgba(0, 0, 0, 0.06) border, sitting inside or beside the Login and Sign up controls.

### Carbon Sign-up Button
**Role:** Primary conversion action

Carbon Black #0d0d0d fill, Paper 13px/500 Inter label at 20px line-height, 8px radius, 0 12px padding, 32px height, no shadow and no border. Used once in the hero and once in the footer CTA.

### Hairline Lift Button
**Role:** Secondary action and white control

Paper fill with Ink #202020 13px/500 label, 8px radius, 0 12px padding, 32px height, and the Hairline Lift shadow instead of a border. Inside product mockups the same treatment shrinks to 28px height and 6px radius.

### Split Display Hero
**Role:** Opening headline and calls to action

Left: Ink Inter 56px/500 headline on two lines with 56px line-height and -2px tracking. Right: a right-aligned 15px/400 Graphite paragraph at 24px line-height over a Carbon Sign-up Button and Hairline Lift Button with 12px gap, all above an Aurora Haze glow.

### Floating Product Window
**Role:** Hero product demonstration

A near-full-width Paper app window with 12px radius and the Product Window Drop shadow. A narrow icon rail, a 13px breadcrumb header with avatar stack, a left property pane of 13px Graphite labels, and a right activity timeline with an embedded code diff card.

### Segmented Tab Pill
**Role:** Activity, Sessions and Findings tabs

30px-tall tabs with 4px 10px padding, 6px radius and a 1px rgba(0, 0, 0, 0.06) border in 13px/500 Ink. The active tab fills Pressed Gray #eaeaea; inactive tabs stay Paper. Spaced 8px apart, no shadow.

### Code Diff Card
**Role:** Inline code finding

Paper card with 8px radius and Hairline Lift, a 13px filename header with a hairline divider and Ash Gray Open link, then Berkeley Mono 12px/400 lines at 16px line-height: Fog Gray line numbers, Code Crimson keywords, Regression Tomato and Patch Green diff bars.

### Highlight Statement Block
**Role:** Scroll-revealed positioning paragraph

A 28px/500 Inter paragraph at 36px line-height and -0.28px tracking where read words turn Ink and unread words stay Ash Gray #888888. Key verbs sit in 4px-radius chips: Signal Blue text on Signal Wash for finds, Chip Gray for the others.

### Feature Illustration Card
**Role:** Three-up capability teaser

A 200px-tall frame with 12px radius, 1px rgba(0, 0, 0, 0.06) border and 4px padding holding a greyed skeleton UI with one coloured detail, faded at the bottom. Below it, a 15px/500 Ink title and a 15px/400 Graphite paragraph at 24px line-height.

### Kanban Issue Card
**Role:** Issue inbox column item

Inside a Mist Panel frame with 12px radius and a 10px-radius Paper inner board, each card is Paper with Hairline Lift and 8px radius: a 10px Ash Gray issue number, a 13px/500 Ink title with a coloured glyph, and a 13px Graphite priority and user-count row.

### Serif Testimonial Panel
**Role:** Customer quote

A full-container Mist Panel slab with 16px radius, 1px Hairline border and an Aurora Haze glow rising from the bottom edge. Centred Heldane Text 28px/400 quote at 36px line-height and -0.28px tracking, then a 48px round avatar with a 15px/500 name and Graphite role.

### Compliance Strip Panel
**Role:** Security and certification summary

A Mist Panel slab with 16px radius split into three cells by 1px Hairline dividers. A two-tone 28px/500 heading (Ink line, Ash Gray line) with a Learn more link, then SOC 2 and GDPR cells with white Hairline Lift icon tiles and 15px Ink, Ash Gray and Graphite lines.

### Release Note Card
**Role:** Changelog teaser in The Latest

An unfilled column with a 1px rgba(0, 0, 0, 0.06) top border and 24px 24px 0 0 padding: a 12px/500 uppercase Graphite date at 12px line-height, a 15px/500 Ink title and a 15px/400 Graphite summary at 24px line-height, three across with 40px gaps.

### Gradient CTA Footer
**Role:** Closing call to action and site map

A tall footer fading from a light grey top into Paper, with an Aurora Haze glow, the 56px/500 headline repeated centred over the two buttons, a row of small Hairline Lift AI-assistant icon keys, a four-column 13px Graphite link grid and a giant faded wordmark at the bottom.

## Do's and Don'ts

### Do
- Use Paper #ffffff as the canvas and step to Mist Panel #f9f9f9 only for contained slabs such as the testimonial, compliance strip and kanban frame.
- Set the display headline in Inter 56px/500 with 56px line-height and -2px tracking, Ink #202020, split left against a right-aligned 15px/400 Graphite paragraph.
- Reserve Carbon Black #0d0d0d for the single Sign up button: 32px tall, 8px radius, 0 12px padding, 13px/500 Paper label.
- Apply the Hairline Lift shadow to every white button and key instead of a visible border.
- Keep interface text at 13px: 450 weight for navigation, 400 for UI body at 20px line-height, 500 for buttons.
- Place the Aurora Haze gradient as a blurred 20% glow behind the hero product window and footer CTA, never behind body text blocks.
- Pair each heading with a two-tone line, Ink #202020 first and Ash Gray #888888 second, as in Secure by design. Safe by default.

### Don't
- Do not use pure black for text; Ink is 87.5% black #202020 and secondary text is Graphite #646464.
- Do not set the 56px display in 600-700 bold; keep Inter 500 with -2px tracking.
- Do not round buttons into 9999px pills; buttons use 8px, nav keys 6px, and only avatars and status dots are round.
- Do not draw cards with heavy drop shadows; use 1px rgba(0, 0, 0, 0.06) Hairline borders and the 12px card radius.
- Do not turn Signal Blue #0588f0 or Code Crimson #e93d82 into button or link colours; they live inside product mockups and one highlighted word.
- Do not replace Heldane Text in the testimonial with an Inter italic; the serif is the page's only editorial voice.
- Do not saturate the Aurora Haze; its stops stay at 20% opacity so the page still reads as white.

## Elevation

- **Hairline Lift:** `rgba(0, 0, 0, 0.06) 0px 0px 0px 0.5px, rgba(0, 0, 0, 0.1) 0px 1px 1px -1px, rgba(0, 0, 0, 0.05) 0px 1px 2px 0px`
- **Product Window Drop:** `rgba(0, 0, 0, 0.07) 0px 0px 0px 0.5px, rgba(0, 0, 0, 0.07) 0px 149px 199px 0px, rgba(0, 0, 0, 0.05) 0px 62.249px 83.137px 0px, rgba(0, 0, 0, 0.04) 0px 33.281px 44.449px 0px, rgba(0, 0, 0, 0.04) 0px 18.657px 24.918px 0px, rgba(0, 0, 0, 0.03) 0px 9.909px 13.234px 0px, rgba(0, 0, 0, 0.02) 0px 4.123px 5.507px 0px`

## Surfaces

- **Paper** (`#ffffff`) — Page canvas, navigation, product window and white buttons
- **Mist Panel** (`#f9f9f9`) — Testimonial, compliance slab, kanban frame and footer
- **Chip Gray** (`#f1f1f1`) — Keyboard hints, word highlight chips and soft pills
- **Pressed Gray** (`#eaeaea`) — Active segmented tab and selected rows in mockups
- **Carbon Black** (`#0d0d0d`) — The single filled Sign up button

## Imagery

There is almost no photography: the imagery is the product itself, rebuilt as live HTML rather than screenshots. Issue detail panes, kanban columns, activity timelines and Berkeley Mono code diffs appear at real 12-13px scale, often cropped and faded into the page with white gradient masks so they feel like glimpses through a window. Small round avatars (24px in the app, 48px in the testimonial) are the only photos. Colour enters through tiny Radix-style status glyphs in blue, amber, green and tomato, and through the soft Aurora Haze glow. Feature illustrations are greyed skeleton UIs with one coloured highlight, and icons are thin monochrome glyphs inside small white Hairline Lift tiles. Customer logos are flat monochrome wordmarks in a fading marquee.

## Layout

A centred 1244px container on a white page with a 64px sticky navigation bar: serif wordmark left, four links centred, Login and Sign up keys right. The hero splits a two-line 56px headline on the left from a right-aligned paragraph and button pair, then drops a near-full-width product window (about 1440px, 12px radius) that overlaps the glowing aurora band. Then a fading logo marquee, a 28px statement paragraph introduces a three-column feature row of illustration cards, and a full-width Mist Panel testimonial follows. Three alternating feature sections pair a left text column (heading, paragraph, two-column capability list under a hairline) with a right product crop. The page closes with a three-cell compliance slab, a three-column release-notes row with top hairlines, and a tall gradient footer that repeats the headline above a four-column link grid. Section spacing is generous (120-160px) while the UI inside stays dense at 4-8px gaps.

## Similar Brands

- **Attio** — Shares a white, near-monochrome canvas where full-fidelity product UI mockups do the visual work instead of illustration.
- **Vercel** — Uses a black-and-white palette with a single solid black primary button and hairline-bordered grey panels.
- **Plain** — Same quiet light developer-tool aesthetic with small 13px interface text and support-inbox product mockups as the hero.
- **Cal.com** — Shares Inter-set light pages, small subtle-shadow white controls and live-looking product windows embedded in the hero.
