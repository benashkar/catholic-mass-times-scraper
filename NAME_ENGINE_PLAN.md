# Name Engine — Multi-Engine PDF Name Extraction & Confidence Scoring

## Goal
Build a reusable name extraction engine that can be shared across projects (church scrapes, cherry_road_pubs, future PDF-based projects). Dramatically reduce junk names by using multiple independent scoring systems and flagging disagreements.

## Architecture Decision
**Standalone Python package** (`name-engine`) in a separate repo (`benashkar/name-engine`).
- Pip-installable by any project
- API: `from name_engine import extract_names, score_names`
- CLI: `name-engine extract --input file.pdf --output names.csv`
- Can later be wrapped as a Claude Code agent or microservice

## Current Problems (church scrapes)
1. **Merged column names** — pdfplumber reads multi-column PDFs linearly, merging names across columns. One Atlanta church produced 645 fake 3-word names.
2. **Pattern 6 too loose** — Generic contextual pattern matches any "FirstName LastName" near keywords. Produces 67% of Georgia names with worst precision.
3. **Score gaming** — English words that are also names (Grace, Faith, Hope) get full credit.
4. **No NLP/ML** — Pure regex + dictionary lookup. No spaCy, no NER, no ML.
5. **Duplicated blocklists** — `non_name_words` copy-pasted in 2 places.

## Implementation Plan

### Phase 1: Column Detection (fixes root cause)
**Goal:** Extract text per-column, not per-page, to eliminate merged names.

- Use pdfplumber's `extract_words()` with bounding box coordinates
- Cluster word `x0` positions to detect column boundaries
- Extract text within each column separately
- Re-run name extraction on column-aware text

**Files:** New `name_engine/pdf_columns.py`
**Dependencies:** pdfplumber (already installed)

### Phase 2: spaCy NER Engine
**Goal:** Add Named Entity Recognition as independent validation.

- Run `en_core_web_sm` on extracted text chunks
- Check if each extracted name overlaps with a spaCy PERSON entity
- Score: 1.0 if PERSON, 0.5 if ORG/GPE (suspicious), 0.0 if no entity

**Files:** New `name_engine/engines/spacy_ner.py`
**Dependencies:** spacy, en_core_web_sm model

### Phase 3: name-dataset Engine
**Goal:** Third scoring engine with 730K+ first names, 983K+ last names from 54 countries.

- Larger reference than SSA (100K) + Census (162K)
- Provides country-of-origin and gender probability
- Score: weighted by match confidence and country relevance

**Files:** New `name_engine/engines/name_dataset_engine.py`
**Dependencies:** names-dataset

### Phase 4: Consensus Scoring
**Goal:** Combine all engines, flag disagreements.

| Dictionary | spaCy NER | name-dataset | Verdict |
|-----------|-----------|-------------|---------|
| high | PERSON | match | **high** — all agree |
| high | not PERSON | match | **medium** — likely merged/contextual |
| high | PERSON | no match | **medium** — unusual name, NER confirms |
| low | PERSON | match | **medium** — dictionary missed it |
| low | not PERSON | no match | **low** — junk |

Names where engines disagree → `is_suspect = 1` for human review.

**Files:** New `name_engine/consensus.py`

### Phase 5: Integration & Re-scoring
**Goal:** Wire into church scrapes pipeline, re-score all 1.3M existing names.

- Replace `score_name_confidence()` in `run_bulletin_scraper.py` with name-engine call
- Run re-score script on existing bulletin_name table
- Update dashboard stats
- Replace hand-rolled `parse_name_parts()` with `nameparser` library
- Consolidate duplicated blocklists into single source

### Phase 6: Package & Publish
**Goal:** Extract engine into standalone `benashkar/name-engine` repo.

- Move core logic to separate repo
- Add CLI interface
- Add tests
- Pip-installable from GitHub: `pip install git+https://github.com/benashkar/name-engine.git`
- Church scrapes and cherry_road_pubs both import it

## Database Performance Fixes (parallel work)

### P0 — Verify FK indexes exist
```sql
-- Check if these exist; create if not
CREATE INDEX idx_bn_pdf_id ON bulletin_name(bulletin_pdf_id);
CREATE INDEX idx_bp_source_id ON bulletin_pdf(bulletin_source_id);
```

### P1 — Use pre-computed stats for pagination total count
Replace expensive `SELECT COUNT(*) FROM v_bulletin_ui_names` with `bulletin_state_stats.total_names`.

### P1 — Fix church_has_bulletin_names()
Replace 50K-row DataFrame load with `SELECT 1 ... LIMIT 1`.

### P2 — New indexes
```sql
CREATE INDEX idx_bn_confidence_suspect ON bulletin_name(confidence, is_suspect);
CREATE INDEX idx_service_church_active_date ON service(church_id, is_active, event_date);
CREATE INDEX idx_church_state_name ON church(state_code, name);
```

## Dependencies (new)
```
spacy>=3.7
nameparser>=1.1
names-dataset>=3.1
```
Note: spacy model downloaded separately: `python -m spacy download en_core_web_sm`
Note: No spacy on Python 3.14 (per global rules). Use 3.12 in Docker.

## Testing Strategy
1. **Unit tests** for each engine against known good/bad names
2. **Georgia regression test** — re-extract Georgia bulletin names, compare junk rate before/after
3. **Precision/recall** on manually-reviewed `removed_names_review_ben.xlsx` (ground truth)
4. **Cross-project test** — run on cherry_road_pubs PDFs to validate generalizability
