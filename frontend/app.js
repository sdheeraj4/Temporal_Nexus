"use strict";

const form = document.querySelector("#input-form");
const fields = document.querySelector("#input-fields");
const imageInput = document.querySelector("#image");
const preview = document.querySelector("#preview");
const fileDetails = document.querySelector("#file-details");
const status = document.querySelector("#request-status");
const content = document.querySelector("#result-content");
const rawDetails = document.querySelector("#raw-details");
const rawJSON = document.querySelector("#raw-json");
const results = document.querySelector("#results");
const resetButton = document.querySelector("#reset");
let previewURL = null;
let busy = false;
let reviewedClues = [];
const clueControls = new Map();
let planRevision = 0;
const clueList = document.querySelector("#clue-list");
const addVisualClueButton = document.querySelector("#add-visual-clue");
const planResults = document.querySelector("#query-results");
const planStatus = document.querySelector("#plan-status");
const prepareButton = document.querySelector("#prepare");
const discoverButton = document.querySelector("#discover");
const discoveryResults = document.querySelector("#discovery-results");
const discoveryStatus = document.querySelector("#discovery-status");
const analyzeButton = document.querySelector("#analyze-sources");
const analysisResults = document.querySelector("#analysis-results");
const analysisStatus = document.querySelector("#analysis-status");
const correlateButton = document.querySelector("#correlate");
const correlationResults = document.querySelector("#correlation-results");
const correlationStatus = document.querySelector("#correlation-status");
let preparedQueries = [];
let platformQueries = [];
let preparedBody = null;
let discoveredCandidates = [];
const candidateSelections = new Map();
let analysisRevision = 0;
let analyzedSources = [];
let correlationRevision = 0;

function setPipelineStage(stage) {
  for (let index = 1; index <= 6; index += 1) {
    const item = document.querySelector(`[data-stage="${index}"]`);
    if (!item) continue;
    item.className = `pipeline-item${index < stage ? " is-complete" : index === stage ? " is-active" : ""}`;
  }
}

function prettyStatus(value) {
  return String(value || "unknown").replaceAll("_", " ").replace(/\b\w/g, letter => letter.toUpperCase());
}

function makeBadge(text, state = "") {
  const badge = document.createElement("span");
  badge.className = `badge${state ? ` ${state}` : ""}`;
  badge.textContent = text;
  return badge;
}

function safeWebLink(rawURL, text, className = "") {
  try {
    const parsed = new URL(rawURL);
    if (!["http:", "https:"].includes(parsed.protocol) || parsed.username || parsed.password) return null;
    const link = document.createElement("a");
    link.href = parsed.href;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.textContent = text || rawURL;
    link.className = className;
    return link;
  } catch {
    return null;
  }
}

function sourceLabel(rawURL, fallback = "Public source") {
  try {
    const host = new URL(rawURL).hostname.replace(/^www\./, "");
    return host || fallback;
  } catch {
    return fallback;
  }
}

function clearCorrelation(message = "Analyze at least one candidate source before correlation.") {
  correlationRevision += 1;
  correlationResults.replaceChildren();
  correlationStatus.textContent = message;
  correlateButton.disabled = true;
}

function clearAnalysis(message = "Discover candidate sources, then select up to three to analyze.") {
  analysisRevision += 1;
  analyzedSources = [];
  analysisResults.replaceChildren();
  analysisStatus.textContent = message;
  analyzeButton.disabled = true;
  clearCorrelation();
}

function clearDiscovery() {
  discoveredCandidates = [];
  candidateSelections.clear();
  discoveryResults.replaceChildren();
  discoveryStatus.textContent = "Prepare current queries before running discovery.";
  clearAnalysis();
}

function invalidatePlan() {
  preparedQueries = [];
  platformQueries = [];
  preparedBody = null;
  discoverButton.disabled = true;
  clearDiscovery();
  planRevision += 1;
  planResults.replaceChildren();
  planStatus.textContent = "No current queries. Click Prepare Search Queries to regenerate.";
}

function clearClues() {
  reviewedClues = [];
  clueControls.clear();
  clueList.replaceChildren();
  document.querySelector("#clue-status").textContent = "Extract image text to review its lines. Supplied context can be used on its own; visibly missed text can be added as a reviewer clue.";
  if (addVisualClueButton) addVisualClueButton.disabled = busy;
  invalidatePlan();
}

function renderClueCard(clue) {
  const card = document.createElement("div");
  card.className = "clue";
  const heading = document.createElement("h3");
  heading.textContent = clue.source === "reviewer_added" ? `${clue.clue_id} · reviewer-added` : clue.clue_id;
  const original = document.createElement("p");
  original.className = "clue-original";
  original.textContent = clue.source === "reviewer_added"
    ? "Source: reviewer-added visible image clue — not OCR output"
    : `Original OCR: ${clue.original_text}`;
  const correction = document.createElement("textarea");
  correction.id = `${clue.clue_id}-text`;
  correction.value = clue.corrected_text;
  correction.maxLength = 200;
  const correctionLabel = document.createElement("label");
  correctionLabel.htmlFor = correction.id;
  correctionLabel.textContent = clue.source === "reviewer_added" ? "Visible text" : "Corrected text";
  correction.addEventListener("input", () => { clue.corrected_text = correction.value; invalidatePlan(); });
  const select = document.createElement("select");
  select.id = `${clue.clue_id}-category`;
  for (const category of ["other", "name", "username", "event", "organization"]) {
    const option = document.createElement("option");
    option.value = category;
    option.textContent = category;
    select.append(option);
  }
  select.value = clue.category || "other";
  const categoryLabel = document.createElement("label");
  categoryLabel.htmlFor = select.id;
  categoryLabel.textContent = "Category";
  select.addEventListener("change", () => { clue.category = select.value; invalidatePlan(); });
  const checkbox = document.createElement("input");
  checkbox.type = "checkbox";
  checkbox.id = `${clue.clue_id}-selected`;
  checkbox.checked = Boolean(clue.selected);
  checkbox.addEventListener("change", () => { clue.selected = checkbox.checked; invalidatePlan(); });
  select.addEventListener("input", invalidatePlan);
  checkbox.addEventListener("input", invalidatePlan);
  clueControls.set(clue.clue_id, { correction, select, checkbox });
  const selectionLabel = document.createElement("label");
  selectionLabel.className = "consent";
  const selectionText = document.createElement("span");
  selectionText.textContent = "Use this reviewed clue";
  selectionLabel.append(checkbox, selectionText);
  card.append(heading, original, correctionLabel, correction, categoryLabel, select, selectionLabel);
  clueList.append(card);
  return correction;
}

function reviewOCR(text) {
  clearClues();
  const lines = text.split(/\r?\n/).map((original, index) => ({
    clue_id: `clue-${String(index + 1).padStart(4, "0")}`,
    original_text: original, corrected_text: original, selected: false, category: "other", source: "ocr"
  })).filter(clue => clue.original_text.trim());
  reviewedClues = lines.slice(0, 50);
  document.querySelector("#clue-status").textContent = lines.length
    ? `${reviewedClues.length} reviewable OCR lines. All start unselected.${lines.length > 50 ? " Only the first 50 are reviewable; the full OCR text remains above." : ""} Correct OCR mistakes, or add visible text the engine missed.`
    : "No OCR lines were detected. You may use supplied context or add a clearly visible image clue manually.";
  for (const clue of reviewedClues) renderClueCard(clue);
  if (addVisualClueButton) addVisualClueButton.disabled = busy || reviewedClues.length >= 50;
}

function nextClueId() {
  const used = new Set(reviewedClues.map(clue => clue.clue_id));
  for (let index = 1; index <= 50; index += 1) {
    const clueId = `clue-${String(index).padStart(4, "0")}`;
    if (!used.has(clueId)) return clueId;
  }
  return null;
}

if (addVisualClueButton) addVisualClueButton.addEventListener("click", () => {
  if (busy) return;
  const clueId = nextClueId();
  if (!clueId) {
    document.querySelector("#clue-status").textContent = "The 50-clue review limit has been reached.";
    addVisualClueButton.disabled = true;
    return;
  }
  const clue = {
    clue_id: clueId,
    original_text: "Reviewer-added visible image clue (not OCR)",
    corrected_text: "",
    selected: false,
    category: "other",
    source: "reviewer_added"
  };
  reviewedClues.push(clue);
  const correction = renderClueCard(clue);
  invalidatePlan();
  document.querySelector("#clue-status").textContent = "Reviewer-added clue created. Enter only text you can visibly confirm in the supplied image; it remains separate from OCR provenance.";
  addVisualClueButton.disabled = reviewedClues.length >= 50;
  correction.focus();
});


function clearPreview() {
  if (previewURL) URL.revokeObjectURL(previewURL);
  previewURL = null;
  preview.removeAttribute("src");
  preview.hidden = true;
}

function clearResults() {
  content.replaceChildren();
  rawJSON.textContent = "";
  rawDetails.hidden = true;
  rawDetails.open = false;
}

function setStatus(message, state = "idle") {
  status.textContent = message;
  status.dataset.state = state;
}

function selectedCandidates() {
  return [...candidateSelections.values()].filter(item => item.checkbox.checked).map(item => item.candidate);
}

function updateAnalyzeButton() {
  analyzeButton.disabled = busy || selectedCandidates().length === 0;
}

function updateCorrelateButton() {
  correlateButton.disabled = busy || !analyzedSources.length;
}

function setBusy(value) {
  busy = value;
  fields.disabled = value;
  resetButton.disabled = value;
  prepareButton.disabled = value;
  discoverButton.disabled = value || !preparedQueries.length;
  document.querySelector("#review-fields").disabled = value;
  if (addVisualClueButton) addVisualClueButton.disabled = value || reviewedClues.length >= 50;
  updateAnalyzeButton();
  updateCorrelateButton();
  results.setAttribute("aria-busy", String(value));
}

function appendText(tag, text) {
  const element = document.createElement(tag);
  element.textContent = text;
  content.append(element);
}

function showProperties(title, entries) {
  appendText("h3", title);
  const list = document.createElement("dl");
  for (const [label, value] of entries) {
    const term = document.createElement("dt");
    const description = document.createElement("dd");
    term.textContent = label;
    description.textContent = value ?? "Not supplied";
    list.append(term, description);
  }
  content.append(list);
}

function errorMessage(payload, response) {
  const detail = payload?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail.map(item => {
      const field = Array.isArray(item.loc) ? item.loc.filter(part => part !== "body").join(" → ") : "Input";
      return `${field}: ${item.msg || "Invalid value"}`;
    }).join("\n");
  }
  if (typeof detail?.message === "string") return detail.message;
  return `The server could not complete the request (HTTP ${response.status}).`;
}

function renderResponse(payload) {
  const context = payload.supplied_context ?? payload.context;
  if (context) showProperties("Supplied context", [
    ["Name", context.name], ["Username", context.username], ["Organization", context.organization], ["Role", context.role], ["Department", context.department]
  ]);
  if (payload.image) showProperties("Validated image", [
    ["Format", payload.image.format], ["Width", `${payload.image.width} px`],
    ["Height", `${payload.image.height} px`], ["File size", `${payload.image.byte_size} bytes`]
  ]);
  if (payload.processed_image) showProperties("Processed image", [
    ["Width", `${payload.processed_image.width} px`], ["Height", `${payload.processed_image.height} px`],
    ["Coordinates", payload.processed_image.coordinate_space]
  ]);
  if (payload.ocr_status) {
    reviewOCR(payload.extracted_text || "");
    showProperties("OCR result", [["OCR status", payload.ocr_status]]);
    appendText("h3", "Extracted text · unverified observations");
    appendText("pre", payload.extracted_text || "No text detected.");
  }
  if (payload.message) appendText("p", payload.message);
}

imageInput.addEventListener("change", () => {
  clearClues();
  clearPreview();
  clearResults();
  const file = imageInput.files[0];
  fileDetails.textContent = file ? `${file.name} · ${file.size.toLocaleString()} bytes · ${file.type || "Unknown declared type"}` : "No image selected.";
  setStatus("Ready. Submit to validate the selected image.");
  setPipelineStage(1);
  if (!file) return;
  // Preview is a convenience only; backend content validation is authoritative.
  if (["image/png", "image/jpeg"].includes(file.type)) {
    previewURL = URL.createObjectURL(file);
    preview.src = previewURL;
    preview.hidden = false;
  }
});

preview.addEventListener("error", () => {
  clearPreview();
  fileDetails.textContent += " · Preview unavailable; the server will validate the file.";
});

form.addEventListener("reset", event => {
  if (busy) { event.preventDefault(); return; }
  clearClues();
  clearPreview();
  clearResults();
  fileDetails.textContent = "No image selected.";
  setStatus("Ready. Choose an image to begin.");
  setPipelineStage(1);
});

form.addEventListener("submit", async event => {
  event.preventDefault();
  if (busy) return;
  clearResults();
  if (!imageInput.files[0]) {
    setStatus("Choose a JPEG or PNG image before submitting.", "error");
    imageInput.focus();
    return;
  }
  if (!document.querySelector("#consent").checked) {
    setStatus("Confirm that you are authorized to submit this image and context.", "error");
    document.querySelector("#consent").focus();
    return;
  }
  const ocr = event.submitter?.value === "ocr";
  if (ocr) clearClues();
  const data = new FormData(form);
  data.set("consent_confirmed", "true");
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 45000);
  setBusy(true);
  setStatus(ocr ? "Extracting image text…" : "Validating input…", "loading");
  try {
    const response = await fetch(ocr ? "/api/image-clues" : "/api/input", {
      method: "POST", body: data, signal: controller.signal
    });
    let payload;
    try { payload = await response.json(); }
    catch (error) {
      if (error.name === "AbortError") throw error;
      setStatus(`The server returned an unreadable response (HTTP ${response.status}). Try again or check the server.`, "error");
      return;
    }
    rawJSON.textContent = JSON.stringify(payload, null, 2);
    rawDetails.hidden = false;
    if (!response.ok) {
      setStatus(`Request failed (HTTP ${response.status}). ${errorMessage(payload, response)}`, "error");
      return;
    }
    if (!payload || typeof payload !== "object") {
      setStatus("The server returned an unexpected response.", "error");
      return;
    }
    setStatus(ocr ? "Request succeeded. OCR processing finished." : "Request succeeded. Input validated.", "success");
    renderResponse(payload);
    setPipelineStage(ocr ? 2 : 1);
  } catch (error) {
    setStatus(error.name === "AbortError"
      ? "Request timed out. Check the server and try again."
      : "Could not reach the server. Check that the backend is running and try again.", "error");
  } finally {
    clearTimeout(timeout);
    setBusy(false);
  }
});

window.addEventListener("pagehide", clearPreview);

for (const field of ["name", "username", "organization", "role", "department"]) {
  document.querySelector(`#${field}`).addEventListener("input", invalidatePlan);
}

function currentSearchPlanBody() {
  const suppliedContext = {};
  for (const field of ["name", "username", "organization", "role", "department"]) {
    suppliedContext[field] = document.querySelector(`#${field}`).value;
  }
  // Read live controls at submission, not the last input/change event's copy.
  const clues = reviewedClues.map(clue => {
    const { correction, select, checkbox } = clueControls.get(clue.clue_id);
    return { clue_id: clue.clue_id, original_text: clue.original_text,
      corrected_text: correction.value, category: select.value, selected: checkbox.checked };
  });
  return JSON.stringify({ supplied_context: suppliedContext, clues });
}

prepareButton.addEventListener("click", async () => {
  if (busy) return;
  invalidatePlan();
  const revision = planRevision;
  const requestBody = currentSearchPlanBody();
  const stillCurrent = () => {
    if (revision !== planRevision) return false;
    if (requestBody !== currentSearchPlanBody()) { invalidatePlan(); return false; }
    return true;
  };
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 15000);
  setBusy(true);
  planStatus.textContent = "Preparing queries locally…";
  try {
    const response = await fetch("/api/search-plan", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: requestBody,
      signal: controller.signal
    });
    const payload = await response.json();
    if (!stillCurrent()) return;
    if (!response.ok) { planStatus.textContent = errorMessage(payload, response); return; }
    preparedQueries = payload.status === "prepared" ? payload.queries : [];
    platformQueries = payload.status === "prepared" ? (payload.platform_queries || []) : [];
    preparedBody = requestBody;
    planStatus.textContent = `${payload.label}\n${payload.status}: ${payload.message}`;
    if (payload.status === "prepared") setPipelineStage(3);
    for (const [queryIndex, query] of [...payload.queries, ...platformQueries].entries()) {
      const card = document.createElement("article");
      card.className = "query query-card";
      const badge = makeBadge(`Query ${String(queryIndex + 1).padStart(2, "0")}`);
      const text = document.createElement("pre");
      text.className = "query-code";
      text.textContent = query.query;
      const reason = document.createElement("p");
      reason.className = "query-reason";
      reason.textContent = query.reason;
      const references = document.createElement("p");
      references.className = "query-provenance";
      references.textContent = "Built from · " + query.references.map(ref => ref.source === "clue"
        ? `image clue ${ref.clue_id} (${ref.field})` : `supplied ${ref.field}`).join(" · ");
      card.append(badge, text, reason, references);
      planResults.append(card);
    }
  } catch (error) {
    if (stillCurrent()) planStatus.textContent = error.name === "AbortError"
      ? "Query preparation timed out. Try again."
      : "Could not prepare queries. Check the server connection and try again.";
  } finally {
    clearTimeout(timer);
    setBusy(false);
  }
});

discoverButton.addEventListener("click", async () => {
  if (busy || !preparedQueries.length) return;
  if (preparedBody !== currentSearchPlanBody()) { invalidatePlan(); return; }
  const revision = planRevision;
  const snapshot = preparedBody;
  const current = () => {
    if (revision !== planRevision) return false;
    if (snapshot !== currentSearchPlanBody()) { invalidatePlan(); return false; }
    return true;
  };
  clearDiscovery();
  discoveryStatus.textContent = "Searching Tavily for candidate public sources…";
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 95000);
  setBusy(true);
  try {
    const response = await fetch("/api/discover", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ queries: preparedQueries, ...(platformQueries.length ? { platform_queries: platformQueries } : {}) }), signal: controller.signal
    });
    const payload = await response.json();
    if (!current()) return;
    if (!response.ok && !payload.issues) {
      discoveryStatus.textContent = errorMessage(payload, response);
      return;
    }
    discoveryStatus.textContent = `${payload.status}: ${payload.message}\n${payload.candidates.length} candidate source(s).`;
    if (payload.candidates.length) setPipelineStage(4);
    for (const issue of payload.issues) {
      const error = document.createElement("p");
      error.textContent = `${issue.code}: ${issue.message} Query: ${issue.query}`;
      discoveryResults.append(error);
    }
    for (const candidate of payload.candidates) {
      // Provider content is untrusted; render text and allow only web links.
      let url;
      try { url = new URL(candidate.url); } catch { continue; }
      if (!["http:", "https:"].includes(url.protocol) || url.username || url.password) continue;
      const index = discoveredCandidates.length;
      discoveredCandidates.push(candidate);

      const card = document.createElement("article");
      card.className = "query source-card";
      const title = safeWebLink(candidate.url, candidate.title || candidate.domain, "source-title");
      const address = document.createElement("p");
      address.className = "source-url";
      address.textContent = candidate.url;
      const snippet = document.createElement("p");
      snippet.className = "source-snippet";
      snippet.textContent = candidate.snippet || "No provider snippet available.";

      const metadata = document.createElement("div");
      metadata.className = "source-meta";
      const domainChip = document.createElement("span");
      domainChip.className = "meta-chip";
      domainChip.textContent = candidate.domain || sourceLabel(candidate.url);
      const typeChip = document.createElement("span");
      typeChip.className = "meta-chip";
      typeChip.textContent = prettyStatus(candidate.platform || candidate.source_type);
      const rankChip = document.createElement("span");
      rankChip.className = "meta-chip";
      rankChip.textContent = `${candidate.provider} · Best provider rank: ${candidate.rank}`;
      metadata.append(domainChip, typeChip, rankChip, makeBadge("Unverified candidate", "unverified"));

      const provenance = document.createElement("p");
      provenance.className = "source-provenance";
      provenance.textContent = "Discovered by: " + candidate.discovered_by.map(query => query.query).join("; ");

      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.id = `candidate-${index}`;
      const selection = document.createElement("label");
      selection.className = "consent source-select";
      selection.htmlFor = checkbox.id;
      const selectionText = document.createElement("span");
      selectionText.textContent = "Add this source to evidence analysis";
      selection.append(checkbox, selectionText);
      candidateSelections.set(index, { candidate, checkbox });
      checkbox.addEventListener("change", () => {
        if (selectedCandidates().length > 3) {
          checkbox.checked = false;
          analysisStatus.textContent = "Select at most three sources for one analysis request.";
        } else {
          clearAnalysis(selectedCandidates().length
            ? `${selectedCandidates().length} source(s) selected. Ready to analyze.`
            : "Select up to three candidate sources to analyze.");
        }
        updateAnalyzeButton();
      });

      if (title) card.append(title);
      card.append(address, snippet, metadata, provenance, selection);
      discoveryResults.append(card);
    }
    updateAnalyzeButton();
  } catch (error) {
    if (current()) discoveryStatus.textContent = error.name === "AbortError"
      ? "Discovery timed out. Some provider requests may have used quota."
      : "Discovery could not complete. Check the server connection and try again.";
  } finally {
    clearTimeout(timer);
    setBusy(false);
  }
});

analyzeButton.addEventListener("click", async () => {
  if (busy) return;
  const candidates = selectedCandidates();
  if (!candidates.length || candidates.length > 3) return;
  const revision = analysisRevision;
  const snapshot = JSON.stringify(candidates.map(candidate => candidate.url));
  const current = () => revision === analysisRevision
    && snapshot === JSON.stringify(selectedCandidates().map(candidate => candidate.url));
  analyzedSources = [];
  analysisResults.replaceChildren();
  clearCorrelation("Source analysis is running. Correlation will be available after analyzed records return.");
  analysisStatus.textContent = "Reading selected public sources and extracting source observations…";
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 45000);
  setBusy(true);
  try {
    const seed = JSON.parse(currentSearchPlanBody());
    const response = await fetch("/api/analyze-sources", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ candidates, supplied_context: seed.supplied_context, clues: seed.clues }), signal: controller.signal
    });
    const payload = await response.json();
    if (!current()) return;
    if (!response.ok) {
      analysisStatus.textContent = errorMessage(payload, response);
      return;
    }
    analyzedSources = Array.isArray(payload.sources) ? payload.sources : [];
    analysisStatus.textContent = `${payload.status}: ${payload.message}`;
    if (analyzedSources.length) setPipelineStage(5);
    for (const source of analyzedSources) {
      const card = document.createElement("article");
      card.className = "query analysis-card";

      const head = document.createElement("div");
      head.className = "analysis-card-head";
      const titleBlock = document.createElement("div");
      const heading = document.createElement("h4");
      heading.textContent = source.page_title || source.candidate.title || source.candidate.domain;
      const readState = document.createElement("p");
      readState.className = "read-state";
      readState.textContent = `${prettyStatus(source.profile?.platform || source.candidate.platform || "other")} / ${source.profile?.access_status || source.status} ? Source read: ${prettyStatus(source.status)} · source observations — not identity-verified`;
      titleBlock.append(heading, readState);
      head.append(titleBlock, makeBadge(prettyStatus(source.status), source.status === "analyzed" ? "supported" : "insufficient"));
      card.append(head);

      if (source.final_url) {
        const finalLink = safeWebLink(source.final_url, source.final_url, "source-url");
        if (finalLink) card.append(finalLink);
      }
      if (source.meta_description) {
        const description = document.createElement("p");
        description.className = "analysis-description";
        description.textContent = source.meta_description;
        card.append(description);
      }
      if (source.issue) {
        const issue = document.createElement("div");
        issue.className = "issue-box";
        issue.textContent = `${source.issue.code}: ${source.issue.message}`;
        card.append(issue);
      }

      const observationSection = document.createElement("section");
      observationSection.className = "observation-section";
      const observationTitle = document.createElement("div");
      observationTitle.className = "observation-section-title";
      const observationLabel = document.createElement("span");
      observationLabel.textContent = "Identity-relevant observations";
      const observationCount = document.createElement("span");
      observationCount.textContent = `${source.observations?.length || 0} extracted`;
      observationTitle.append(observationLabel, observationCount);
      observationSection.append(observationTitle);

      if (source.observations?.length) {
        for (const observation of source.observations) {
          const row = document.createElement("div");
          row.className = "observation-row";
          const value = document.createElement("div");
          value.className = "observation-value";
          const field = document.createElement("span");
          field.textContent = observation.field;
          const actualValue = document.createElement("strong");
          actualValue.textContent = observation.value;
          value.append(field, actualValue);

          const evidence = document.createElement("div");
          evidence.className = "evidence-box";
          const method = document.createElement("code");
          method.textContent = `EVIDENCE · ${prettyStatus(observation.extraction_method)}`;
          const excerpt = document.createElement("span");
          excerpt.textContent = observation.evidence;
          evidence.append(method, excerpt);
          row.append(value, evidence);
          observationSection.append(row);
        }
      } else if (source.status === "analyzed") {
        const none = document.createElement("p");
        none.className = "hint";
        none.textContent = "No structured subject fields were supported strongly enough to extract from this page.";
        observationSection.append(none);
      }
      card.append(observationSection);

      const attribution = source.page_attribution || {};
      const attributionItems = [
        ...(attribution.authors || []).map(value => `Page author: ${value}`),
        ...(attribution.publishers || []).map(value => `Publisher/site owner: ${value}`),
        ...(attribution.site_social_links || []).map(value => `Site social link: ${value}`)
      ];
      if (attributionItems.length) {
        const details = document.createElement("details");
        details.className = "attribution-details";
        const summary = document.createElement("summary");
        summary.textContent = "Page attribution · excluded from subject identity by default";
        details.append(summary);
        for (const item of attributionItems) {
          const row = document.createElement("p");
          row.textContent = item;
          details.append(row);
        }
        card.append(details);
      }
      if (source.content_excerpt) {
        const details = document.createElement("details");
        const summary = document.createElement("summary");
        summary.textContent = "Readable source excerpt";
        const excerpt = document.createElement("pre");
        excerpt.textContent = source.content_excerpt;
        details.append(summary, excerpt);
        card.append(details);
      }
      analysisResults.append(card);
    }
    correlationStatus.textContent = analyzedSources.some(source => source.status === "analyzed")
      ? "Analyzed source records are ready for evidence correlation."
      : "Sources were inaccessible. Build the report to review indexed candidates and missing evidence.";
    updateCorrelateButton();
  } catch (error) {
    if (current()) analysisStatus.textContent = error.name === "AbortError"
      ? "Source analysis timed out. Try fewer or different candidate pages."
      : "Source analysis could not complete. Check the server connection and try again.";
  } finally {
    clearTimeout(timer);
    setBusy(false);
  }
});


correlateButton.addEventListener("click", async () => {
  if (busy) return;
  const sources = analyzedSources;
  if (!sources.length) return;
  const seed = JSON.parse(currentSearchPlanBody());
  const revision = correlationRevision;
  const snapshot = currentSearchPlanBody();
  const current = () => revision === correlationRevision && snapshot === currentSearchPlanBody();
  correlationResults.replaceChildren();
  correlationStatus.textContent = "Comparing analyzed records with the seed identity and source evidence…";
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 20000);
  setBusy(true);
  try {
    const response = await fetch("/api/correlate", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ supplied_context: seed.supplied_context, clues: seed.clues, sources, discovered_candidates: discoveredCandidates }),
      signal: controller.signal
    });
    const payload = await response.json();
    if (!current()) return;
    if (!response.ok) {
      correlationStatus.textContent = errorMessage(payload, response);
      return;
    }
    correlationStatus.textContent = `${payload.status}: ${payload.message}`;
    if (payload.status === "completed") setPipelineStage(6);

    if (payload.report && typeof renderIdentityReport === "function") {
      renderIdentityReport(correlationResults, payload.report);
      return;
    }
    const assessments = Array.isArray(payload.assessments) ? payload.assessments : [];
    const seedProfile = Array.isArray(payload.seed_profile) ? payload.seed_profile : [];
    const crossSupport = Array.isArray(payload.cross_source_support) ? payload.cross_source_support : [];
    const identitySignal = seedProfile.find(item => item.field === "name")
      || seedProfile.find(item => item.field === "username") || seedProfile[0];
    const identityLabel = identitySignal?.value || "Seed identity";
    const matchedSignals = assessments.reduce((count, item) => count + item.comparisons.filter(c => c.status === "match").length, 0);
    const conflictSignals = assessments.reduce((count, item) => count + item.comparisons.filter(c => c.status === "conflict").length, 0);
    const unresolvedSignals = assessments.reduce((count, item) => count + item.comparisons.filter(c => ["missing", "no_support"].includes(c.status)).length, 0);
    const supportedSources = assessments.filter(item => item.status === "supported").length;
    const uncertainSources = assessments.filter(item => item.status === "uncertain").length;
    let overallLabel = "Insufficient Evidence";
    if (conflictSignals && supportedSources) overallLabel = "Mixed Evidence";
    else if (conflictSignals) overallLabel = "Conflicting Evidence";
    else if (supportedSources) overallLabel = "Supported Association";
    else if (uncertainSources) overallLabel = "Uncertain Association";

    const overview = document.createElement("section");
    overview.className = "assessment-overview";
    const summary = document.createElement("div");
    summary.className = "summary-card";
    const summaryLabel = document.createElement("span");
    summaryLabel.className = "summary-label";
    summaryLabel.textContent = "Identity assessment";
    const summaryIdentity = document.createElement("div");
    summaryIdentity.className = "summary-identity";
    summaryIdentity.textContent = identityLabel;
    const summaryStatus = document.createElement("span");
    summaryStatus.className = "summary-status";
    summaryStatus.textContent = overallLabel;
    summary.append(summaryLabel, summaryIdentity, summaryStatus);
    overview.append(summary);

    const metricData = [
      ["Analyzed sources", assessments.length, `${supportedSources} supported`],
      ["Supporting signals", matchedSignals, "field-level matches"],
      ["Conflicts", conflictSignals, conflictSignals ? "requires review" : "none detected"],
      ["Corroborations", crossSupport.length, `${unresolvedSignals} missing / unsupported`],
    ];
    for (const [label, value, note] of metricData) {
      const metric = document.createElement("div");
      metric.className = "metric-card";
      const metricLabel = document.createElement("small");
      metricLabel.textContent = label;
      const metricValue = document.createElement("strong");
      metricValue.textContent = String(value);
      const metricNote = document.createElement("span");
      metricNote.textContent = note;
      metric.append(metricLabel, metricValue, metricNote);
      overview.append(metric);
    }
    correlationResults.append(overview);

    const correlationLayout = document.createElement("div");
    correlationLayout.className = "correlation-layout";

    const matrixCard = document.createElement("section");
    matrixCard.className = "evidence-matrix-card";
    const matrixEyebrow = document.createElement("div");
    matrixEyebrow.className = "card-eyebrow";
    matrixEyebrow.textContent = "Signal-by-source comparison";
    const matrixTitle = document.createElement("h4");
    matrixTitle.className = "card-title";
    matrixTitle.textContent = "Evidence matrix";
    const matrixCopy = document.createElement("p");
    matrixCopy.className = "card-copy";
    matrixCopy.textContent = "Match, conflict, missing, and non-support are kept distinct so absence is never treated as contradiction.";
    matrixCard.append(matrixEyebrow, matrixTitle, matrixCopy);

    const matrixScroll = document.createElement("div");
    matrixScroll.className = "matrix-scroll";
    const table = document.createElement("table");
    table.className = "evidence-matrix";
    const thead = document.createElement("thead");
    const headerRow = document.createElement("tr");
    const signalHeader = document.createElement("th");
    signalHeader.textContent = "Seed signal";
    headerRow.append(signalHeader);
    for (const assessment of assessments) {
      const th = document.createElement("th");
      th.textContent = assessment.page_title || sourceLabel(assessment.source_url);
      headerRow.append(th);
    }
    thead.append(headerRow);
    table.append(thead);
    const tbody = document.createElement("tbody");
    for (const signal of seedProfile) {
      const row = document.createElement("tr");
      const label = document.createElement("td");
      label.textContent = `${signal.field} · ${signal.value}`;
      row.append(label);
      for (const assessment of assessments) {
        const comparison = assessment.comparisons.find(item => item.field === signal.field && item.seed_value === signal.value)
          || assessment.comparisons.find(item => item.field === signal.field);
        const cell = document.createElement("td");
        const state = document.createElement("span");
        const stateName = comparison?.status || "missing";
        state.className = `matrix-state ${stateName}`;
        state.textContent = prettyStatus(stateName);
        cell.append(state);
        row.append(cell);
      }
      tbody.append(row);
    }
    table.append(tbody);
    matrixScroll.append(table);
    matrixCard.append(matrixScroll);
    correlationLayout.append(matrixCard);

    const mapCard = document.createElement("section");
    mapCard.className = "nexus-map-card";
    const mapEyebrow = document.createElement("div");
    mapEyebrow.className = "card-eyebrow";
    mapEyebrow.textContent = "Evidence network snapshot";
    const mapTitle = document.createElement("h4");
    mapTitle.className = "card-title";
    mapTitle.textContent = "How the analyzed records connect";
    const mapCopy = document.createElement("p");
    mapCopy.className = "card-copy";
    mapCopy.textContent = "A compact view of candidate records around the seed identity. This is a visual summary, not a separate verification engine.";
    const map = document.createElement("div");
    map.className = "nexus-map";
    const core = document.createElement("div");
    core.className = "nexus-core";
    const coreLabel = document.createElement("small");
    coreLabel.textContent = "Seed identity";
    const coreValue = document.createElement("strong");
    coreValue.textContent = identityLabel;
    core.append(coreLabel, coreValue);
    map.append(core);
    assessments.slice(0, 3).forEach((assessment, index) => {
      const node = document.createElement("div");
      node.className = `nexus-source pos-${index} ${assessment.status}`;
      node.textContent = `${sourceLabel(assessment.source_url, assessment.page_title)} · ${prettyStatus(assessment.status)}`;
      map.append(node);
    });
    mapCard.append(mapEyebrow, mapTitle, mapCopy, map);
    correlationLayout.append(mapCard);
    correlationResults.append(correlationLayout);

    const seedCard = document.createElement("section");
    seedCard.className = "seed-card";
    const seedEyebrow = document.createElement("div");
    seedEyebrow.className = "card-eyebrow";
    seedEyebrow.textContent = "Investigation baseline";
    const seedHeading = document.createElement("h4");
    seedHeading.className = "card-title";
    seedHeading.textContent = "Seed Identity Profile";
    const seedCopy = document.createElement("p");
    seedCopy.className = "card-copy";
    seedCopy.textContent = "Only supplied context and explicitly selected image clues are shown here.";
    const seedSignals = document.createElement("div");
    seedSignals.className = "seed-signals";
    if (seedProfile.length) {
      for (const signal of seedProfile) {
        const chip = document.createElement("span");
        chip.className = "seed-chip";
        const origins = signal.references.map(ref => ref.source === "image_clue" ? ref.clue_id : "supplied context").join(", ");
        chip.textContent = `${prettyStatus(signal.field)}: ${signal.value} · ${origins}`;
        seedSignals.append(chip);
      }
    } else {
      const none = document.createElement("span");
      none.className = "seed-chip";
      none.textContent = "No usable seed identity signals.";
      seedSignals.append(none);
    }
    seedCard.append(seedEyebrow, seedHeading, seedCopy, seedSignals);
    correlationResults.append(seedCard);

    const cross = document.createElement("section");
    cross.className = "corroboration-card";
    const crossEyebrow = document.createElement("div");
    crossEyebrow.className = "card-eyebrow";
    crossEyebrow.textContent = "Repeated observations";
    const crossHeading = document.createElement("h4");
    crossHeading.className = "card-title";
    crossHeading.textContent = "Cross-source corroboration";
    const crossCopy = document.createElement("p");
    crossCopy.className = "card-copy";
    crossCopy.textContent = crossSupport.length
      ? "The same subject value appeared in at least two analyzed public sources."
      : "No subject value was repeated across two analyzed sources in this run.";
    cross.append(crossEyebrow, crossHeading, crossCopy);
    if (crossSupport.length) {
      const list = document.createElement("div");
      list.className = "corroboration-list";
      for (const item of crossSupport) {
        const row = document.createElement("div");
        row.className = "corroboration-item";
        const icon = document.createElement("span");
        icon.className = "corroboration-icon";
        icon.textContent = "✓";
        const main = document.createElement("div");
        main.className = "corroboration-main";
        const label = document.createElement("strong");
        label.textContent = item.field;
        const value = document.createElement("span");
        value.textContent = item.value;
        main.append(label, value);
        const count = document.createElement("span");
        count.className = "corroboration-count";
        count.textContent = `${item.source_urls.length} sources`;
        row.append(icon, main, count);
        list.append(row);
      }
      cross.append(list);
    }
    correlationResults.append(cross);

    const assessmentsGrid = document.createElement("section");
    assessmentsGrid.className = "assessments-grid";
    for (const assessment of assessments) {
      const card = document.createElement("article");
      card.className = `assessment-card ${assessment.status}`;
      const head = document.createElement("div");
      head.className = "assessment-head";
      const headingBlock = document.createElement("div");
      const heading = document.createElement("h4");
      heading.textContent = assessment.page_title || sourceLabel(assessment.source_url);
      const url = document.createElement("p");
      url.className = "assessment-url";
      url.textContent = assessment.source_url;
      headingBlock.append(heading, url);
      const state = makeBadge(`Assessment: ${assessment.status.toUpperCase()}`, assessment.status);
      head.append(headingBlock, state);

      const rationale = document.createElement("p");
      rationale.className = "assessment-rationale";
      rationale.textContent = assessment.rationale;
      const signalList = document.createElement("div");
      signalList.className = "signal-list";

      for (const comparison of assessment.comparisons) {
        const block = document.createElement("div");
        block.className = "signal-block";
        const signalSummary = document.createElement("div");
        signalSummary.className = "signal-summary";
        const symbol = document.createElement("span");
        symbol.className = `signal-symbol ${comparison.status}`;
        symbol.textContent = comparison.status === "match" ? "✓" : comparison.status === "conflict" ? "×" : comparison.status === "no_support" ? "~" : "—";
        const name = document.createElement("div");
        name.className = "signal-name";
        const field = document.createElement("strong");
        field.textContent = comparison.field;
        const seedValue = document.createElement("span");
        seedValue.textContent = `Seed: ${comparison.seed_value}`;
        name.append(field, seedValue);
        const comparisonBadge = makeBadge(prettyStatus(comparison.status), comparison.status);
        signalSummary.append(symbol, name, comparisonBadge);

        const details = document.createElement("div");
        details.className = "signal-details";
        const explanation = document.createElement("div");
        explanation.textContent = comparison.explanation;
        details.append(explanation);
        for (const evidence of comparison.evidence || []) {
          const evidenceBox = document.createElement("div");
          evidenceBox.className = "signal-evidence";
          const evidenceValue = document.createElement("strong");
          evidenceValue.textContent = `Source evidence · ${evidence.value}`;
          const evidenceText = document.createElement("div");
          evidenceText.textContent = evidence.evidence;
          evidenceBox.append(evidenceValue, evidenceText);
          const evidenceLink = safeWebLink(evidence.source_url, sourceLabel(evidence.source_url));
          if (evidenceLink) evidenceBox.append(evidenceLink);
          details.append(evidenceBox);
        }
        block.append(signalSummary, details);
        signalList.append(block);
      }
      card.append(head, rationale, signalList);
      assessmentsGrid.append(card);
    }
    correlationResults.append(assessmentsGrid);
  } catch (error) {
    if (current()) correlationStatus.textContent = error.name === "AbortError"
      ? "Correlation timed out. Try again."
      : "Correlation could not complete. Check the server connection and try again.";
  } finally {
    clearTimeout(timer);
    setBusy(false);
    updateCorrelateButton();
  }
});

// Enable submission only after all handlers have attached successfully.
document.querySelector("#validate").disabled = false;
document.querySelector("#extract").disabled = false;
prepareButton.disabled = false;
if (addVisualClueButton) addVisualClueButton.disabled = false;
setPipelineStage(1);
setStatus("Ready. Choose an image to begin.");
