# Docs writing principles

Short filter for **user-facing** docs (Sphinx site, README, published usage guides). Audience: **geo data engineers** who install from PyPI and need a large AOI → polite fetch → analysis-ready xarray path.

Grounded in [Diátaxis](https://diataxis.fr/) plus how mature geospatial Python libs (NumPy/SciPy/xarray-style) speak to practitioners: job-first, honest about behaviour, light on project archaeology.

Integrate with the product canon ({doc}`design-principles`, [AGENTS.md](https://github.com/MatthewJWhittle/tilearray/blob/main/AGENTS.md)) — do not fork a second ethos. This file is the **writing filter**, not a second product design.

---

## Diátaxis map (tilearray)

| Mode | Job | tilearray home |
|------|-----|----------------|
| **Tutorials** | Guided success once | Usage guides (live-host figures) |
| **How-to** | Solve a concrete task | Quick start, troubleshooting recipes, example sources |
| **Reference** | Look up exact API/behaviour | API index, config/preset names, attrs contracts |
| **Explanation** | Understand why / mental model | How it works, design principles (ethos + CRS roles) |

Do not mix modes in one section. Roadmaps and release process are **not** tutorials or how-tos for PyPI users.

---

## Audience filter (keep)

Write for someone who wants to:

1. Install and get a mosaic from a documented public host.
2. Understand plan → polite fetch → decode → lazy mosaic → compute.
3. Tune concurrency / soft-fail / cache / credentials without reading PR history.
4. Hand off CRS / transform / nodata to plotting or rioxarray correctly.
5. Know protocol depth and hard limits (what works today vs what is thin).

Prefer **visual usage guides** from live hosts (figures assembled into Sphinx offline). Plain language over framework jargon.

---

## Cut filter (remove or demote)

Strip from user-facing pages (or move to CONTRIBUTING / {doc}`building-docs` / GitHub only):

| Cut | Why |
|-----|-----|
| Milestone chatter, exit-criteria checklists, soft-exit notes | Release process, not a reader job |
| “We decided…”, agent/PR archaeology, issue/PR deep-links as the story | Process leakage |
| Stage-gate / stranger first-look / nightly-vs-PR CI lanes | Maintainer workflow |
| Unchecked boxes for shipped themes; “feeds 0.N criterion” | Stale or internal |
| AI-slop padding, dual habits, contradictory compute guidance | Noise and distrust |
| Research / product strategy write-ups | Belong in Project store / Notion, not the docs site |

Contributor pages (`building-docs`, CONTRIBUTING, AGENTS) may keep maintainer detail — still avoid obsolete release checklists.

---

## Honesty rules (current vs target)

1. Document **what the installed package does now** in the imperative voice.
2. Mark **target / planned** design explicitly (e.g. separate `bbox_crs` vs output CRS). Never imply shipped behaviour.
3. Prefer delete/condense over “also note that someday…”.
4. Keep accurate API and mosaic contracts (CRS, transform, bands, fail-loud defaults).
5. Align with {doc}`design-principles` — do not fight the spatial contract or scope boundaries.

---

## Checklist before merge (docs PR)

- [ ] Every section answers a **reader job** (install / fetch / understand / look up / fix).
- [ ] No milestone, stage-gate, soft-exit, or agent/PR archaeology in user Start-here paths.
- [ ] Current vs target is labelled where design is unfinished.
- [ ] One primary compute habit (`create_array` → `compute_mosaic`); no competing “preferred” paths.
- [ ] Visual guides / figures pipeline preserved; no rewrite onto another docs stack.
- [ ] Research and internal plans stay out of the repo docs PR.
- [ ] Prefer delete/condense; no version bump; no drive-by feature docs for unshipped work.

Stickiness comes from this checklist plus AGENTS / CONTRIBUTING — not automated keyword lint (fragile false positives on legitimate phrasing).

---

## Tone

Direct, concrete, short. Name the host, the knob, the failure mode. Sound like a library reference written by someone who has hit 403s and wrong CoverageIds — not like a project status report.
