# Wise — Marketing Site Design Spec

> Source: wise.com (live DOM, computed styles), 2026-07-18 (desktop 1512px). Semantic tokens read from the site's own `:root` custom properties; brand system published at wise.design.
> Coverage: home / marketing. Dashboard: pending login (`needs-dashboard`). Responsive: breakpoints from tokens only; mobile rendering not verified.

## 1. Visual Theme & Atmosphere

Loud, confident consumer-fintech brand: a clean white canvas dominated by an enormous UPPERCASE Wise Sans headline set at the heaviest weight (900) with *negative leading* — the line-height is smaller than the font size, so the lines stack into a solid typographic block. The signature colour pairing is a bright acid green `#9fe870` against deep forest green `#163300`: the green fills every fully-rounded pill button while the forest green does the talking as ink. Body and UI drop to Inter at normal weights, which keeps the shouting reserved for the display. The hero's other half is the live currency-converter card — a large rounded white panel with big tabular numerals, flag-avatar currency pills and a full-width green CTA. Feels: bold, direct, optimistic, trustworthy.

## 2. Color Palette & Roles

### Surfaces
| Token | Hex | Role / usage |
|---|---|---|
| canvas | `#ffffff` | Page background, converter card |
| surface | `#e8ebe6` | Light gray-green section fills |
| mint | `#e2f6d5` | Pale green info banners / highlights |
| ice | `#ecf9f9` | Pale blue-green info row |
| forest | `#163300` | Deep green blocks, primary ink |

### Text
| Token | Hex | Role / usage |
|---|---|---|
| ink | `#0e0f0c` | Display headline, near-black |
| forest | `#163300` | Primary text, button label on green |
| gray | `#454745` | Body / secondary text |
| gray-2 | `#6a6c6a` | Tertiary text |
| faint | `#868685` | Captions, placeholder |

### Accent
| Token | Hex | Role / usage |
|---|---|---|
| bright-green | `#9fe870` | Signature accent — every pill button |
| forest-alt | `#1a3300` | Deep green variant |

### Semantic (from `:root` custom properties)
| Token | Hex | Role / usage |
|---|---|---|
| content-accent | `#0097c7` | Accent text (blue) |
| interactive-accent | `#00a2dd` | Interactive blue |
| positive | `#008026` / `#2ead4b` | Success |
| negative | `#cf2929` | Error |
| warning | `#9a6500` | Warning |

Product token set also exposes `--color-content-primary #37517e`, `--color-content-secondary #5d7079`, `--color-content-tertiary #768e9c` (legacy navy scale used in product UI).

Rule: the bright green `#9fe870` is reserved for pill buttons and highlights; forest green `#163300` carries text and dark blocks. Never use the green as body text or the blue semantic accent as a brand colour.

## 3. Typography Rules

- Display: `"Wise Sans", sans-serif` — UPPERCASE, weight 900, with negative leading.
- UI / Body: `Inter, Helvetica, Arial, sans-serif` — the workhorse for everything else.

| Role | Size/Line | Weight | Tracking |
|---|---|---|---|
| Display (h1) | 90 / 76.6 | 900 | normal, UPPERCASE |
| Section (h2) | 59 / 50.4 | 900 | normal |
| Sub-display | 40 / 34 | 900 | normal |
| Body / lead | 16–18 / 1.5 | 400 | normal, Inter |
| Button | 16 | 600 | normal, Inter |

Patterns: the display is the whole identity — huge, uppercase, weight 900, and set with line-height *below* font-size so lines lock together. Everything else is calm Inter at 400–600. Never set body copy in Wise Sans 900.

## 4. Component Stylings

### Buttons (fully-rounded pills)
- **Primary (green)**: bg `#9fe870`, text `#163300`, radius `9999px`, h `40–48px`, Inter 600 16px. Hover: slightly deeper green.
- **Nav Sign up**: same green pill, h `32px`, 600.
- **Text / nav link**: forest `#163300`, no fill; inline links are underlined.
- Converter CTA is a full-width green pill.

### Nav
- On white: Wise arrow-mark logo + wordmark left, links (Personal / Business / Platform), right locale selector with flag, Help, Log in, green Sign-up pill.

### Cards / panels — signature converter
- Large rounded white card (radius ~`24px`) with a soft shadow, holding: labelled amount rows with very large tabular numerals, currency pills carrying circular flag avatars and a chevron, a pale mint `#e2f6d5` info banner, icon+label info rows (arrival time, fees) separated by hairlines, and a full-width green pill CTA.

### Inputs
- Amount fields render as large borderless numerals inside the card; currency selector is a pill. Focus ring not captured on marketing.

## 5. Layout Principles

- Two-column hero: left the huge display block + lead + green CTA, right the converter card.
- Max content ~1240px, generous margins.
- Rating badges (App Store / Google Play with stars) sit above the headline.
- Sections alternate white with `#e8ebe6` gray-green and forest-green blocks.

## 6. Depth & Elevation

- Radii: pill `9999px` (buttons, currency chips), card ~`24px`, small chips ~`19px`.
- Elevation: the converter card floats on a soft neutral shadow; most other surfaces are flat, separated by colour blocks and hairlines.

## 7. Motion

- Restrained: hover colour shifts on pills and links; the converter updates values live. No heavy scroll library confirmed. Ease default cubic-bezier.

## 8. Backgrounds, Effects & WebGL

- Flat solid surfaces — white, gray-green `#e8ebe6`, mint `#e2f6d5` and forest `#163300` blocks. No gradients, grain or WebGL captured.
- Colour and flag imagery inside the converter card provide the only visual texture.

## 9. Do's and Don'ts

### Do
- Set the hero in Wise Sans, UPPERCASE, weight 900, with line-height below font-size so lines lock together.
- Keep the canvas white with forest-green `#163300` ink.
- Put the bright green `#9fe870` on every pill button, with forest-green label text.
- Use Inter at 400–600 for all body, UI and button copy.
- Build the signature converter card: big tabular numerals, flag currency pills, mint info banner, full-width green CTA.
- Use fully-rounded pills for buttons and chips.

### Don't
- Don't set body copy in Wise Sans 900 — the heavy display is reserved for headlines.
- Don't use the bright green as text colour or the semantic blue as a brand accent.
- Don't square off buttons; they are full pills.
- Don't add gradients or decorative effects — surfaces are flat colour blocks.
- Don't give the display normal/positive leading; the tight negative leading is the signature.

## 10. Responsive Behavior

- Two-column hero collapses to stacked on mobile (markup present); display scales fluidly. Mobile rendering not verified — captured at desktop 1512px only.

## 11. Agent Prompt Guide

Quick token reference:

- Canvas `#ffffff` · Ink `#163300` / `#0e0f0c` · Body gray `#454745` · Surface `#e8ebe6` · Mint `#e2f6d5` · Accent bright-green `#9fe870`
- Fonts: Wise Sans 900 UPPERCASE (display, negative leading) / Inter 400–600 (UI+body) · Body 16px · Radius full-pill button / 24px card · Button height 40–48px
- Focus: not captured · Hover: slightly deeper green on pills

<wise_design_language>
Use the Wise design language: a bold consumer-fintech brand on a white canvas whose entire identity is an enormous UPPERCASE Wise Sans headline at weight 900 set with NEGATIVE leading (90px type on 76.6px line-height) so the lines stack into a solid typographic block, in near-black-green ink #0e0f0c over forest green #163300. The signature pairing is bright acid green #9fe870 against that forest green: the green fills every fully-rounded pill button (9999px, 40-48px tall) with forest-green label text, and never appears as body copy. All body, UI and button text drops to calm Inter at 400-600, keeping the shouting to the display. Surfaces are flat colour blocks — white, gray-green #e8ebe6, pale mint #e2f6d5 banners and deep forest blocks — with no gradients or effects; the one floating element is the signature currency-converter card (radius ~24px, soft shadow) holding very large tabular numerals, flag-avatar currency pills, a mint info banner, hairline-separated icon info rows and a full-width green pill CTA. Semantic colours are blue #00a2dd, positive #008026, negative #cf2929, warning #9a6500. Feels: bold, direct, optimistic, trustworthy. Avoid: setting body copy in Wise Sans 900, using the bright green as text, squared-off buttons, gradients, or giving the display normal positive leading.
</wise_design_language>
