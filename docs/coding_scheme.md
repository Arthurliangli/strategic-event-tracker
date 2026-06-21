# Event Classification Coding Scheme
**Version:** 1.0  
**Date:** 2026-06-21  
**Purpose:** Validation rubric for Cohen's kappa inter-rater agreement test  
**Target:** κ ≥ 0.80 on primary event type classification

---

## Overview

The pipeline classifies each 8-K filing (or news article) into one of five primary event types, and then into a subtype within that category. This rubric defines the coding rules used both by the LLM classifier and by Arthur for the validation sample (~100 events, stratified across types).

---

## Primary Event Types

### 1. `ceo_turnover` — CEO / Officer Turnover

**Trigger:** SEC 8-K Item 5.02

**Include:**
- Departure of CEO, President, CFO, COO, CTO, or any C-suite officer
- Appointment or election of CEO, President, CFO, COO, CTO
- Board of director membership changes (elections, resignations)
- Interim appointments when a permanent successor has not been named

**Exclude:**
- Routine board committee changes with no officer departure/appointment
- Compensation changes only (no personnel change)
- Changes in title without role change (e.g., "CEO also assumes Chairman role" when both were already the same person)

**Subtypes:**
| Subtype | Definition |
|---------|------------|
| `ceo_departure` | CEO (or President serving as CEO) announced departure, resignation, retirement, or termination |
| `ceo_appointment` | Named appointment of a new CEO or permanent successor |
| `cfo_departure` | CFO departure |
| `cfo_appointment` | CFO appointment |
| `coo_departure` | COO departure |
| `coo_appointment` | COO appointment |
| `director_departure` | Board director resignation or departure |
| `director_appointment` | Board director election or appointment |
| `officer_change` | Other C-suite officer change not covered above |

**Ambiguous cases:**
- If the same filing announces both a departure and an appointment (succession), code as the **departure** subtype (it is the primary event)
- If multiple officers change, code the **most senior** one

---

### 2. `ma` — Merger & Acquisition

**Triggers:** SEC 8-K Items 1.01 (agreement) and 2.01 (completion)

**Include:**
- Definitive merger agreement (acquirer + target both named)
- Acquisition of a business, subsidiary, or significant assets
- Completion of a previously announced deal
- Divestiture of a business unit or subsidiary (coded as subtype `divestiture`)

**Exclude:**
- Licensing agreements
- Distribution or supply agreements (even if material)
- Minority equity investments < 20% stake (code as `foreign_entry` if cross-border, otherwise exclude)
- Real estate acquisitions (property only, no business)

**Subtypes:**
| Subtype | Definition |
|---------|------------|
| `acquisition` | Firm is acquiring another business (horizontal, vertical, or conglomerate) |
| `divestiture` | Firm is selling/divesting a business unit or subsidiary |
| `merger` | Combination of two firms as equals (rare; usually one acquirer) |
| `joint_venture` | Formation of a JV entity as a deal structure (not market entry) |

---

### 3. `restructuring` — Restructuring / Layoffs

**Trigger:** SEC 8-K Item 2.05

**Include:**
- Announced workforce reductions (layoffs, RIFs, redundancy programs)
- Plant or facility closures
- Business unit eliminations
- Reorganizations that involve headcount reductions or asset disposals
- Charges related to exit or disposal activities

**Exclude:**
- Routine divestitures of subsidiaries (code as `ma` / divestiture)
- Cost reduction programs with no headcount or facility impact specified
- Note: some Item 2.05 filings involve only accounting charges with no operational restructuring — exclude if no employees or facilities are affected

**Subtypes:**
| Subtype | Definition |
|---------|------------|
| `layoffs` | Workforce reduction / headcount reduction as primary announcement |
| `facility_closure` | Plant, office, or facility closure as primary announcement |
| `restructuring` | Broader reorganization (may include both layoffs and closures) |
| `exit_activity` | Exit or disposal activity with no specific headcount or facility detail |

---

### 4. `foreign_entry` — Foreign Market Entry

**Triggers:** SEC 8-K Item 8.01 (keyword-screened) + NewsAPI

**Include:**
- New subsidiary formation in a foreign country
- New manufacturing, production, or R&D facility established abroad
- Greenfield investment in a foreign market
- New joint venture in a foreign country (as a market entry vehicle)
- Acquisition of a foreign company (when primary strategic rationale is market entry, not financial; if primarily financial acquisition, code as `ma`)
- Announced expansion into a new country market
- New distribution or sales office in a foreign country

**Exclude:**
- Domestic U.S. expansions
- Foreign licensing agreements with no equity investment (too low commitment to code)
- M&A deals where the target is foreign but the stated rationale is technology/talent, not market access (code as `ma`)

**Subtypes:**
| Subtype | Definition |
|---------|------------|
| `wholly_owned` | Wholly-owned subsidiary (WOS) or wholly-owned greenfield facility |
| `joint_venture` | JV with a local or other foreign partner |
| `acquisition` | Acquisition of an existing foreign firm as entry vehicle |
| `greenfield` | New facility/plant with no existing local acquisition |
| `licensing` | Licensing or franchising agreement involving entry (low-commitment) |
| `other_entry` | Entry announced but mode not specified or unclear |

**Note:** This category relies on a noisier source than Items 5.02, 1.01/2.01, 2.05. False positives are more common. When in doubt, **exclude** rather than include — it is better to have a cleaner, smaller dataset than a larger, noisier one.

---

### 5. `foreign_exit` — Foreign Market Exit

**Triggers:** SEC 8-K Item 8.01 (keyword-screened) + NewsAPI

**Include:**
- Closure of a foreign subsidiary or facility
- Withdrawal from a foreign market
- Sale or divestiture of a foreign subsidiary (when primary rationale is market exit, not portfolio optimization; if primarily portfolio optimization, consider `ma`)
- Wind-down of foreign operations
- Announcement of ceasing business in a specific country

**Exclude:**
- Restructuring of a foreign operation without exit (code as `restructuring`)
- Reduction in foreign headcount without closing the foreign operation
- Temporary suspension of foreign operations

**Subtypes:**
| Subtype | Definition |
|---------|------------|
| `divestiture` | Sale of a foreign subsidiary or business |
| `closure` | Closing of a foreign facility or subsidiary (not sold) |
| `withdrawal` | Announcement of market withdrawal (mode of exit not yet specified) |

---

## Coding rules for ambiguous cases

1. **If an event could be both M&A and Foreign Entry:** Code as `foreign_entry` if the primary stated strategic rationale is market access in a new country. Code as `ma` if the primary rationale is financial, technological, or talent-based.

2. **If the same 8-K announces both CEO departure and M&A:** Code the **most significant** event by its strategic salience. Both types are logged; the primary code for tournament purposes is the one with the most direct market impact. When in doubt, code both and flag.

3. **Item 8.01 filing without clear event type:** Exclude. Do not force-fit Item 8.01 filings into a category without clear evidence. Mark as "not applicable" in arthur_event_type.

4. **Materiality threshold:** Only events that are likely to be market-moving are included. Do not code:
   - Minor executive departures at the VP level
   - Very small M&A deals (< $10M stated value if disclosed)
   - Single-employee "restructuring" without a program name or charge

---

## Validation procedure

1. Export a stratified sample: `python scripts/validate_export.py export --n 100`
2. Open `data/validation_sample.csv` in Excel
3. For each row:
   - Read the `headline` and visit the `raw_text_url` if needed
   - Fill in `arthur_event_type` using the primary types above
   - Fill in `arthur_event_subtype` using the subtype table above
   - Add notes in `arthur_notes` for any ambiguous cases
4. Run: `python scripts/validate_export.py kappa --file data/validation_sample.csv`
5. Review disagreements — revise rubric if systematic patterns emerge
6. Re-code a second batch of 25 events after any rubric revision
7. Document final κ and any rubric revisions in this file
