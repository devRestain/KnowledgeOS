# G lane — GUI design summary

This summary belongs to the KnowledgeOS documentation boundary, outside the
Vault. The full implementation contract is in
[`G_GUI_DESIGN_CONTRACT.md`](G_GUI_DESIGN_CONTRACT.md).

## Direction

Home becomes a **signal console**, not a long vertical button list. On a
MacBook, the content canvas is split into two readable sections:

| Command deck | Repository reading field |
| --- | --- |
| Today, one `Open daily` action, compact capture commands, and a short action queue | Three source-linked text windows: Read first, Decide, and Watch |

The signal rail between them indicates the boundary between moving and
reading. It is the one distinctive visual device; the rest stays quiet and
content-first.

On mobile, the same regions stack vertically. The layout never creates a
horizontal scroll surface.

## Visual language

Use the recommended navy/slate foundation (`#1E3A5F`, `#334155`, `#0F172A`,
`#192134`) with secure green (`#059669`) for confirmed states and a restrained
amber for the current human attention path. Use `Fira Sans` for reading and
interface text; reserve `Fira Code` for shortcuts, paths, IDs, hashes, and
timestamps. Use visible focus, semantic state text, and light/dark contrast
checks.

## Action rule

`Open daily` appears once. A `daily/open` URI is the implementation behind that
action, not another visible button. Capture choices stay compact and use their
existing reviewed IDs and hotkeys. The space removed from oversized controls
is used for short, source-linked repository text rather than decorative cards.

## Text windows

The default three windows are:

- **Read first:** today focus or the current useful sentence.
- **Decide:** an open question or decision with its source.
- **Watch:** an active project, knowledge item, Inbox capture, or AI Review
  item, selected from existing canonical Base surfaces.

They are read-only projections, not duplicated summaries. Git/provider data is
not silently added to Home. If Native Bases cannot expose a body excerpt, the
future implementation must use a reviewed read-only adapter or disclose the
property-only fallback; it must not invent content or add a second writer.

## Figma and Obsidian follow-up

The Figma project `KnowledgeHub — G GUI Design Studio` is the canvas to edit in
a later session. The next edit should create MacBook, compact desktop, mobile,
and state frames, then apply the two-pane/stacked topology and action
consolidation.

The G03/G09 static implementation has now updated the Blueprint-owned C08
source, Home-only schema registration, generated Home, CSS, generated
artifacts, and focused tests together. This document still does not edit
Figma, operate Obsidian, mutate plugin settings, add `cssclasses` to
future-note templates, or claim that a theme, text projection, or bottom
Properties placement is live. Those remain separate G07/profile/device
evidence.
