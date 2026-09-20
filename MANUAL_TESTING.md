# Temporal Nexus · verify the updated workspace

Use your own authorized sample. These steps test the current prototype, not facial identity verification.

## Start

1. Extract the ZIP into a new folder. Keep your previous working copy as a backup.
2. Activate your Python environment and run `python -m pip install -r requirements.txt` from the extracted project root.
3. Copy `.env.example` to `.env`. Add your own `TAVILY_API_KEY` only if testing live discovery. Keep the existing Tesseract setup; set `TESSERACT_CMD` if needed.
4. Run `python -m uvicorn backend.main:app --reload`.
5. Open `http://127.0.0.1:8000/` and hard refresh with Ctrl+Shift+R. Use this address rather than an IDE HTML preview.

## Input and image clues

1. Select a valid PNG/JPEG containing readable text. Enter a name, organization, role and department. Confirm authorization.
2. Click **Validate Only**. Expect image metadata and your five context fields, with blank fields marked not supplied.
3. Click **Extract Image Text**. Expect real OCR observations; supplied details stay separate.
4. In clue review, correct a misread word. Expect the original OCR text to remain visible.
5. Make two event clues read `Nexus Summit` and `NEXUS SUMMIT`, both selected. With supplied name `Alex Demo`, click **Prepare Search Queries**. Expect only one equivalent `"Alex Demo" "Nexus Summit"` query and merged contributor references.
6. Deselect both event clues and prepare again. That event query must disappear. Change a category and prepare again; its explanation must use the new category.
7. Change the supplied role or department after preparing. Previous downstream results must be cleared so stale evidence is not displayed.

## Live sources and report

1. Restore your authorized real context and prepare queries.
2. Click the discovery action. This sends query text to Tavily; no image is sent. Without a key, expect a clear provider configuration error, not fabricated results.
3. Select at most three relevant candidate pages, then analyze them. Restricted/unreadable pages must show a status rather than invented observations.
4. Click the correlation action. Expect a concise **Identity Resolution Report**: summary metrics first, then seed corroboration, canonical Connected Public Profiles, additional observations, missing/conflicting evidence, and collapsed source/audit details.
5. Confirm the organization in **Supplied identity** is exactly your supplied organization, even if an OCR clue contains only a fragment.
6. Expand a field. Expect source excerpts and clickable source links. A source-wide text mention alone must appear as partial evidence, not identity proof.
7. A missing role/department must be marked missing; a different affiliation needs review rather than becoming a hard identity conflict.
8. An explicit different name/username must produce review-required output. Source records must remain separate.
9. Check the `X / Y` label: it counts supported supplied fields, not matching words or confidence. It must not be presented as a probability.
10. Resize the browser to mobile width. Check the report, evidence disclosures, keyboard focus and navigation links.

Live pages vary. For reproducible edge cases, run the automated synthetic-record tests below; do not change real records to manufacture a successful report.

## Automated checks

```sh
python -m unittest discover -s tests -q
node --test tests/test_frontend.js tests/test_report.js
```

Verified in this update: 135 Python tests and 31 JavaScript tests passed. Existing exact response assertions were expanded for the two new optional context fields. Network discovery is mocked in automated tests; a live Tavily call requires your own key. JavaScript tests simulate the DOM, so manual browser testing remains necessary. Automated browser screenshots could not be completed because the Chromium download failed in the build environment.

## Current boundaries

Implemented: validation, local multi-pass OCR, editable clue provenance, query deduplication, Tavily discovery, selected-source reading, structured extraction, deterministic correlation and evidence reporting.

Not implemented in this update: biometric face identification, general logo recognition, automatic cross-platform account ownership proof, or any claim that the ML score is a calibrated identity probability. Temporal reasoning is implemented only for explicit dated/current/historical observations; source dates and repeated text alone are not converted into employment, attendance, or activity claims. API report inputs are client-supplied prototype data, not cryptographically authenticated evidence.

## Multi-platform discovery and Connected Public Profiles

API additions are backward-compatible:
- `/api/search-plan` keeps `queries` (maximum six) and adds `platform_queries` (maximum three). The platform groups cover LinkedIn/Scholar, GitHub, and Instagram/YouTube/X/Twitter. Matching Tavily `include_domains` filters bound these targeted requests as well as the displayed site clauses. Generic queries continue to discover institutions, conferences, publications and personal sites.
- `/api/discover` accepts the optional `platform_queries` array. A click makes at most nine Tavily Basic requests, five results each, without retries. The UI allows 95 seconds; provider timeout remains eight seconds per request. Authentication/quota failures stop remaining calls; other failures preserve successful results.
- Discovery candidates add `candidate_id`, `platform`, `verification` and per-query `provenance` (original URL, rank, snippet). Only known platform host aliases/scheme/slash variants are collapsed; meaningful URL parameters and distinct handles remain separate. Generic websites retain potentially meaningful host/scheme/path differences.
- `/api/analyze-sources` adds `profile` to each source. At most three selected pages are read. Existing source protections and request limits remain in place.
- `/api/correlate` accepts optional `discovered_candidates` (maximum 45) and returns `report.connected_profiles`. Old response fields remain available. Profiles are recomputed from source observations, not trusted from a client-submitted `profile`.

Association rules: a subject-scoped name plus matching organization, role or department supports an association. Clear role aliases are compared; broad department fragments are not full matches. Explicit current organization differences require review, not automatic merging. Historical/undated affiliations remain uncertainty rather than contradictions. A one-hop explicit link from an already supported record adds evidence; an inaccessible target remains at most partially supported. Name/handle similarity, indexed snippets, overlaps, footer accounts and numbers of URLs cannot independently establish ownership. No confidence percentages or `IDENTITY_CONFIRMED` state are generated. Additional education/projects/publications remain separate from supplied-field coverage.

### Exact authorized HOD-case procedure

1. From the project directory, stop the previous server with Ctrl+C and run:
   ```powershell
   .\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
   ```
   Use the existing local `.env` containing `TAVILY_API_KEY`; do not put the key into the browser or this document.
2. Open `http://127.0.0.1:8000/` and press Ctrl+Shift+R. Select your existing authorized photograph containing CMR TECHNICAL CAMPUS signage.
3. Enter name `Dr. S Rao Chintalapudi`, organization `CMR Technical Campus`, role `HOD`, department `CSE (AI & ML)`, and leave username blank. Confirm authorization.
4. Run image extraction. Correct misread text, preserve the displayed original OCR, and select only useful visible clues with appropriate categories. A fragment such as `CMR` must not replace the supplied organization.
5. Click **Prepare Search Queries**. Inspect the generic queries and up to three additional grouped site queries. The LinkedIn/Scholar group should use the canonical name and full organization. No search has run yet.
6. Run discovery once. This uses real Tavily quota. Expect only actual provider results, each with a platform label and **Unverified candidate** badge. Expand/review originating queries. No results on a platform is not evidence that no profile exists. Partial failures must preserve successful results.
7. Select up to three useful actual results (prefer an institution/faculty record and genuinely relevant platform records if returned). Analyze selected sources. Expect platform and access state. Restricted sources remain visible with their indexed metadata; they must not show invented subject fields.
8. Run correlation. Review **Connected Public Profiles**: platform, name/username when known, status, supporting/missing/conflicting signal counts, access state, rationale and expandable evidence. Unselected discovered candidates are explicitly snippet-only. A video/repository reference is a page reference, not automatically a person-owned profile.
9. Expand **View supporting, missing and source evidence**. Check that each asserted link has a subject-specific origin. Footer university accounts must not become the person's profiles. Current differences remain visible for review; historical affiliation is not automatically a conflict. Additional public observations do not increase seed coverage.
10. Change the role or a clue correction. Previous downstream results must clear. Regenerate before continuing. Reset the case and confirm all candidate/report state clears.

No specific LinkedIn, GitHub, Instagram, or other profile is an expected finding. Record the actual provider results and evidence. Login walls, lack of indexing, unsupported page markup and ambiguous same-name records can legitimately leave the outcome insufficient.

## Concise Stage 06 regression checks

Use a multi-platform case such as a public developer/researcher with a known handle. The final unexpanded report should stay short even when discovery returns many URLs. Verify that:

- GitHub repositories are attached to one canonical GitHub profile rather than rendered as separate people.
- X/Twitter status, replies and repost URLs are attached to one account record.
- LinkedIn posts are evidence/mentions; only a canonical `/in/` page is a LinkedIn profile candidate.
- Personal-site subpages consolidate under one personal-site record.
- Indexed profile snippets may support exact seed role/organization/department values when the snippet is clearly subject-anchored, but remain labelled indexed metadata.
- Full Tavily snippets and raw JSON are not rendered in the default report. Evidence excerpts are bounded and the complete source/audit trail remains collapsed.
- The top report shows the actual computed seed coverage, canonical profile-candidate count, source-domain count and direct-conflict count.

The UI intentionally presents synthesis first and audit detail on demand.

## Stage 06 advanced decision-flow regression

After **Correlate Identity Evidence**, verify that the default report is ordered as:

1. `MOST SUPPORTED IDENTITY`
2. `CONNECTED PUBLIC FOOTPRINT`
3. `TEMPORAL REASONING`
4. `RELATIONSHIP GRAPH`
5. `EVIDENCE`
6. `CONFLICTS & UNCERTAINTY`

The top card should state the canonical seed identity, association status, supplied-signal coverage, connected public-trace count and direct-conflict count. Supported/partially-supported canonical profiles belong in the footprint. Conflicting or insufficient candidates belong under Conflicts & uncertainty. Repositories, posts, articles and personal-site subpages must not appear as independent public identities. Raw snippets and source records should stay collapsed under **Full evidence & source audit**.


## Advanced final-phase checks

### Temporal reasoning
- A source saying a person **currently** works for a different organization should create a temporal conflict/review item.
- A source explicitly marked historical (for example an old role/year) should remain historical context, not automatically conflict with the current seed.
- Timeline entries should appear only when the source exposes an explicit year/date or current/historical temporal label.
- The UI must never transform a page publication date into an employment/event date without source support.

### Relationship graph
- The center node is the seed/resolved identity.
- Supported/partial public profiles connect to the identity as public traces.
- Organization/role/department nodes appear only when the seed field has evidence support.
- Project/event/publication/education/location nodes are sourced from supported/partial profile observations and remain bounded.
- Explicit profile-to-profile links render only when both endpoints are discovered canonical profiles.
- Conflicting nodes/edges remain visually distinct.

### Custom ML assist
- The report shows a model association score and band (`strong`, `moderate`, `weak`).
- Treat the score as an assistive ranking/evidence signal only; it is **not** a calibrated identity probability.
- The model is dependency-free logistic regression trained locally on controlled synthetic same-person/different-person feature pairs.
- Explicit deterministic conflicts cap the ML score and force review; the model never overrides them.
- Compare the model output with the deterministic evidence summary during the demo rather than presenting it as proof.
