# Theory Changelog

This file documents all additions, modifications, and removals to the theory roster.
Every event record stores a `theory_roster_version` field pointing to the version
active when predictions were generated. This ensures the dataset remains internally
consistent as the roster evolves.

**RULE:** New theories earn credit only on events generated **after** the version entry date.
No retroactive application to prior events.

---

## Version 1 — Initial Roster
**Date activated:** 2026-06-21  
**Events covered:** All events from 2024-06-21 (backfill start) through present

### CEO / Officer Turnover
| Theory Key | Theory Name | Predicted Direction |
|------------|-------------|---------------------|
| `upper_echelons` | Upper Echelons Theory | Conditional (outsider × underperformance) |
| `agency_ceo` | Agency Theory (CEO Turnover) | Positive |
| `disruption_ceo` | Disruption / Instability View | Negative |

### M&A
| Theory Key | Theory Name | Predicted Direction |
|------------|-------------|---------------------|
| `synergy_tce` | Synergy / Transaction Cost Economics | Positive |
| `hubris_ma` | Hubris Hypothesis (Roll 1986) | Negative |
| `entrenchment_ma` | Managerial Entrenchment | Negative |

### Restructuring / Layoffs
| Theory Key | Theory Name | Predicted Direction |
|------------|-------------|---------------------|
| `rbv_restructuring` | Resource-Based View | Negative |
| `signaling_restructuring` | Signaling Theory | Positive |
| `stakeholder_restructuring` | Stakeholder Theory | Negative |

### Foreign Market Entry
| Theory Key | Theory Name | Predicted Direction |
|------------|-------------|---------------------|
| `oli_entry` | OLI Paradigm / Internalization Theory | Conditional (mode × distance) |
| `institutional_entry` | Institutional Theory / Liability of Foreignness | Conditional (distance × commitment) |
| `real_options_entry` | Real Options Theory (Entry) | Conditional (commitment × uncertainty) |

### Foreign Market Exit
| Theory Key | Theory Name | Predicted Direction |
|------------|-------------|---------------------|
| `strategic_refocusing_exit` | Strategic Refocusing / RBV | Positive |
| `sunk_cost_exit` | Sunk Cost / Legitimacy Loss | Negative |
| `real_options_exit` | Real Options Theory (Exit) | Conditional (uncertainty priced vs. not) |

---

## Reserved for v2 (deferred)
The following event types and associated theories are deferred to version 2:
- Strategic alliances / joint ventures (standalone, not as entry vehicle)
- Activist investor campaigns

---

## How to add a new theory

1. Add the theory to `THEORIES` dict in `src/config.py` with a new unique key
2. Add the theory key to the relevant `EventType.theories` list in `src/config.py`
3. Bump `THEORY_ROSTER_VERSION` in `src/config.py`
4. Add an entry to this changelog with the date and version number
5. The new theory will begin generating predictions on the next scheduled predict job
6. Existing event records retain their original `theory_roster_version` — never backfill
