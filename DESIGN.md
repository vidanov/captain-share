---
name: Captain Share
description: A team library of agent assets with one action per row, and a patch bay behind it for where each one is installed.
colors:
  panel: "#131518"
  panel-2: "#1a1d21"
  panel-3: "#22262b"
  rule: "#2a2e34"
  rule-strong: "#3b4149"
  jack-ring: "#646b75"
  ink: "#ece9e3"
  ink-2: "#aeb2b9"
  ink-3: "#8b919a"
  amber: "#f2a33a"
  amber-hover: "#f6b55c"
  amber-ink: "#1c1306"
  mint: "#43c08f"
  mint-dim: "#2a6e57"
  mint-ring: "#358a6c"
  red: "#f06464"
  red-ink: "#ffd5d5"
typography:
  display:
    fontFamily: "-apple-system, BlinkMacSystemFont, Segoe UI, system-ui, Roboto, Helvetica Neue, sans-serif"
    fontSize: "2rem"
    fontWeight: 650
    lineHeight: 1.5
    letterSpacing: "-0.01em"
  headline:
    fontFamily: "-apple-system, BlinkMacSystemFont, Segoe UI, system-ui, Roboto, Helvetica Neue, sans-serif"
    fontSize: "1.5rem"
    fontWeight: 650
    lineHeight: 1.5
    letterSpacing: "-0.01em"
  title:
    fontFamily: "-apple-system, BlinkMacSystemFont, Segoe UI, system-ui, Roboto, Helvetica Neue, sans-serif"
    fontSize: "1.125rem"
    fontWeight: 650
    lineHeight: 1.5
    letterSpacing: "-0.01em"
  subtitle:
    fontFamily: "-apple-system, BlinkMacSystemFont, Segoe UI, system-ui, Roboto, Helvetica Neue, sans-serif"
    fontSize: "1rem"
    fontWeight: 650
    lineHeight: 1.5
    letterSpacing: "-0.01em"
  wordmark:
    fontFamily: "-apple-system, BlinkMacSystemFont, Segoe UI, system-ui, Roboto, Helvetica Neue, sans-serif"
    fontSize: "15px"
    fontWeight: 700
    lineHeight: 1.5
    letterSpacing: "-0.01em"
  row-title:
    fontFamily: "-apple-system, BlinkMacSystemFont, Segoe UI, system-ui, Roboto, Helvetica Neue, sans-serif"
    fontSize: "13.5px"
    fontWeight: 620
    lineHeight: 1.5
  body:
    fontFamily: "-apple-system, BlinkMacSystemFont, Segoe UI, system-ui, Roboto, Helvetica Neue, sans-serif"
    fontSize: "14px"
    fontWeight: 400
    lineHeight: 1.5
  hint:
    fontFamily: "-apple-system, BlinkMacSystemFont, Segoe UI, system-ui, Roboto, Helvetica Neue, sans-serif"
    fontSize: "12.5px"
    fontWeight: 400
    lineHeight: 1.5
  control:
    fontFamily: "-apple-system, BlinkMacSystemFont, Segoe UI, system-ui, Roboto, Helvetica Neue, sans-serif"
    fontSize: "13px"
    fontWeight: 620
    lineHeight: 1.5
  label:
    fontFamily: "-apple-system, BlinkMacSystemFont, Segoe UI, system-ui, Roboto, Helvetica Neue, sans-serif"
    fontSize: "11px"
    fontWeight: 650
    lineHeight: 1.5
    letterSpacing: "0.08em"
  mono:
    fontFamily: "ui-monospace, SF Mono, Menlo, Consolas, monospace"
    fontSize: "12px"
    fontWeight: 400
    lineHeight: 1.5
rounded:
  chip: "4px"
  control: "6px"
  panel: "10px"
  inset: "8px"
  dialog: "12px"
  pill: "999px"
  jack: "50%"
spacing:
  xs: "4px"
  sm: "10px"
  md: "16px"
  lg: "24px"
  xl: "28px"
components:
  button-primary:
    backgroundColor: "{colors.amber}"
    textColor: "{colors.amber-ink}"
    typography: "{typography.control}"
    rounded: "{rounded.control}"
    padding: "8px 14px"
  button-primary-hover:
    backgroundColor: "{colors.amber-hover}"
    textColor: "{colors.amber-ink}"
  button-quiet:
    backgroundColor: "transparent"
    textColor: "{colors.ink}"
    typography: "{typography.control}"
    rounded: "{rounded.control}"
    padding: "8px 14px"
  button-quiet-hover:
    backgroundColor: "{colors.panel-3}"
    textColor: "{colors.ink}"
  button-small:
    rounded: "{rounded.control}"
    padding: "4px 9px"
  input:
    backgroundColor: "{colors.panel-3}"
    textColor: "{colors.ink}"
    rounded: "{rounded.control}"
    padding: "8px 10px"
  agent-summary:
    backgroundColor: "transparent"
    textColor: "{colors.ink-2}"
    rounded: "{rounded.chip}"
    padding: "1px 6px"
  agent-summary-open:
    backgroundColor: "{colors.panel-3}"
    textColor: "{colors.ink}"
  agent-chip:
    backgroundColor: "transparent"
    textColor: "{colors.ink-3}"
    rounded: "{rounded.chip}"
    padding: "1px 2px"
  agent-chip-on:
    textColor: "{colors.ink}"
  button-link:
    backgroundColor: "transparent"
    textColor: "{colors.ink-3}"
    padding: "0"
  button-link-hover:
    textColor: "{colors.ink}"
  agent-pick:
    backgroundColor: "transparent"
    textColor: "{colors.ink-2}"
    rounded: "{rounded.pill}"
    padding: "7px 14px"
  agent-pick-on:
    textColor: "{colors.ink}"
  status-strip:
    backgroundColor: "{colors.panel-2}"
    textColor: "{colors.ink}"
    rounded: "{rounded.panel}"
    padding: "12px 16px"
  library-row:
    backgroundColor: "{colors.panel-2}"
    padding: "13px 16px"
  share-panel:
    backgroundColor: "{colors.panel}"
    rounded: "{rounded.inset}"
    padding: "14px 16px"
  copy-line:
    backgroundColor: "{colors.panel-2}"
    rounded: "{rounded.inset}"
    padding: "10px 12px"
  where-option:
    backgroundColor: "transparent"
    textColor: "{colors.ink-2}"
    rounded: "{rounded.inset}"
    padding: "10px 12px"
  jack:
    backgroundColor: "{colors.panel}"
    rounded: "{rounded.jack}"
    size: "22px"
  jack-on:
    backgroundColor: "{colors.mint}"
    rounded: "{rounded.jack}"
    size: "22px"
  jack-inherited:
    backgroundColor: "{colors.mint-dim}"
    rounded: "{rounded.jack}"
    size: "22px"
  faceplate:
    backgroundColor: "{colors.panel-2}"
    height: "3.75rem"
    padding: "0 24px"
  panel:
    backgroundColor: "{colors.panel-2}"
    rounded: "{rounded.panel}"
  dock:
    padding: "0 24px 16px"
  routing-bar:
    backgroundColor: "{colors.panel-3}"
    textColor: "{colors.ink}"
    rounded: "{rounded.panel}"
    padding: "10px 12px 10px 18px"
  review-sheet:
    backgroundColor: "{colors.panel-2}"
    width: "34rem"
    padding: "22px 24px"
---

# Design System: Captain Share

## Overview

**Creative North Star: "The Patch Bay"**

Behind its front panel, the window is a rack-mounted patch bay. Day to day, a teammate sees only the front: a calm list of what the team library offers, what is new, and what they already have, with one action on each row. The bay itself sits under "Where installed": library assets are rows, places (this project, the folders above it, your user) are columns, and a round jack at each crossing shows where an asset is live. The panel is anodized near-black in three stepped tones, labels are engraved in small tracked caps, and the only light in the room comes from lamps: mint for what is installed, amber for what you are about to change, red for what is blocked. A thin amber cable runs from a folder's jack to the project jack it feeds, so inheritance is drawn rather than explained.

The metaphor lives in the visuals and in the code, not in the words. The interface says "Assets", "click a circle", "Add a library" and "Review and apply"; it never asks a teammate to know what a jack, patch or bay is. Density is that of a working tool: 14px body, hairline-ruled rows, a centered 52rem library column, and on the grid a matrix sized to its columns with one quiet legend line under it. Places, agents and layers never reach the everyday screens, and anything that is a setting rather than a state stays folded until asked for.

The system is dark-only (`color-scheme: dark`), uses one sans for everything a person reads and monospace only for paths and commands, ships no images or icon fonts, and runs under a self-only content policy with no external fonts or scripts.

**Key Characteristics:**
- Stepped panel tones and 1px hairlines carry all structure; one modal casts the only shadow.
- Color is state: mint is installed or up to date, amber is new, pending or your next step, red is blocked.
- Everyday screens (Library, Share, first setup) are one column of rows with one action each; the grid is a maintenance view.
- The jack (22px circle) is the signature control, with seven legible states and a one-line legend under the grid.
- Engraved small caps label the panel; they never act as headings or kickers.
- One authored motion: a new cable draws out of the folder jack in 200ms.

## Colors

A warm off-white ink on a cool, nearly neutral anodized black, lit by two lamp colors and one fault color.

### Primary
- **Patch Amber** (`amber`): Everything the person is doing, about to do, or should look at. Primary buttons, the active tab and sub-tab underline, the status-strip lamp when something waits, "New" and "Updated" row badges, the border of a chosen where-option, the focus ring, caret, text selection tint, pending jacks, the routing bar's border, cables, and the "Archive" operation in the review list.
- **Lit Amber** (`amber-hover`): Hover state of primary buttons only.
- **Amber Ink** (`amber-ink`): Text on any amber fill (8.8:1).

### Secondary
- **Live Mint** (`mint`): Installed, linked, up to date. Filled jacks, the lamp in a "have" pill and on a linked agent chip or agent pick, the status-strip lamp when a library is up to date, "Link" operations in the review list, the badge on a folder that is already a library. A 4% mint wash marks the "Your user" column.
- **Dim Mint** (`mint-dim`): The same state seen secondhand. Fill only: inherited jacks and installed-but-unsynced jacks, plus the border of the badge on a folder that is already a library.
- **Mint Ring** (`mint-ring`): The ring of every secondhand-mint jack (the solid ring of an inherited jack and the dashed ring of an asset installed for your user), and the border of a chosen agent pick (4.03:1 on the lifted panel).

### Tertiary
- **Fault Red** (`red`): Blocked. The ring and slash of a jack where a local file is in the way, and the border of an error message.
- **Fault Ink** (`red-ink`): The one error text color: error messages, conflict messages, warnings and folder errors, readable on every panel tone.

### Neutral
- **Anodized Panel** (`panel`): Page background, the recessed library group rows inside the matrix, the recessed share form inside a library row, and the empty jack face.
- **Lifted Panel** (`panel-2`): Faceplate, matrix and list panels, review sheet, folder dialog.
- **Raised Control** (`panel-3`): Inputs, the routing bar, status messages, the open agent summary, and hover fill for quiet buttons and agent chips.
- **Hairline** (`rule`): Row and column dividers, panel borders.
- **Strong Hairline** (`rule-strong`): Control borders, the matrix head's bottom rule, the nav divider, scrollbar thumbs.
- **Jack Ring** (`jack-ring`): The ring of a free jack, of an unchosen agent-pick lamp and of the setup step numbers, the one hairline-grey edge that must read as a control (3.14:1 on the lifted panel, 3.4:1 on the page).
- **Engraved Ink** (`ink`): Primary text and headings.
- **Secondary Ink** (`ink-2`): Paragraphs, engraved labels, inactive tabs (7.95:1 on the lifted panel).
- **Muted Ink** (`ink-3`): Descriptions, hints, kind tags, counts, paths (5.33:1 on the lifted panel; keep it at or above 12px).

### Named Rules
**The Two Lamps Rule.** Mint says "this is installed" or "up to date"; amber says "this waits for you": something new, a pending change, the next step, or what you are touching. Neither appears as decoration, and mint never fills a button.

**The Token Rule.** Every color lives as a custom property on `:root`. A new surface extends that set; it does not introduce a raw hex.

**The Readable Ring Rule.** Every jack ring that identifies a state or a control holds at least 3:1 against the panel it sits on; fills may be dimmer, rings may not.

## Typography

**Body Font:** the platform UI sans (`-apple-system`, `Segoe UI`, `system-ui`, Roboto, `Helvetica Neue`)
**Label/Mono Font:** `ui-monospace` (`SF Mono`, Menlo, Consolas), for paths, commands and change lists only

**Character:** One workhorse sans at mid-heavy weights (620 and 650) does all the talking, so the panel reads like a labelled instrument rather than a document. Monospace appears only where the text is something a machine reads back.

### Hierarchy
- **Display** (`display`): The first-setup heading ("Set up Captain Share"), the only place the window raises its voice. The About page title is a one-off step below it at 1.75rem; it is not a ramp step for new surfaces.
- **Headline** (`headline`): The view title at the top of each tab.
- **Title** (`title`): Sheet and section headings, such as "Review changes". Small panel headings drop to 13px.
- **Subtitle** (`subtitle`): Headings of the ruled sections on the About page ("Start here", "How sharing works").
- **Wordmark** (`wordmark`): "Captain Share" in the faceplate, beside its amber lamp, on every view. The first-setup introduction paragraph is set at the same 15px as a one-off lede; it is not a separate role.
- **Row title** (`row-title`): Asset and library names in matrix and list rows. On the library home, names use the body size at the same weight (620).
- **Body** (`body`): Running text; paragraphs in Secondary Ink, capped near 44rem, descriptions capped at 62ch and clamped to one line (two at narrow width) until the row is opened.
- **Hint** (`hint`): Help lines, descriptions and the 12px legend line, in Muted Ink.
- **Control** (`control`): Buttons, inputs, selects and tabs; small buttons drop to 12px, and the agent summary and agent chips drop to 11px and 10.5px.
- **Label** (`label`): Engraved small caps for panel labels and the library home's list headings (in Muted Ink). Kind tags ("SKILL", "AGENT") and row badges ("NEW" in amber at weight 700, "EDITED HERE" in Secondary Ink) use the same treatment at 10.5px and 0.06em.
- **Mono** (`mono`): Paths in lists and library rows, the review sheet's change lines, preformatted blocks; 11px inside dense file lists.

### Named Rules
**The Engraving Rule.** Uppercase tracked caps label a part of the panel: a control, the matrix corner, a library group, a list on the library home, an asset's kind or a row badge. They are never placed above a heading to introduce it.

**The Machine Text Rule.** Monospace means a machine path or command. Prose, names and buttons stay in the sans.

**The Tabular Count Rule.** Every live count (asset totals, the routing summary, count chips) uses tabular numerals so it does not jitter as it changes.

## Layout

A sticky faceplate (3.75rem) spans the top: wordmark, then the section tabs pushed right, then About. Major tabs (Library, Share) and minor tabs (Where installed, Settings) are split by a short vertical hairline, minor ones in Muted Ink at 12.5px. The project selector appears in the faceplate only under Where installed; its full path is not printed but is the selector's tooltip. Content sits in a 24px gutter with 28px above.

Library and Share are a single centered column capped at 52rem (intro text at 40rem): the heading, status strips stacked 16px apart, the search field, then sections of an engraved list heading over one list panel. First setup is a centered 40rem column of numbered steps divided by hairlines. Where installed opens with a sub-tab row (Grid, Projects, Local files, Archive) over the view it selects.

The Grid view is one column: a tools row (filter field, a kind select that appears only when a library holds more than one kind, and a tabular count), the matrix, sized exactly to its columns (a 25rem asset column plus one 9rem column per place) so jacks stay near the names they belong to, and a single legend line 12px below it. The matrix head sticks under the faceplate. Library groups break the rows with a recessed band. Folders above the project beyond the nearest collapse behind a "+N folders above" button in the matrix corner.

A dock sticks 16px above the viewport bottom, in the page gutter, and stacks its contents 8px apart: the status message on top, the routing bar (at least 40rem wide) below it, so neither covers the other. The dock passes clicks through its empty space. The review opens as a 34rem sheet docked to the right edge below the faceplate, with its Apply and Cancel actions stuck to the sheet's bottom; while it is open the status message moves into the sheet under its hint.

The other secondary views are single columns: Settings caps at 48rem, About at 72rem with an auto-fill grid of 20rem minimum columns. Rhythm comes from 10px gaps between controls, 16px row insets, 24px gutters and 28px between major blocks.

At 900px and below, the faceplate wraps to two lines (6.5rem) with the tabs on their own horizontally scrolling row and the path hidden. The page gutter and the dock's inset drop to 16px. The legend and status strips wrap, and a library row stacks its action under its text, left-aligned. The matrix becomes its own two-axis scroll area, columns narrow to 9rem for assets and 6.5rem for places, and the asset column and head row stick so names and places stay visible while scrolling.

### Named Rules
**The Sized-to-Fit Rule.** The matrix is as wide as its columns and no wider. It is never stretched to fill the viewport.

**The Front Panel Rule.** Everyday screens (Library, Share, first setup) show no places, agents or layers. Each row carries one action: a Get button, or a "have" pill with a quiet Remove link. Places, agents and layers live under Where installed and Settings.

**The Folded Settings Rule.** The grid shows state; settings wait to be asked for. A place's agents read as one quiet line in its column head and open into toggles only for the column clicked; a filter that cannot narrow anything is not shown; extra folders, long descriptions and full paths stay folded behind a button, a click on the row, or a tooltip.

## Elevation & Depth

The system is flat. Depth comes from three panel tones (Anodized, Lifted, Raised) and from 1px hairlines in two strengths. The page is the darkest tone; the faceplate, panels and sheet are lifted one step; inputs, the routing bar and status messages are raised a second step. Group bands inside the matrix, and the share form that opens inside a library row, drop back to the page tone so they read as recessed. The inset ring on a lit jack and on the wordmark (a 3px ring of panel color inside the lamp) is a lamp bezel, not elevation.

### Shadow Vocabulary
- **Dialog lift** (`box-shadow: 0 12px 24px rgba(0, 0, 0, 0.5)`): The folder picker dialog only, over a 60% near-black backdrop.

### Named Rules
**The Flat Panel Rule.** Panels, bars and sheets are separated by tone and hairline, never by a drop shadow. Only a modal dialog casts one.

## Shapes

Corners are gently rounded and sized by role: 4px on agent chips and the agent summary, 6px on buttons, inputs, selects and status messages, 8px on boxes inset within a panel or sheet (the share form, the command line, where-options), 10px on panels, status strips and the routing bar, 12px on the dialog, full pills for agent picks, count chips and badges. Jacks, lamps (agent, "have", status strip), the wordmark and the setup step numbers are perfect circles; the circle is reserved for things that show state. Borders are always 1px, except the 2px ring of a jack and the 2px active-tab underline. A disabled slot (an asset that cannot go to that place) is a short 6x2px dash with fully rounded 2px ends instead of a circle; that capsule is a one-off, not a step in the radius scale.

## Components

### Buttons
Compact and tactile, with color reserved for the step that moves forward.
- **Shape:** gently rounded (`rounded.control`).
- **Primary:** Patch Amber fill with Amber Ink text, 8px by 14px padding, 13px at weight 620. Used for the forward step: "Review and apply", "Apply changes", "Clone and continue", "Get the starter set", "Share", and "Get" on a new or updated library item.
- **Hover / Focus:** hover lifts to Lit Amber; focus shows a 2px amber outline offset 2px. Background, border and text color ease over 160ms.
- **Quiet:** transparent with a Strong Hairline border and Engraved Ink text; hover fills Raised Control and brightens the border to Muted Ink. Used for "Discard", "Cancel", "I already have a checkout…", Pull, Push, and "Get" on items that are not new.
- **Link:** no box, Muted Ink at 12px and weight 500, underlined 3px below; hover brightens to Engraved Ink. Used for secondary row actions ("Remove", "Finish", "Show all"). "Copy get command" is a link that appears only on row hover or focus where the device can hover, and stays visible on touch.
- **Small:** 4px by 9px padding at 12px, for inline actions such as "+1 folder above".
- **Disabled:** 45% opacity, default cursor.

### Chips
- **Agent summary:** a borderless text button under each place's name listing its linked agents ("Kiro, Copilot", or "No agents"), Secondary Ink at 11px and weight 500, truncated with an ellipsis. Hover and open fill Raised Control and brighten to Engraved Ink. It exposes `aria-expanded` and toggles the agent chips for that column only.
- **Agent chip:** shown only under an open summary. A 4px-rounded button per agent (Kiro, Claude, Codex, Copilot) with a 6px lamp before the name. Unlinked: hollow lamp, Muted Ink text. Linked: mint lamp, Engraved Ink text, Strong Hairline border. At narrow width they stack in one column and lose their border.
- **Count chip and badge:** hairline pills in Secondary Ink at 11 to 11.5px for counts and facts.
- **Agent pick:** a pill toggle (7px by 14px) in first setup and Settings with an 8px lamp before the agent name. Off: Jack Ring lamp, Secondary Ink text, Strong Hairline border. On: mint lamp, Engraved Ink text, Mint Ring border.
- **Have pill:** borderless; a 12px mint lamp with a 2px panel bezel and a 12.5px weight-600 Secondary Ink label ("For you", "In backend").

### Cards / Containers
- **Corner Style:** `rounded.panel`.
- **Background:** Lifted Panel.
- **Shadow Strategy:** none (see Elevation & Depth).
- **Border:** 1px Hairline outline; rows inside are divided by Hairline rules, the last row without one.
- **Internal Padding:** 14 to 16px per row.
- The matrix, library-home lists, asset lists, the library list and collapsible form blocks share this panel. On the About page, "cards" are hairline-ruled sections with no box at all.

### Inputs / Fields
- **Style:** Raised Control fill, Strong Hairline border, 6px corners, 8px by 10px padding, 13px text; placeholders in Muted Ink. Selects draw their own chevron from two gradients. Filters are a search field plus, when there is a choice to make, a natural-width kind select ("All kinds", "Skills", …).
- **Focus:** 2px amber outline, offset 2px.
- **Labels:** 12.5px weight 600 Secondary Ink above the field, or engraved small caps where the label names a panel control.

### Navigation
- **Sub-tabs:** under Where installed, a row of square, transparent tabs in Muted Ink at weight 600 over a Hairline rule; the active one is Engraved Ink with a 2px amber underline.
- **Faceplate tabs:** transparent, square, Secondary Ink text; hover brightens to Engraved Ink; the active tab is Engraved Ink at weight 600 with a 2px amber underline that sits on the faceplate's bottom rule. Minor tabs are smaller and muted, after a vertical hairline. At narrow width the tab row scrolls horizontally with its scrollbar hidden.

### Jack (signature component)
A 22px circle with a 2px ring that is both the state display and the toggle (`aria-pressed`, a full spoken label and a tooltip). Ring, fill and scale ease over 180ms; hover scales to 1.08 and fills Raised Control.
- **Free:** Jack Ring on the Anodized Panel face.
- **Installed here:** mint ring and fill behind a 3px bezel of panel color.
- **From a folder above:** Mint Ring around a Dim Mint fill, behind the same bezel.
- **Installed for your user:** dashed Mint Ring, transparent face.
- **Will be installed:** amber ring with a solid amber dot.
- **Will be removed:** amber ring around a hollow amber inner ring.
- **Blocked:** red ring crossed by a red diagonal slash.
- The legend line under the matrix uses 14px samples of the same classes, so the legend and the grid can never disagree. Its labels are short: Installed, From a folder, For your user, Will install, Will remove, File in the way.

### Cable
A 2px line in 55% amber from the feeding folder's jack across to the place it reaches, drawn behind the jacks. A pending inheritance shows as a dashed full-amber line. A newly added cable scales in from the folder side over 200ms; cables already on screen do not redraw.

### Routing bar
The pending-change summary: Raised Control fill, 1px amber border, 10px corners, a tabular plain-language sentence ("Add reviewer.json to your user; remove plan from backend"), then a quiet Discard and a primary "Review and apply". It sits static in the dock, under any status message, and wraps at narrow width. List views use the same bar, sticky on its own, with a Strong Hairline border instead of amber.

### Review sheet
A right-docked Lifted Panel sheet with a Strong Hairline left edge that slides 24px in over 220ms. Changes are grouped by place under 12px bold group titles; each line pairs a colored sans operation word (mint "Link", amber "Archive") with a monospace path. Conflicts appear in Fault Ink with fix buttons inline.

### Library row
The everyday unit. A two-column row in a list panel, 13px by 16px padding: on the left the name (weight 620) with its engraved kind tag and any amber "NEW"/"UPDATED" or muted "EDITED HERE" badge, a 12.5px Secondary Ink description, and a 12px Muted Ink line for who shared it and when; on the right, centered across those lines and at most 16rem wide, the one action. At narrow width the action drops under the text.

### Status strip
A Lifted Panel strip with 10px corners and a 9px lamp: amber when updates or new items wait, mint when the library is up to date. One plain sentence, an optional 12px Muted Ink line, and at most one button: a primary "Update library" when updates wait, a quiet "Check for updates" otherwise. The starter-set offer uses the same strip with an amber lamp and a primary "Get the starter set".

### Share panel
Opens inline under a row on the Share screen: a recessed page-tone well with a Hairline border and 8px corners holding a note field (Raised Control textarea), a push checkbox, the change lines in the review-sheet style, and Share and Cancel. After sharing, the row shows the result and a command line: a Lifted Panel box with 8px corners holding the monospace get command and a quiet Copy button.

### Where option
A choice inside the review sheet's "where should it work" fieldset: a transparent box with a Hairline border and 8px corners, a radio, a bold Engraved Ink choice ("Everywhere for me", "Only in backend") and a Secondary Ink explanation. The chosen option's border turns amber.

### Status message
A Raised Control message with 6px corners and a Strong Hairline border, 10px by 14px padding, 13px text, up to 44rem wide, dismissed by click. It never floats over content: it sits at the top of the dock, or inside the review sheet under its hint while the sheet is open. Errors switch the border to Fault Red and the text to Fault Ink.

## Do's and Don'ts

### Do:
- **Do** show install state with the jack and its seven states, and keep the one-line legend directly under the matrix.
- **Do** use the three panel tones and 1px hairlines for every separation; keep the matrix sized to its columns.
- **Do** reserve Patch Amber for what waits for the person: new items, pending changes, the forward action, focus, the active tab and selection.
- **Do** keep monospace for paths, commands and change lines only.
- **Do** use tabular numerals for every count that updates live.
- **Do** give each library row exactly one action: Get (primary amber only for new or updated items, quiet otherwise) or a mint "have" pill with a Remove link.
- **Do** keep occasional row actions as links that appear on hover or focus where the device can hover.
- **Do** summarize per-place settings in one quiet line and open their controls only on request, for one place at a time.
- **Do** put status messages and pending changes in the bottom dock, stacked, so neither covers the other or the grid.
- **Do** draw jack rings in Jack Ring, Mint Ring or a lamp color, never in a hairline tone.
- **Do** use plain words in the interface ("circle", "install", "Review and apply"); keep the patch-bay vocabulary in visuals and class names.
- **Do** turn the cable and sheet animations off under `prefers-reduced-motion: reduce`, and keep state transitions at 160 to 220ms on `cubic-bezier(0.16, 1, 0.3, 1)`.

### Don't:
- **Don't** fill a button, banner or decoration with mint; mint means installed.
- **Don't** put a drop shadow on panels, bars or sheets; only a modal dialog casts one.
- **Don't** place engraved small caps above a heading as a kicker or eyebrow.
- **Don't** show places, agents or layers on Library, Share or first setup.
- **Don't** use a circle for anything that is not a state lamp, a toggleable jack or a setup step number.
- **Don't** float toasts over the grid or show a control that has nothing to choose between.
- **Don't** add raw hex values in component rules; extend the `:root` custom properties.
- **Don't** lay out card grids or stack lifted boxes inside panels; rows inside a panel are divided by hairlines, and a form opened from a row sits in a recessed page-tone well with 8px corners.
