# Strategic Event Tracker: Theory Tournament

A research-grade pipeline that monitors corporate strategic events, has competing management theories generate pre-registered predictions, and scores which theory predicted reality.

**Live leaderboard:** `https://YOUR-APP.streamlit.app`  
**GitHub repo:** `https://github.com/YOUR_USERNAME/strategic-event-tracker`

---

## What this does

1. **Detects events** from SEC EDGAR 8-K filings and NewsAPI (every 6 hours via GitHub Actions)
2. **Locks predictions** — each management theory generates a directional prediction *before* outcomes are observed (timestamped, write-once)
3. **Scores theories** at 3-day and 10-day horizons using cumulative abnormal returns (firm return − SPY return)
4. **Public leaderboard** on Streamlit — with crowd voting as a fourth competitor
5. **Residual tracking** — persistent all-theory misses are flagged for quarterly inductive review

The pre-outcome prediction design mirrors preregistration logic, making the tournament standings a legitimate comparative test of predictive validity.

---

## Event types covered

| Event Type | Source | Notes |
|-----------|--------|-------|
| CEO / officer turnover | SEC 8-K Item 5.02 | Clean trigger, high recall |
| M&A | SEC 8-K Items 1.01, 2.01 | Clean trigger |
| Restructuring / layoffs | SEC 8-K Item 2.05 | Clean trigger |
| Foreign market entry | 8-K Item 8.01 + NewsAPI | Noisier — shown separately on leaderboard |
| Foreign market exit | 8-K Item 8.01 + NewsAPI | Noisier — shown separately |

**Sample frame:** S&P 500 companies only.  
**Backfill window:** 24 months historical, then live going forward.

---

## Theory roster v1

See [THEORY_CHANGELOG.md](THEORY_CHANGELOG.md) for the full versioned roster.

**CEO Turnover:** Upper Echelons Theory · Agency Theory · Disruption/Instability View  
**M&A:** Synergy/TCE · Hubris Hypothesis (Roll 1986) · Managerial Entrenchment  
**Restructuring:** Resource-Based View · Signaling Theory · Stakeholder Theory  
**Foreign Entry:** OLI Paradigm/Internalization · Institutional Theory/LoF · Real Options  
**Foreign Exit:** Strategic Refocusing/RBV · Sunk Cost/Legitimacy · Real Options

---

## Scoring rules

- **Win:** predicted direction (positive/negative) matches realized abnormal return direction
- **Flat:** |CAR| < 0.5% → no clear signal; **all theories score as a loss** (not excluded)
- **Foreign events** scored separately (lower source quality)
- **Crowd** (majority vote) appears as a fourth competitor, scored identically

---

## Research-grade requirements

| Requirement | Implementation |
|------------|---------------|
| No leakage | Predict job (predict.yml) and score job (score.yml) are separate; predictions never regenerated |
| Write-once predictions | Append-only CSV; `locked=True` column on every prediction record |
| Provenance | `predicted_at`, `source_url`, `model_used` stored with every record |
| Reproducibility | Fixed schedule, raw data archived alongside derived data |
| Validation | Stratified ~100-event hand-coding sample; kappa target ≥ 0.80 |
| Theory versioning | `theory_roster_version` field on every event + prediction record |
| Residual tracking | All-miss events flagged; quarterly inductive review |

---

## Setup

### Prerequisites

- Python 3.11+
- GitHub account with a new public repo named `strategic-event-tracker`
- Anthropic API key (required for prediction generation)
- NewsAPI key (required for foreign market events)
- Resend API key (optional, for email notifications)

### Local setup

```bash
# Clone or copy this project to your machine
cd strategic-event-tracker

# Install dependencies
pip install -r requirements.txt

# Set environment variables
export ANTHROPIC_API_KEY=sk-ant-...
export NEWSAPI_KEY=...
export RESEND_API_KEY=...    # optional
export NOTIFICATION_FROM=notifications@yourdomain.com  # optional

# Run the 24-month backfill (takes 4–8 hours; safe to interrupt and resume)
python scripts/backfill.py --months 24

# Run the Streamlit app locally
streamlit run app/streamlit_app.py
```

### Push to GitHub

```bash
export GITHUB_PAT=ghp_...
export GITHUB_REPO=YOUR_USERNAME/strategic-event-tracker

python push_to_github.py
```

### GitHub Actions secrets

Go to your repo → Settings → Secrets and variables → Actions, and add:

| Secret | Required | Description |
|--------|----------|-------------|
| `ANTHROPIC_API_KEY` | ✅ Yes | For prediction generation |
| `NEWSAPI_KEY` | ✅ Yes (for foreign events) | Foreign market entry/exit feed |
| `RESEND_API_KEY` | ☐ Optional | Email notifications |
| `NOTIFICATION_FROM` | ☐ Optional | Sending email address |
| `NTFY_TOPIC` | ☐ Optional | ntfy.sh topic for push notifications |

### Deploy to Streamlit Cloud

1. Go to [share.streamlit.io](https://share.streamlit.io)
2. Connect your GitHub account
3. New app → Repo: `YOUR_USERNAME/strategic-event-tracker`
4. Main file: `streamlit_app.py`
5. Add your API keys under App Settings → Secrets:
   ```toml
   ANTHROPIC_API_KEY = "sk-ant-..."
   ```

---

## Data files

All data lives in the `/data` folder and is committed to the repo on every scoring run.

```
data/
├── events/events.csv          # All detected events
├── predictions/predictions.csv # Locked theory predictions
├── outcomes/
│   ├── outcomes.csv           # CAR calculations per event
│   ├── scores.csv             # Win/loss per theory per event per horizon
│   └── residuals.csv          # All-miss events for quarterly review
├── crowd/votes.csv            # Crowd votes (PII — NOT committed; local only)
└── snapshots/                 # Daily snapshots for archival
```

### Column schemas

See [src/config.py](src/config.py) for complete column definitions (`EVENT_COLUMNS`, `PREDICTION_COLUMNS`, `OUTCOME_COLUMNS`, `SCORE_COLUMNS`).

Key columns for analysis:

**events.csv:** `event_id`, `company`, `ticker`, `cik`, `sic_code`, `sector`, `filing_date`, `event_type`, `event_subtype`, `ai_flag`, `market_cap_usd`, `slack_ratio`, `theory_roster_version`

**predictions.csv:** `prediction_id`, `event_id`, `theory_key`, `theory_name`, `predicted_direction`, `reasoning`, `magnitude_estimate`, `confidence`, `predicted_at`, `model_used`, `locked`

**scores.csv:** `score_id`, `prediction_id`, `event_id`, `theory_key`, `horizon`, `predicted_direction`, `realized_direction`, `is_flat`, `is_win`, `is_foreign_event`, `scored_at`

---

## Validation

The coding scheme for event classification is documented in [docs/coding_scheme.md](docs/coding_scheme.md).

**To run the validation:**

```bash
# Export a stratified sample
python scripts/validate_export.py export --n 100 --output data/validation_sample.csv

# (Arthur hand-codes the sample — fill in arthur_event_type column)

# Compute kappa
python scripts/validate_export.py kappa --file data/validation_sample.csv
```

Target: Cohen's κ ≥ 0.80 on primary event type classification.

---

## Analysis in R/Stata/Python

The `/data` folder contains flat CSV files ready for analysis.

**Example: win rates by theory in R**

```r
library(tidyverse)
scores <- read_csv("data/outcomes/scores.csv")
events <- read_csv("data/events/events.csv")

scores %>%
  left_join(events, by = "event_id") %>%
  filter(!is_flat, !is_foreign_event) %>%
  group_by(theory_name, horizon) %>%
  summarise(
    n = n(),
    wins = sum(is_win),
    win_rate = wins / n,
    .groups = "drop"
  ) %>%
  arrange(horizon, desc(win_rate))
```

---

## Modifying the theory roster

See [THEORY_CHANGELOG.md](THEORY_CHANGELOG.md) for versioning rules.

**To add a new theory (e.g., "Theory D"):**

1. Add to `THEORIES` dict in `src/config.py`
2. Add its key to the relevant event type's `theories` list
3. Bump `THEORY_ROSTER_VERSION`
4. Add a changelog entry with activation date
5. The new theory earns credit only on events generated *after* activation

**Never** retroactively apply a new theory to events that inspired it.

---

## Scheduling

Two GitHub Actions workflows run every 6 hours:

| Workflow | Schedule | What it does |
|----------|----------|-------------|
| `predict.yml` | :00 UTC (0, 6, 12, 18) | Fetch events → generate locked predictions |
| `score.yml` | :30 UTC (0, 6, 12, 18) | Score closed outcome windows → update leaderboard |

The 30-minute offset ensures predictions are never generated *after* scoring has run.

---

## Adapting to a different LLM

The prediction model is set via a single config variable:

```python
# src/config.py
PREDICTION_MODEL = os.getenv("PREDICTION_MODEL", "claude-opus-4-8")
```

To switch to a different provider (e.g., Grok), set `PREDICTION_MODEL` to the relevant model string and update the client in `src/predictor.py`. The `generate_prediction()` function is the only place that calls the LLM.

---

## License

MIT. Data is licensed separately — if you use this dataset in a publication, please cite the repository and the methodology documented here.
