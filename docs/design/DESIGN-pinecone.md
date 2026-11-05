# Pinecone — Style Reference
> graph paper under a cobalt halftone. Frame every block in square 1px stone hairlines on a near-white sheet and spend electric blue only on actions, figures and one headline word.

**Theme:** light

Source measurements are normalized; roles and recommendations are interpreted. Font summary lists are independent, not paired by position. HTML examples are reconstructions, not source components.

Pinecone is a near-white technical sheet ruled into a visible grid: a 1184px content column framed by full-height #e7e5e4 hairlines, with every card, button and input cut at 0px corners and joined edge to edge. Stone-toned ink #1c1917 carries the copy, and a single electric Index Cobalt #002bff does all the talking: the announcement strip, the square primary button, one word of the headline, the halftone dot field beside the hero and the benchmark figures. GT Planar sets 44px and 32px headings at a confident 700 with only slight negative tracking, while widely tracked 14px uppercase eyebrows and JetBrains Mono tabs and brace tags give the page the feel of a well-kept engineering console rather than an AI-hype poster.

## Colors

| Name | Value | Role |
|------|-------|------|
| Index Cobalt | `#002bff` | Announcement strip, square primary buttons, the headline accent word and benchmark figures — one saturated blue against stone grays |
| Stone Ink | `#1c1917` | Headings, navigation, body copy, logo and line icons, plus the filled selected code tab and the closing Start building button |
| Frost Canvas | `#fbfbfc` | Page canvas, header field, ghost-button and input fills — a faintly cool off-white so Paper cards read as raised sheets |
| Paper | `#ffffff` | Grid cards, code blocks, the console panel and button labels on Index Cobalt |
| Hairline Stone | `#e7e5e4` | The 1px rules that build the whole page: column gutters, header base, card seams, ghost buttons and inputs |
| Graphite Stone | `#57534e` | Lead paragraphs, card body copy and inactive segment labels — a warm gray that keeps long copy softer than the ink |
| Pebble Gray | `#a8a29e` | Section eyebrows, footer column heads, code comments, stat captions and the legal line |
| Haze Cobalt | `#7f95ff` | Cobalt at 50% strength: the idle Subscribe block, console button outlines and the fading dots of the hero halftone |
| Cobalt Wash | `#f2f4ff` | Active row tint in the console sidebar (cobalt at 5% opacity) behind a 2px Index Cobalt left rule |
| Console Slate | `#e2e8f0` | Cooler 1px borders inside the product console mock-up: sidebar edge, tab rail, filter fields and chart frames |
| Live Green | `#22c55e` | The 9999px live-status dot beside the record count and high similarity scores in the console |

## Typography

### GT Planar — Grilli Type's GT Planar, loaded as the variable GT_Planar_VCTR_VF file under the alias gtPlanar, sets everything but code: 44px/700 hero headlines at -0.88px tracking, 32px/700 section heads, 20px/700 card titles, 16px/400 body at 1.5 and 15px/400 navigation. Its squared, slightly mechanical grotesk shapes suit the ruled grid; the 14px/500 uppercase eyebrows open up to +1.8px tracking.
- **Substitute:** Inter, Arial, sans-serif
- **Licence:** commercial (the font file names it GT Planar VCTR VF); buy a licence from its foundry, or use the substitute
- **Weights:** 400, 500, 600, 700
- **Sizes:** 9px, 10px, 11px, 12px, 13px, 14px, 15px, 16px, 18px, 20px, 24px, 32px, 44px
- **Line height:** 1.00, 1.10, 1.20, 1.30, 1.33, 1.40, 1.43, 1.50, 1.60, 1.85
- **Letter spacing:** -0.88px at 44px; -0.6px on one 32px heading; +1.8px on 14px and 12px uppercase eyebrows; +0.15px at 13px; +0.17px at 11px labels; otherwise 0

### JetBrains Mono — The developer register: 13px/400 code lines at 22.1px line-height, the 13px agent tabs (Claude Code, Cursor, Copilot), the cobalt {lower cost} brace tags, console hostnames at 11px and the 14px/500 GET STARTED eyebrow at +1.8px tracking. It appears wherever the page wants to look like a terminal rather than a brochure.
- **Substitute:** SFMono-Regular, Menlo, Consolas, monospace
- **Licence:** open (SIL Open Font License); free to use and self-host
- **Weights:** 400, 500, 600
- **Sizes:** 11px, 13px, 14px
- **Line height:** 1.50, 1.70, 1.85
- **Letter spacing:** +1.8px at 14px uppercase; +1.5px at 13px on ellipsis glyphs; otherwise 0

### Type Scale

| Role | Family | Weight | Size | Line Height | Letter Spacing |
|------|--------|--------|------|-------------|----------------|
| micro-label | GT Planar | 400 | 10px | 1.6 | 1px |
| caption | GT Planar | 400 | 12px | 1.33 | 0px |
| code | JetBrains Mono | 400 | 13px | 1.7 | 0px |
| eyebrow | GT Planar | 500 | 14px | 1.5 | 1.8px |
| nav | GT Planar | 400 | 15px | 1.4 | 0px |
| body | GT Planar | 400 | 16px | 1.5 | 0px |
| lead | GT Planar | 400 | 18px | 1.5 | 0px |
| card-title | GT Planar | 700 | 20px | 1.4 | 0px |
| stat | GT Planar | 700 | 24px | 1.33 | 0px |
| section-heading | GT Planar | 700 | 32px | 1.2 | 0px |
| display | GT Planar | 700 | 44px | 1.1 | -0.88px |

## Spacing & Layout

**Base unit:** 4px

**Density:** comfortable

- **Page max-width:** 1184px
- **Section gap:** 96px
- **Card padding:** 32px
- **Element gap:** 16px

### Border Radius

- **buttons:** 0px
- **cards:** 0px
- **inputs:** 0px
- **tags:** 0px
- **images:** 0px
- **dots:** 9999px

## Components

### Cobalt Announcement Strip
**Role:** Site-wide release banner

A full-width 33px Index Cobalt #002bff strip with Paper 14px/400 GT Planar text at 16.8px line-height, 8px 16px 8px 32px padding, an underlined Read the announcement link and a 12px Dismiss control with an x at the right edge.

### Ruled Header Bar
**Role:** Top-level site navigation

A 75px Frost Canvas bar with a 1px #e7e5e4 bottom rule, the Pinecone wordmark at left and 15px/400 Stone Ink #1c1917 links. At right, search, theme, Contact and Log in sit in joined cells divided by 1px Hairline Stone borders with 8px 12px padding, ending in a 39px Index Cobalt square button.

### Cobalt Square Button
**Role:** Primary conversion action

Index Cobalt #002bff fill and 1px border, Paper #ffffff label in GT Planar 15px/400 at 21px line-height, 8px 16px padding, 0px radius and no shadow. In the hero it is wrapped by four small Stone Ink corner crop marks that sit just outside the box.

### Hairline Ghost Button
**Role:** Secondary action beside section heads

Frost Canvas #fbfbfc fill, Stone Ink #1c1917 15px/400 label at 21px line-height, a 1px Hairline Stone #e7e5e4 border, 10px 20px padding, 0px radius and no shadow; trailing arrows such as Explore Nexus are plain text glyphs.

### Experience Segmented Switch
**Role:** Builder or Business Leader audience toggle

A 1px #e7e5e4 bordered box holding a 12px/500 uppercase Graphite Stone label at +1.2px tracking, then segments at 14px/500 with 20px line-height and 6px 16px padding; the active segment is an Index Cobalt fill with Paper text, the other is transparent Graphite Stone #57534e.

### Tracked Eyebrow Label
**Role:** Section kicker above headings

GT Planar 14px/500 uppercase at 21px line-height and +1.8px tracking, in Pebble Gray #a8a29e on section heads and Index Cobalt #002bff in the hero; process steps use the same style with a number and middle dot, as in 01 · WRITE.

### Mono Agent Tab
**Role:** Code-sample selector for agents and SDKs

JetBrains Mono 13px/400 at 19.5px line-height with 8px 14px padding and 0px radius. The selected tab is a Stone Ink #1c1917 fill with Frost Canvas #fbfbfc text; unselected tabs are transparent Graphite Stone text on a Frost Canvas rail ruled top and bottom by 1px hairlines.

### Numbered Code Block
**Role:** Install and quickstart snippet

A Paper #ffffff panel under the tab rail with JetBrains Mono 13px/400 code at 22.1px line-height, Pebble Gray #a8a29e line numbers and comments, Stone Ink commands, and a square 1px #e7e5e4 bordered copy button at the top right.

### Mono Brace Tag
**Role:** Benchmark claim label

A Paper box with a 1px Hairline Stone #e7e5e4 border and 0px radius containing JetBrains Mono 13px/400 Index Cobalt #002bff text wrapped in literal braces, as in {lower cost}; it sits above a 16px/400 Stone Ink claim line.

### Benchmark Stat Rail
**Role:** Right-hand figure column of the benchmark table

A 200px Paper cell with a 1px #e7e5e4 left border and 32px padding, right-aligned: a 12px/400 up to prefix, a 32px/700 Index Cobalt figure, 10px/400 Pebble Gray caption lines at 14px line-height and a 2px Index Cobalt progress rule beneath. Inactive rows gray the figure out.

### Hairline Grid Card
**Role:** Trust and process feature cell

A Paper #ffffff cell in an edge-to-edge 3-column grid sharing 1px Hairline Stone borders, 32px padding, 0px radius and no shadow: a line icon in a 48px bordered square, a 20px/700 Stone Ink title at 28px line-height and 14px/400 Graphite Stone #57534e copy at 21px line-height.

### Console Showcase Panel
**Role:** Product UI demonstration

A Paper #ffffff app window with a 1px Console Slate #e2e8f0 border and the Console Float shadow (0 8px 32px rgba(0,0,0,0.1), 0 2px 8px rgba(0,0,0,0.06)). The active sidebar row uses Cobalt Wash #f2f4ff, a 2px Index Cobalt left rule and 13px/500 cobalt text at +0.15px tracking.

### Square Email Field
**Role:** Newsletter subscribe form

A 46px Frost Canvas #fbfbfc input with a 1px #e7e5e4 border, 10px 16px padding, 0px radius and 15px/400 Stone Ink text at 24px line-height, butted directly against a square Subscribe block shown in Haze Cobalt #7f95ff until the field is valid.

### Dotted Logo Marquee
**Role:** Customer proof strip

An 80px band between 1px Hairline Stone rules over a faint dotted pattern, scrolling monochrome Stone Ink customer wordmarks (Microsoft, OpenAI, cohere, FOX) spaced evenly with 32px side padding and no cards or dividers between them.

### Split CTA Band
**Role:** Closing start-building call

A Paper band divided by 1px #e7e5e4 verticals: a 32px/700 Stone Ink two-line heading, an 18px/400 Graphite Stone lead at 27px line-height, and a stacked pair of a Stone Ink #1c1917 Start building square and a Hairline Ghost Get a demo button.

### Four Column Footer
**Role:** Sitemap and legal footer

Frost Canvas with a 1px #e7e5e4 top rule, the wordmark at left and joined bordered social icon squares at right, then four columns of 14px/400 Stone Ink links at 18.2px line-height with 8px 0 padding under Pebble Gray heads, closed by a 13px/400 Pebble Gray legal line.

## Do's and Don'ts

### Do
- Use Frost Canvas #fbfbfc as the page and Paper #ffffff for cards, code blocks and the console, separated by 1px Hairline Stone #e7e5e4 seams.
- Set hero headlines in GT Planar 44px/700 at 48.4px line-height and -0.88px tracking, and color one key word Index Cobalt #002bff.
- Keep every button, card, input and tag at a 0px radius; reserve 9999px for the small Live Green status dot only.
- Apply 14px/500 uppercase eyebrows with +1.8px tracking above section heads, in Pebble Gray #a8a29e or Index Cobalt in the hero.
- Build primary actions as Index Cobalt #002bff squares with Paper 15px/400 labels and 8px 16px padding; pair them with a Frost Canvas ghost button bordered in Hairline Stone.
- Set code, agent tabs and brace tags in JetBrains Mono 13px/400; mark the selected tab with a Stone Ink #1c1917 fill and Frost Canvas text.
- Hold content inside a 1184px column framed by full-height hairline gutters, with 96px between sections and 32px card padding.

### Don't
- Do not round corners on cards or buttons; even the 8px or 12px radii common on AI sites break the 0px ruled grid.
- Do not float cards on soft drop shadows; use shared 1px #e7e5e4 borders and keep the 0 8px 32px shadow for the console mock-up alone.
- Do not introduce purple, teal or gradient brand washes; the only hue is Index Cobalt #002bff and its 50% Haze Cobalt and 5% Cobalt Wash tints.
- Do not set body copy in pure black; use Stone Ink #1c1917 for primary text and Graphite Stone #57534e for supporting paragraphs.
- Do not set headings at 400 or 500; GT Planar headings run 700 from 20px card titles to the 44px display.
- Do not switch code, terminal tabs or brace tags to the sans; they stay JetBrains Mono so the developer layer stays distinct.
- Do not fill sections with colored bands; every section sits on Frost Canvas and changes only through hairline frames and Paper cards.

## Elevation

- **Console Float:** `rgba(0, 0, 0, 0.1) 0px 8px 32px 0px, rgba(0, 0, 0, 0.06) 0px 2px 8px 0px`
- **Cobalt Focus Ring:** `rgb(0, 43, 255) 0px 0px 0px 1px`

## Surfaces

- **Frost Canvas** (`#fbfbfc`) — Page canvas, header field, ghost buttons and the email input
- **Paper** (`#ffffff`) — Grid cards, code block body, stat rail and the console panel
- **Cobalt Wash** (`#f2f4ff`) — Selected sidebar row inside the product console
- **Stone Ink** (`#1c1917`) — Selected code tab and the closing Start building button
- **Index Cobalt** (`#002bff`) — Announcement strip, primary buttons and active segment

## Imagery

There is no photography and no illustration of people. The visual layer is product and data: a full product-console mock-up (sidebar, metrics tabs, line and bar charts in cobalt, violet and cyan) lifted on the page's only shadow, a numbered code block, small cobalt bar-chart and area-sparkline micro-graphics inside the process cards, and big cobalt benchmark figures with 2px progress rules. The signature texture is a halftone field of tiny Index Cobalt dots that thins from dense at the right edge to nothing near the headline, echoed by a faint dotted pattern behind the grayscale customer-logo marquee. Icons are thin 1.5px Stone Ink line glyphs set inside 48px hairline-bordered squares; everything is square-cornered.

## Layout

The page is a single ruled sheet: a 1184px column (content starts 32px in) bounded by full-height 1px Hairline Stone gutter lines running header to footer, with horizontal hairlines closing each band. A 33px Index Cobalt announcement strip sits above a 75px header whose right-hand utilities are joined bordered cells ending in a square cobalt button. The hero is left-aligned: a centred Select Experience segmented switch, a cobalt tracked eyebrow, the 44px headline with one rotating cobalt word, an 18px lead in Graphite Stone and two buttons framed by corner crop marks, while a blue halftone dot field fills the right third. Below, an 80px dotted logo marquee, then sections at roughly 96px intervals each open with eyebrow, 32px heading and lead on the left and a ghost button at the far right, then edge-to-edge hairline grids: a tabbed code block, a 2-column benchmark table with a stat rail, a 3-column process strip, a 3-column trust grid, a split closing CTA band and a 4-column footer.

## Similar Brands

- **Vercel** — Shares a hairline-ruled grid layout with visible column lines and crosshair-style corner markers around blocks.
- **PlanetScale** — Uses square-cornered, border-built sections and dense technical type for a database product, with almost no shadow.
- **Tailwind CSS** — Same device of a centred content column framed by full-height gutter lines on a pale canvas.
