---
name: OCAD Map Viewer
colors:
  # Primary brand — Forest Green (Terra & Forest design system)
  primary: "#243624"
  primary-container: "#3a4d39"
  on-primary: "#ffffff"
  on-primary-container: "#a7bda4"
  primary-fixed: "#d2e9ce"
  primary-fixed-dim: "#b7cdb2"

  # Secondary — Terra Brown
  secondary: "#775843"
  secondary-container: "#ffd4bb"
  on-secondary: "#ffffff"
  on-secondary-container: "#7a5a46"
  secondary-fixed: "#ffdcc7"
  secondary-fixed-dim: "#e7bea5"

  # Tertiary — Terra Orange
  tertiary: "#4f2700"
  tertiary-container: "#703a00"
  on-tertiary: "#ffffff"
  on-tertiary-container: "#ffa14e"
  tertiary-fixed: "#ffdcc3"
  tertiary-fixed-dim: "#ffb77d"

  # Backgrounds & surfaces
  background: "#faf9f7"
  on-background: "#1a1c1b"
  surface: "#faf9f7"
  on-surface: "#1a1c1b"
  on-surface-variant: "#434842"
  surface-container: "#efeeec"
  surface-container-low: "#f4f3f1"
  surface-container-high: "#e9e8e6"
  surface-container-highest: "#e3e2e0"
  surface-container-lowest: "#ffffff"
  surface-dim: "#dadad8"
  surface-bright: "#faf9f7"
  surface-variant: "#e3e2e0"
  surface-tint: "#50634e"

  # Dark mode surfaces
  inverse-surface: "#2f3130"
  inverse-on-surface: "#f1f1ef"
  inverse-primary: "#b7cdb2"

  # Viewer dark environment (preserved from original)
  viewer-background: "#1a1a2e"
  on-viewer-background: "#8888aa"
  viewer-divider: "#333333"

  # Borders & outlines
  outline: "#747871"
  outline-variant: "#c3c8bf"
  outline-dashed: "#747871"

  # Semantic
  error: "#ba1a1a"
  error-container: "#ffdad6"
  on-error: "#ffffff"
  on-error-container: "#93000a"
  meta: "#747871"

  # Accent (compass north indicator — preserved)
  accent-danger: "#e53935"
  accent-danger-dark: "#b71c1c"

typography:
  display-lg:
    fontFamily: "Inter"
    fontSize: 48px
    fontWeight: "700"
    lineHeight: 56px
    letterSpacing: "-0.02em"
  headline-lg:
    fontFamily: "Inter"
    fontSize: 32px
    fontWeight: "600"
    lineHeight: 40px
    letterSpacing: "-0.01em"
  headline-lg-mobile:
    fontFamily: "Inter"
    fontSize: 28px
    fontWeight: "600"
    lineHeight: 36px
  headline-md:
    fontFamily: "Inter"
    fontSize: 24px
    fontWeight: "600"
    lineHeight: 32px
  body-lg:
    fontFamily: "Inter"
    fontSize: 18px
    fontWeight: "400"
    lineHeight: 28px
  body-md:
    fontFamily: "Inter"
    fontSize: 16px
    fontWeight: "400"
    lineHeight: 24px
  label-md:
    fontFamily: "Inter"
    fontSize: 14px
    fontWeight: "500"
    lineHeight: 20px
    letterSpacing: "0.02em"
  label-sm:
    fontFamily: "Inter"
    fontSize: 12px
    fontWeight: "600"
    lineHeight: 16px

rounded:
  sm: 4px
  DEFAULT: 4px
  lg: 8px
  xl: 12px
  full: 9999px

spacing:
  unit: 4px
  xs: 4px
  sm: 8px
  md: 16px
  lg: 24px
  xl: 40px
  gutter: 20px
  content-max-width: 1100px
  sidebar-width: 320px
  card-min-width: 280px
  card-gap: 20px
  header-v: 32px
  header-h: 24px
  upload-v: 40px
  upload-h: 20px
  card-info-padding: "14px 16px"
  controls-padding: "10px 14px"
  calibration-width: 260px

elevation:
  card: "0 2px 8px rgba(0, 0, 0, 0.10)"
  card-hover: "0 4px 16px rgba(0, 0, 0, 0.15)"
  panel: "0 2px 8px rgba(0, 0, 0, 0.30)"
  button: "0 2px 6px rgba(0, 0, 0, 0.30)"
  compass: "drop-shadow(0 1px 3px rgba(0, 0, 0, 0.50))"

motion:
  duration-fast: 150ms
  duration-base: 200ms
  easing-default: ease
  easing-smooth: ease-out
  spinner-duration: 800ms
  error-dismiss-delay: 8000ms

components:
  upload-zone:
    backgroundColor: "{colors.surface}"
    borderColor: "{colors.outline-dashed}"
    borderStyle: dashed
    borderWidth: 3px
    rounded: "{rounded.lg}"
    padding: "{spacing.upload-v} {spacing.upload-h}"
  upload-zone-active:
    backgroundColor: "{colors.primary-container}"
    borderColor: "{colors.primary}"
  map-card:
    backgroundColor: "{colors.surface}"
    rounded: "{rounded.lg}"
    boxShadow: "{elevation.card}"
  map-card-hover:
    transform: "translateY(-2px)"
    boxShadow: "{elevation.card-hover}"
  map-card-thumb:
    height: 180px
    objectFit: cover
  controls-bar:
    backgroundColor: "{colors.surface-overlay}"
    rounded: "{rounded.md}"
    boxShadow: "{elevation.panel}"
    padding: "{spacing.controls-padding}"
  viewer-divider:
    width: 6px
    backgroundColor: "{colors.viewer-divider}"
  btn-back:
    size: 40px
    backgroundColor: "{colors.surface-overlay}"
    rounded: "{rounded.md}"
    boxShadow: "{elevation.button}"
    fontSize: 20px
  btn-back-hover:
    backgroundColor: "{colors.outline-variant}"
  calibration-panel:
    backgroundColor: "{colors.surface-overlay}"
    rounded: "{rounded.md}"
    boxShadow: "{elevation.panel}"
    width: "{spacing.calibration-width}"
  btn-utility:
    backgroundColor: "{colors.surface-container}"
    borderColor: "{colors.outline}"
    rounded: "{rounded.sm}"
  btn-utility-hover:
    backgroundColor: "{colors.outline-variant}"
  btn-delete:
    backgroundColor: transparent
    textColor: "{colors.error}"
    rounded: "{rounded.sm}"
  btn-delete-hover:
    backgroundColor: "{colors.error-container}"
  error-toast:
    backgroundColor: "{colors.error-container}"
    textColor: "{colors.on-error-container}"
    rounded: "{rounded.md}"
  no-coverage-toast:
    backgroundColor: "rgba(0, 0, 0, 0.75)"
    textColor: "{colors.on-primary}"
    rounded: "{rounded.DEFAULT}"
  spinner:
    size: 24px
    trackColor: "{colors.outline-variant}"
    activeColor: "{colors.primary}"
    duration: "{motion.spinner-duration}"
---

## Brand & Style

OCAD Map Viewer is a professional cartographic tool for orienteers and map enthusiasts. It lets users upload geo-referenced OCAD PDF exports and explore them side-by-side with Google Street View. The design philosophy is **content-first and functional**: every interface element serves the map, never competes with it.

The visual language draws from **Material Design 3** principles with a **Terra & Forest** color palette — anchoring the brand with a sophisticated forest green (#243624) for primary actions, complemented by warm terra tones (browns and oranges) for secondary elements. This earthy aesthetic reflects the outdoor, cartographic nature of the domain while maintaining professional clarity.

The emotional tone is **precise and efficient**: a tool used in the field demands clarity at a glance and minimum friction.

### Design Evolution

This design system was generated by **Google Stitch** and represents a coordinated visual refresh from the original Google Blue system. Key changes:
- **Color vocabulary:** Shifted from Google Blue (#1a73e8) to a Forest Green primary (#243624) with complementary Terra Brown (#775843) and Terra Orange (#4f2700) accents
- **Typography:** Updated to use **Inter** font family (Google's design system standard) with Material Design 3 scales (display-lg, headline-lg, etc.)
- **Component refinement:** Tailored spacing, border-radius, and elevation for optimal readability on both light backgrounds (home) and the split-view cartographic context

Two distinct visual environments coexist:
- **Home (light):** A warm beige scaffold (#faf9f7) with white card surfaces creates a welcoming, organised dashboard. The forest green accent draws users to upload and action buttons.
- **Viewer (dark/split):** A full-viewport split layout places the OCAD overlay on a satellite map alongside Street View. The dark navy placeholder (#1a1a2e) is preserved from the original design, framing the panorama and reducing eye fatigue during extended navigation sessions.

---

## Colors

The palette anchors on **Forest Green** (#243624), a sophisticated primary colour that conveys trust and natural expertise. It is complemented by warm **Terra Brown** (#775843) for secondary elements and **Terra Orange** (#4f2700) for tertiary accents, creating an earthy, cohesive visual identity aligned with cartographic traditions.

**Primary Palette:**
- **Primary (#243624):** Forest green. Used for primary actions, active states, focus indicators, and interactive highlights. Commands attention in the upload zone and call-to-action buttons.
- **Primary container (#3a4d39):** A lighter forest shade for fill backgrounds where the primary tone needs less saturation.
- **On primary (#ffffff):** White text/icons on forest green surfaces for maximum contrast.
- **Primary fixed (#d2e9ce):** A light tint for hover states and elevated surfaces.

**Secondary Palette:**
- **Secondary (#775843):** Warm terra brown. Applied to secondary actions and informational states.
- **Secondary container (#ffd4bb):** A warm, desaturated tone for secondary background fills.
- **On secondary (#ffffff):** White content on brown surfaces.

**Tertiary Palette:**
- **Tertiary (#4f2700):** Deep terra orange. Reserved for distinctive accents and highlights (compass, warnings).
- **Tertiary container (#703a00):** Darker orange for emphasis.
- **On tertiary (#ffffff):** White text/icons on tertiary surfaces.

**Neutral Palette:**
- **Background (#faf9f7):** Home page scaffold. A warm, near-white beige that feels natural and inviting.
- **Surface (#faf9f7):** Card and panel backgrounds. Matches background for a clean, unified canvas.
- **Surface variant (#e3e2e0):** Subtle differentiation for secondary surfaces and dividers.
- **Surface container (#efeeec):** Mid-tone gray for container boundaries and subtle layering.
- **On background (#1a1c1b):** Dark charcoal for primary text and UI elements.
- **On surface variant (#434842):** Muted gray for secondary text and metadata.

**Error & Semantic:**
- **Error (#ba1a1a):** A warm red for destructive actions and error states (delete button, validation messages).
- **Error container (#ffdad6):** Soft pink for error backgrounds and highlights.
- **Meta (#747871):** Muted gray for secondary labels, scale indicators, and metadata.

**Dark Mode & Viewer:**
- **Inverse surface (#2f3130):** Dark background for inverted (night mode) contexts.
- **Inverse primary (#b7cdb2):** Light green for accents on dark surfaces.
- **Viewer background (#1a1a2e):** Preserved from the original design. Deep navy for the Street View placeholder, reducing eye fatigue during extended panorama navigation.

Text hierarchy follows the new palette: #243624 (forest green) for primary interactive elements, #1a1c1b for primary content, #434842 for secondary text, and #747871 for metadata.

---

## Typography

The design system uses **Inter** (Google's open-source design system font), a humanist sans-serif optimised for screen legibility. Inter ensures consistency with Material Design 3 standards and provides crisp rendering at all sizes. This replaces the previous native system font stack, enabling precise typographic control while maintaining cross-platform consistency.

**Type Scale (Material Design 3):**
- **Display-lg (48 px / 700 / -0.02em):** Maximum emphasis. Used for page titles and primary headings only (e.g., "OCAD Map Viewer" on the home page). Letter spacing is tightened to -0.02em for authority.
- **Headline-lg (32 px / 600 / -0.01em):** Section headings with secondary emphasis. Slightly tighter letter spacing (-0.01em) for polish.
- **Headline-md (24 px / 600):** Subsection titles such as "Cartes disponibles". Provides clear visual breaks without excessive weight.
- **Body-lg (18 px / 400):** Large body text for prominent descriptions (e.g., upload zone prompt).
- **Body-md (16 px / 400):** Primary body text and UI control labels. The standard reading size.
- **Label-md (14 px / 500 / 0.02em):** UI control labels and buttons. Slightly heavier weight (500) and expanded letter spacing (0.02em) for clarity in compact spaces.
- **Label-sm (12 px / 600):** Map metadata (scale, dimensions), secondary labels, and small UI text. Bold weight for emphasis despite small size.

These scales follow Material Design 3 conventions, ensuring the interface feels modern while maintaining the precision required for cartographic work.

---

## Layout & Spacing

Spacing follows a **4 px base grid** (refined from the original 8 px system). All meaningful dimensions are multiples of 4, allowing for both generous whitespace and tight micro-interactions.

**Home Page:**
- Content is constrained to `1100 px` max-width and centred.
- The header uses `24px` horizontal padding and is `72px` tall (9 × 4 units), providing substantial visual weight without feeling bloated.
- The main content area uses `32px` (lg) vertical gutters between sections and `40px` (xl) for major breaks.
- The maps grid uses CSS `auto-fill` with a `280 px` column minimum and `20px` (gutter) gaps. This adapts fluidly from single-column mobile layouts to 4+ columns on wide screens.
- Cards are `8px` (lg) radius containers with `14px/16px` internal padding, maintaining generous breathing room around content.

**Viewer Page:**
- Full-viewport flex row; the map panel and Street View panel each take `flex: 1`.
- A `6 px` dark drag-handle divider separates the two panels and remains the single most prominent structural element in the viewer.
- Floating controls (back button, control bar, compass, calibration panel) are anchored to the map panel's top-left corner with consistent `10 px` inset margins (2.5 × 4 px).
- The calibration panel is `260 px` wide, sized for dual-range input at comfortable touch targets.

---

## Elevation & Depth

Depth is communicated through **shadows, not borders** on the home page, and through **opacity and dark backgrounds** in the viewer.

- **Level 0 — Scaffold:** `#f0f2f5` background with no shadow.
- **Level 1 — Cards:** White surface + `0 2px 8px rgba(0,0,0,0.10)`. Subtle ambient lift.
- **Level 1 hover — Cards:** `0 4px 16px rgba(0,0,0,0.15)` + `translateY(-2px)`. A 2 px upward nudge and a slightly deeper shadow give haptic-like feedback.
- **Level 2 — Floating panels:** `0 2px 8px rgba(0,0,0,0.30)`. Applied to the controls bar, back button, calibration panel, and lock-rotation button. Higher opacity shadow places these firmly above the Google Maps canvas.
- **Compass drop-shadow:** `drop-shadow(0 1px 3px rgba(0,0,0,0.50))` ensures the SVG needle is legible over any map tile colour.

The upload zone uses no shadow; its dashed border is the sole affordance, keeping the visual weight low until activated.

---

## Shapes

The border-radius vocabulary is minimal and purposeful:

- **`12 px` (rounded-lg):** Primary containers — upload zone and map cards. The dominant shape of the home page.
- **`8 px` (rounded-md):** All floating viewer panels (controls bar, back button, calibration panel). A slightly tighter radius befitting the denser, professional viewer chrome.
- **`6 px` (DEFAULT):** Toast messages and the Street View "no coverage" indicator.
- **`4 px` (rounded-sm):** Small utility buttons (Calibrer, Reset, Fermer, lock-rotation). A nearly-rectangular shape keeps them understated.
- **`9999 px` (full):** The upload spinner ring.

The compass uses a frameless SVG polygon — sharp, unrounded arrowheads — to match the precision aesthetic of cartographic instruments.

---

## Components

### Upload Zone
A dashed 3 px border on a white card provides an immediately recognisable drop target. On hover or `dragover`, the border shifts to `#1a73e8` and the background fills with `#e8f0fe`, signalling readiness through colour alone — no textual prompt changes are needed. While processing, the border and background revert and a centred spinner replaces the icon.

### Map Grid & Cards
Cards use a thumbnail-first layout: a 180 px tall cover image fills the top, followed by the map title and scale metadata, then a right-aligned delete button. Cards are navigable as `<a>` blocks; the delete button uses `stopPropagation` so it does not trigger navigation. Hover feedback is purely elevation-based — no background colour change — keeping the tile grid visually quiet.

### Viewer Controls Bar
A frosted-glass pill (`rgba(255,255,255,0.95)`) floating 10 px from the top edge of the map panel. It contains an opacity range slider (accented in `#1a73e8`), a visibility toggle checkbox, and the Calibrer utility button, all in a flex row with `12 px` gaps. Body-sm (13 px) type keeps it compact.

### Compass & Rotation Lock
A 48 × 48 px SVG compass sits at `top: 60px, left: 10px` — directly below the back button — and rotates in real time with the Street View heading. The north needle is `#e53935` (red) and the south needle is `#cccccc` (light gray), following universal cartographic convention. A `🔒` icon button beside it toggles rotation lock.

### Calibration Panel
A `260 px` wide floating panel with two range sliders (latitude and longitude offset in metres) and Reset/Close buttons. Uses `font-family: monospace` for the numeric readouts so values align vertically during adjustment.

### Street View Placeholder
The right panel renders a deep navy (`#1a1a2e`) fill with muted gray-blue (`#8888aa`) instructional text centred in the void. This ensures the viewer is never perceived as "broken" before a Street View location is selected.

### Toast Messages
Two toast types exist: the upload error (red-on-pink, `8 px` radius, auto-dismisses after 8 s) and the "no Street View coverage" notification (white-on-dark-translucent, `6 px` radius, bottom-centred over the map panel). Both are `pointer-events: none` so they never block interaction.

---

## Design System Implementation: Google Stitch Generated Screens

The following four screens were generated by **Google Stitch** using this design system (Terra & Forest variant). They serve as reference implementations and responsive design guides:

### 1. Home Screen — `home-optimized.html`
**Purpose:** Primary landing page and map browser interface
**Key Elements:**
- Full-width header (72 px height) with title and action buttons
- "Cartes disponibles" (Available Maps) section heading
- Primary action button ("Ajouter" — Add) in forest green (#243624)
- Responsive grid layout of map cards (4 columns on desktop, adapts down)
- Each card includes a 180 px thumbnail, title, scale metadata, and delete button
- Sidebar width: 320 px (when applicable)

**Technical Stack:**
- Tailwind CSS (CDN)
- Material Symbols Outlined icons
- Inter font family for all text
- Responsive: `grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4`

### 2. Viewer — Split View Screen — `viewer-split.html`
**Purpose:** Dual-panel cartographic viewer combining OCAD and Street View
**Key Elements:**
- Full-viewport flex layout with 50/50 split panels
- Left panel: OCAD map overlay with opacity control bar (forest green accent slider)
- Right panel: Street View panorama or placeholder (dark navy #1a1a2e)
- Floating controls:
  - Back button (top-left, 40 px square, forest green text on white with shadow)
  - Opacity control bar (24 px pill with slider, label-md typography)
  - Compass indicator (48 × 48 px SVG, rotates with heading)
  - Calibration panel (260 px width, anchored to top-left, monospace numerics)
- 6 px drag-handle divider between panels (#333333)

**Interactive States:**
- Hover effect on back button: outline-variant background (#c3c8bf)
- Opacity slider accent: primary forest green (#243624)
- Calibration inputs: range sliders with precise touch targets
- Lock-rotation button: toggle state for compass rotation

### 3. File Selection — `file-selection.html`
**Purpose:** Modal interface for uploading and selecting OCAD PDF files
**Key Elements:**
- Centered modal with white background (surface #faf9f7)
- Title: "Importer une nouvelle carte" (headline-md, 24 px)
- Description text (body-lg, 18 px)
- Upload area:
  - Large drop zone with dashed border (#747871, 3 px width)
  - Centered upload icon (material symbols)
  - "Glissez et déposez votre fichier PDF ici" (Drag and drop text)
  - "ou" (or) divider
  - "Parcourir les fichiers" (Browse files) button
- Selected file preview area with filename, size, and delete option
- Action buttons: "Annuler" (Cancel), "Lancer l'importation" (Start Import)

**Upload Zone States:**
- Default: dashed border (#747871), surface background
- Hover/Dragover: primary green border (#243624), primary-container fill (#3a4d39)
- Processing: spinner animation (800 ms duration)

### 4. Import Modal — `import-modal.html`
**Purpose:** Real-time progress tracking during PDF file processing
**Key Elements:**
- Modal header with filename (body-md typography)
- Progress indicator bar (primary green, 45% filled example)
- Task checklist with status icons:
  - ✓ "Lecture de la structure du fichier" (File structure reading — completed)
  - ⟳ "Analyse de la géométrie PDF" (PDF geometry analysis — in progress)
  - ○ "Génération des tuiles vectorielles" (Vector tile generation — pending)
  - ○ "Construction de l'index" (Index building — pending)
- Cancel button (label-md, rounded-sm, error color for destructive action)

**Visual Feedback:**
- Completed tasks: checkmark icon, secondary text color
- In-progress task: spinner animation, primary text
- Pending tasks: outline circle, meta text color (gray #747871)
- Progress bar background: surface-container (#efeeec)
- Progress bar fill: primary (#243624)

---

## Design Token Reference

All design tokens are defined in the YAML frontmatter and can be referenced in components as follows:

- **Colors:** `{colors.primary}`, `{colors.secondary}`, `{colors.error}`, etc.
- **Typography:** Font families are "Inter"; use className patterns like `font-headline-md`, `text-headline-md`
- **Spacing:** `{spacing.xs}` (4px), `{spacing.sm}` (8px), `{spacing.md}` (16px), `{spacing.lg}` (24px), `{spacing.xl}` (40px)
- **Rounded:** `{rounded.sm}` (4px), `{rounded.DEFAULT}` (4px), `{rounded.lg}` (8px), `{rounded.full}` (9999px)
- **Elevation:** `{elevation.card}` (subtle), `{elevation.panel}` (floating), `{elevation.button}` (interactive)
- **Motion:** `{motion.duration-fast}` (150ms), `{motion.duration-base}` (200ms)

---

**Last Updated:** 12 mai 2026  
**Design System:** Terra & Forest (Google Stitch v1)  
**Font Family:** Inter  
**Color Mode:** Light (primary), Dark (optional, inverted surfaces available)
