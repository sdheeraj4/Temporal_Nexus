// Lightweight DOM/network simulations; these are not real browser tests.
const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../frontend/app.js"), "utf8");
const html = fs.readFileSync(path.join(__dirname, "../frontend/index.html"), "utf8");

class Element {
  constructor() { this.events = {}; this.children = []; this.files = []; this.checked = false; this.hidden = false; this.dataset = {}; this.attributes = {}; this._text = ""; }
  addEventListener(name, handler) { this.events[name] = handler; }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(child => child.textContent).join(""); }
  set innerHTML(value) { throw new Error("Unsafe rendering attempted"); }
  append(...children) { this.children.push(...children); }
  replaceChildren() { this.children = []; this._text = ""; }
  removeAttribute(name) { delete this.attributes[name]; }
  setAttribute(name, value) { this.attributes[name] = value; }
  focus() { this.focused = true; }
}

function fixture(fetchImpl) {
  const elements = new Map();
  const get = selector => {
    if (!elements.has(selector)) elements.set(selector, new Element());
    return elements.get(selector);
  };
  const calls = [], revoked = [];
  let sequence = 0;
  class FormDataMock {
    constructor() { this.values = new Map(); }
    set(name, value) { this.values.set(name, value); }
    get(name) { return this.values.get(name); }
  }
  vm.runInNewContext(source, {
    document: { querySelector: get, createElement: () => new Element() },
    window: { addEventListener() {} },
    URL: class extends URL {
      static createObjectURL() { return `blob:${++sequence}`; }
      static revokeObjectURL(url) { revoked.push(url); }
    },
    FormData: FormDataMock, AbortController, setTimeout, clearTimeout,
    fetch: async (...args) => { calls.push(args); return fetchImpl(...args); }
  });
  get("#image").files = [{ name: "demo.png", size: 100, type: "image/png" }];
  get("#consent").checked = true;
  const submit = (value = "validate") => get("#input-form").events.submit({ preventDefault() {}, submitter: { value } });
  return { get, submit, calls, revoked };
}

const response = (status, payload) => ({ status, ok: status >= 200 && status < 300, json: async () => payload });

test("form fails closed when the script is unavailable and enables after initialization", () => {
  assert.match(html, /<form[^>]+onsubmit="return false;"/);
  assert.match(html, /<button id="validate"[^>]+disabled/);
  assert.match(html, /<button id="extract"[^>]+disabled/);
  assert.match(html, /Open this page through FastAPI/);
  const ui = fixture(() => { throw new Error("No startup request expected"); });
  assert.equal(ui.get("#validate").disabled, false);
  assert.equal(ui.get("#extract").disabled, false);
  assert.equal(typeof ui.get("#input-form").events.submit, "function");
});

test("submission cancels default navigation and preserves the selected image and fields", async () => {
  const ui = fixture(async () => response(200, { status: "validated" }));
  const selectedImage = ui.get("#image").files[0];
  ui.get("#name").value = "Demo";
  let prevented = false;
  await ui.get("#input-form").events.submit({
    preventDefault() { prevented = true; }, submitter: { value: "validate" }
  });
  assert.equal(prevented, true);
  assert.equal(ui.calls.length, 1);
  assert.equal(ui.get("#image").files[0], selectedImage);
  assert.equal(ui.get("#name").value, "Demo");
});

test("valid submission uses relative URL and browser multipart boundary; renders text safely", async () => {
  const markup = "<img src=x onerror=alert(1)>";
  const ui = fixture(async () => response(200, {
    context: { name: markup }, image: { format: "PNG", width: 16, height: 12, byte_size: 100 },
    message: "Input validated. OCR and identity correlation have not run."
  }));
  await ui.submit();
  assert.equal(ui.calls[0][0], "/api/input");
  assert.equal(ui.calls[0][1].headers, undefined);
  assert.equal(ui.calls[0][1].body.get("consent_confirmed"), "true");
  assert.match(ui.get("#result-content").textContent, /<img src=x onerror=alert\(1\)>/);
  assert.equal(ui.get("#request-status").dataset.state, "success");
  assert.equal(ui.get("#raw-details").hidden, false);
});

test("unchecked consent and missing image reject before sending", async () => {
  const ui = fixture(() => { throw new Error("Must not send"); });
  ui.get("#consent").checked = false;
  await ui.submit();
  assert.match(ui.get("#request-status").textContent, /authorized/);
  ui.get("#image").files = [];
  await ui.submit();
  assert.match(ui.get("#request-status").textContent, /Choose a JPEG or PNG/);
  assert.equal(ui.calls.length, 0);
});

test("backend validation errors and OCR unavailable errors are shown", async () => {
  for (const [code, payload, expected] of [
    [422, { detail: [{ loc: ["body", "name"], msg: "Too long" }] }, /name: Too long/],
    [413, { detail: "Image file must not exceed 5 MiB." }, /5 MiB/],
    [503, { detail: { code: "ocr_engine_missing", message: "Install local Tesseract." } }, /Install local Tesseract/]
  ]) {
    const ui = fixture(async () => response(code, payload));
    await ui.submit("ocr");
    assert.equal(ui.calls[0][0], "/api/image-clues");
    assert.equal(ui.get("#request-status").dataset.state, "error");
    assert.match(ui.get("#request-status").textContent, expected);
    assert.equal(ui.get("#result-content").textContent, "");
  }
});

test("OCR observations remain separate from supplied context", async () => {
  const ui = fixture(async () => response(200, {
    supplied_context: { name: "Supplied Only" }, ocr_status: "completed", extracted_text: "NEXUS\nDEMO",
    processed_image: { width: 20, height: 30, coordinate_space: "Pixels" }
  }));
  await ui.submit("ocr");
  assert.match(ui.get("#result-content").textContent, /Supplied context.*Supplied Only/);
  assert.match(ui.get("#result-content").textContent, /unverified observationsNEXUS\nDEMO/);
});

test("loading prevents duplicate submissions and disables controls", async () => {
  let finish;
  const ui = fixture(() => new Promise(resolve => { finish = resolve; }));
  const pending = ui.submit();
  assert.equal(ui.get("#input-fields").disabled, true);
  assert.equal(ui.get("#reset").disabled, true);
  await ui.submit();
  assert.equal(ui.calls.length, 1);
  finish(response(200, { status: "validated" }));
  await pending;
  assert.equal(ui.get("#input-fields").disabled, false);
  assert.equal(ui.get("#reset").disabled, false);
});

test("preview URLs revoked on replacement and reset; result state cleared", async () => {
  const ui = fixture(async () => response(200, { status: "validated" }));
  ui.get("#image").events.change();
  ui.get("#image").events.change();
  assert.deepEqual(ui.revoked, ["blob:1"]);
  await ui.submit();
  ui.get("#input-form").events.reset({ preventDefault() {} });
  assert.deepEqual(ui.revoked, ["blob:1", "blob:2"]);
  assert.equal(ui.get("#preview").hidden, true);
  assert.equal(ui.get("#raw-details").hidden, true);
  assert.equal(ui.get("#result-content").textContent, "");
  assert.match(ui.get("#request-status").textContent, /Choose an image/);
});

test("network and client timeout failures allow retry", async () => {
  for (const name of ["TypeError", "AbortError"]) {
    const ui = fixture(async () => { const error = new Error(); error.name = name; throw error; });
    await ui.submit();
    assert.equal(ui.get("#request-status").dataset.state, "error");
    assert.match(ui.get("#request-status").textContent, name === "AbortError" ? /timed out/ : /Could not reach/);
    assert.equal(ui.get("#input-fields").disabled, false);
  }
});

test("non-JSON server failures are reported without displaying HTML", async () => {
  const ui = fixture(async () => ({ status: 500, ok: false, json: async () => { throw new SyntaxError(); } }));
  await ui.submit();
  assert.match(ui.get("#request-status").textContent, /unreadable response \(HTTP 500\)/);
  assert.equal(ui.get("#raw-details").hidden, true);
});

test("review defaults, correction provenance, stale plans, and reset", async () => {
  let submitted;
  const ui = fixture(async (url, options) => {
    if (url === "/api/image-clues") return response(200, { ocr_status: "completed", extracted_text: "Nexvs Summit\nAlex Demo" });
    submitted = JSON.parse(options.body);
    return response(200, { label: "Prepared queries — no search performed.", status: "prepared", message: "Local only", queries: [
      { query: '"Alex Demo" "Nexus Summit"', reason: "Event", references: [{ source: "clue", clue_id: "clue-0001", field: "corrected_text" }] }
    ] });
  });
  await ui.submit("ocr");
  const card = ui.get("#clue-list").children[0];
  const original = card.children[1], correction = card.children[3], category = card.children[5], selection = card.children[6].children[0];
  assert.equal(selection.checked, false);
  assert.equal(category.children[0].value, "other");
  correction.value = "Nexus Summit";
  correction.events.input();
  category.value = "event";
  category.events.change();
  selection.checked = true;
  selection.events.change();
  ui.get("#name").value = "Alex Demo";
  await ui.get("#prepare").events.click();
  assert.equal(submitted.clues[0].clue_id, "clue-0001");
  assert.equal(submitted.clues[0].original_text, "Nexvs Summit");
  assert.equal(submitted.clues[0].corrected_text, "Nexus Summit");
  assert.equal(submitted.clues[0].selected, true);
  assert.match(original.textContent, /Nexvs Summit/);
  assert.equal(ui.get("#query-results").children.length, 1);
  for (const changed of [() => correction.events.input(), () => category.events.change(), () => selection.events.change(), () => ui.get("#name").events.input()]) {
    await ui.get("#prepare").events.click();
    changed();
    assert.equal(ui.get("#query-results").children.length, 0);
  }
  ui.get("#input-form").events.reset({ preventDefault() {} });
  assert.equal(ui.get("#clue-list").children.length, 0);
  assert.equal(ui.get("#query-results").children.length, 0);
});

test("image replacement clears reviewed clues and plans", async () => {
  const ui = fixture(async () => response(200, { ocr_status: "completed", extracted_text: "Demo" }));
  await ui.submit("ocr");
  assert.equal(ui.get("#clue-list").children.length, 1);
  ui.get("#image").events.change();
  assert.equal(ui.get("#clue-list").children.length, 0);
  assert.equal(ui.get("#query-results").children.length, 0);
});

test("query preparation ignores stale in-flight response after context changes", async () => {
  let finish;
  const ui = fixture(() => new Promise(resolve => { finish = resolve; }));
  const pending = ui.get("#prepare").events.click();
  ui.get("#name").events.input();
  finish(response(200, { label: "Old", status: "prepared", message: "Old", queries: [{ query: "old" }] }));
  await pending;
  assert.equal(ui.get("#query-results").children.length, 0);
  assert.match(ui.get("#plan-status").textContent, /regenerate/);
  assert.equal(ui.get("#prepare").disabled, false);
});

test("submission snapshots live corrected text, category and selection without relying on events", async () => {
  let submitted;
  const ui = fixture(async (url, options) => {
    if (url === "/api/image-clues") return response(200, { ocr_status: "completed", extracted_text: "Al3x Demo\nNexvs Summit\nNEXUS SUMMIT" });
    submitted = JSON.parse(options.body);
    return response(200, { status: "prepared", label: "Local", message: "Prepared", queries: [] });
  });
  await ui.submit("ocr");
  const cards = ui.get("#clue-list").children;
  cards[0].children[3].value = "Alex Demo";
  cards[0].children[5].value = "name";
  cards[0].children[6].children[0].checked = true;
  cards[1].children[5].value = "organization";
  cards[1].children[5].events.change();
  cards[1].children[3].value = "Nexus Summit";
  cards[1].children[5].value = "event";
  cards[1].children[6].children[0].checked = true;
  cards[2].children[5].value = "event";
  cards[2].children[6].children[0].checked = true;
  await ui.get("#prepare").events.click();
  assert.equal(submitted.clues[0].corrected_text, "Alex Demo");
  assert.equal(submitted.clues[1].corrected_text, "Nexus Summit");
  assert.equal(submitted.clues[1].category, "event");
  assert.equal(submitted.clues[2].selected, true);
  assert.equal(submitted.clues[0].original_text, "Al3x Demo");
  assert.equal(submitted.clues[1].original_text, "Nexvs Summit");
  cards[2].children[6].children[0].checked = false;
  await ui.get("#prepare").events.click();
  assert.equal(submitted.clues[2].selected, false);
});

test("late plan responses cannot restore values edited without an input event", async () => {
  let finish;
  const ui = fixture(async url => {
    if (url === "/api/image-clues") return response(200, { ocr_status: "completed", extracted_text: "Alex" });
    return new Promise(resolve => { finish = resolve; });
  });
  await ui.submit("ocr");
  const pending = ui.get("#prepare").events.click();
  ui.get("#clue-list").children[0].children[3].value = "Updated";
  finish(response(200, { status: "prepared", label: "Old", queries: [{ query: "old" }] }));
  await pending;
  assert.equal(ui.get("#query-results").children.length, 0);
  assert.match(ui.get("#plan-status").textContent, /regenerate/);
});

const discoveryQuery = { query: '"Alex Demo"', reason: "Identity query", references: [] };
const preparedResponse = { status: "prepared", label: "Prepared", message: "Local", queries: [discoveryQuery] };

test("discovery sends only prepared queries and safely renders candidates with provenance", async () => {
  const ui = fixture(async (url, options) => {
    if (url === "/api/search-plan") return response(200, preparedResponse);
    assert.equal(url, "/api/discover");
    assert.deepEqual(JSON.parse(options.body), { queries: [discoveryQuery] });
    return response(200, { status: "completed", message: "Candidate sources only", issues: [], candidates: [
      { title: "<script>unsafe</script>", url: "https://example.org/", snippet: "<img src=x>", domain: "example.org", source_type: "web_page", provider: "tavily", rank: 1, discovered_by: [discoveryQuery] },
      { title: "Unsafe link", url: "javascript:alert(1)" }
    ] });
  });
  await ui.get("#prepare").events.click();
  assert.equal(ui.get("#discover").disabled, false);
  await ui.get("#discover").events.click();
  const cards = ui.get("#discovery-results").children;
  assert.equal(cards.length, 1);
  assert.equal(cards[0].children[0].textContent, "<script>unsafe</script>");
  assert.equal(cards[0].children[0].rel, "noopener noreferrer");
  assert.match(cards[0].textContent, /Unverified candidate/);
  assert.match(cards[0].textContent, /tavily/);
  assert.match(cards[0].textContent, /Best provider rank: 1/);
  assert.match(cards[0].textContent, /Alex Demo/);
  ui.get("#name").events.input();
  assert.equal(ui.get("#discovery-results").children.length, 0);
  assert.equal(ui.get("#discover").disabled, true);
});

test("discovery missing key and no-results do not fabricate candidates", async () => {
  for (const result of [response(503, { detail: { message: "Set TAVILY_API_KEY" } }),
    response(200, { status: "no_results", message: "No candidate sources", issues: [], candidates: [] })]) {
    const ui = fixture(async url => url === "/api/search-plan" ? response(200, preparedResponse) : result);
    await ui.get("#prepare").events.click();
    await ui.get("#discover").events.click();
    assert.match(ui.get("#discovery-status").textContent, /TAVILY_API_KEY|no_results/);
    assert.equal(ui.get("#discovery-results").children.length, 0);
    assert.equal(ui.get("#discover").disabled, false);
  }
});

test("discovery ignores late responses for edited plans and prevents duplicate requests", async () => {
  let finish;
  const ui = fixture(async url => url === "/api/search-plan" ? response(200, preparedResponse) : new Promise(resolve => { finish = resolve; }));
  await ui.get("#prepare").events.click();
  const pending = ui.get("#discover").events.click();
  await ui.get("#discover").events.click();
  assert.equal(ui.calls.filter(([url]) => url === "/api/discover").length, 1);
  ui.get("#name").events.input();
  finish(response(200, { status: "completed", candidates: [{ title: "Old" }] }));
  await pending;
  assert.equal(ui.get("#discovery-results").children.length, 0);
  assert.equal(ui.get("#discover").disabled, true);
});

test("selected discovery candidate can be analyzed and observations render safely", async () => {
  const ui = fixture(async (url, options) => {
    if (url === "/api/search-plan") return response(200, preparedResponse);
    if (url === "/api/discover") return response(200, {
      status: "completed", message: "Candidates", issues: [], candidates: [{
        title: "Alex Demo", url: "https://example.org/profile", snippet: "Public profile",
        domain: "example.org", source_type: "web_page", provider: "tavily", rank: 1,
        discovered_by: [discoveryQuery]
      }]
    });
    assert.equal(url, "/api/analyze-sources");
    const submitted = JSON.parse(options.body);
    assert.equal(submitted.candidates.length, 1);
    assert.equal(submitted.candidates[0].url, "https://example.org/profile");
    return response(200, {
      status: "completed", message: "Source observations only", sources: [{
        candidate: submitted.candidates[0], status: "analyzed", final_url: "https://example.org/profile",
        page_title: "<b>Alex Demo</b>", meta_description: "Controlled public page",
        content_excerpt: "Name: Alex Demo", issue: null,
        observations: [{ field: "name", value: "<script>Alex Demo</script>", evidence: "Name: Alex Demo", extraction_method: "text_pattern" }]
      }]
    });
  });
  await ui.get("#prepare").events.click();
  await ui.get("#discover").events.click();
  const candidateCard = ui.get("#discovery-results").children[0];
  const checkbox = candidateCard.children[5].children[0];
  checkbox.checked = true;
  checkbox.events.change();
  assert.equal(ui.get("#analyze-sources").disabled, false);
  await ui.get("#analyze-sources").events.click();
  assert.equal(ui.get("#analysis-results").children.length, 1);
  assert.match(ui.get("#analysis-results").textContent, /<b>Alex Demo<\/b>/);
  assert.match(ui.get("#analysis-results").textContent, /<script>Alex Demo<\/script>/);
  assert.match(ui.get("#analysis-results").textContent, /not identity-verified/);
});

test("source selection is capped at three and changing the plan clears analysis", async () => {
  const candidates = Array.from({ length: 4 }, (_, i) => ({
    title: `Candidate ${i}`, url: `https://example.org/${i}`, snippet: "x", domain: "example.org",
    source_type: "web_page", provider: "tavily", rank: i + 1, discovered_by: [discoveryQuery]
  }));
  const ui = fixture(async url => url === "/api/search-plan" ? response(200, preparedResponse)
    : response(200, { status: "completed", message: "Candidates", issues: [], candidates }));
  await ui.get("#prepare").events.click();
  await ui.get("#discover").events.click();
  const cards = ui.get("#discovery-results").children;
  for (let i = 0; i < 4; i += 1) {
    const checkbox = cards[i].children[5].children[0];
    checkbox.checked = true;
    checkbox.events.change();
  }
  assert.equal(cards[3].children[5].children[0].checked, false);
  assert.match(ui.get("#analysis-status").textContent, /at most three/);
  ui.get("#name").events.input();
  assert.equal(ui.get("#analysis-results").children.length, 0);
  assert.equal(ui.get("#analyze-sources").disabled, true);
});

test("source analysis sends current seed context and correlation renders evidence-backed assessment", async () => {
  let analysisBody, correlationBody;
  const candidate = {
    title: "Alex Demo", url: "https://example.org/profile", snippet: "Public profile", domain: "example.org",
    source_type: "web_page", provider: "tavily", rank: 1, discovered_by: [discoveryQuery]
  };
  const analyzed = {
    candidate, status: "analyzed", final_url: candidate.url, page_title: "Alex Demo",
    meta_description: "Controlled page", content_excerpt: "Alex Demo at ABC College", issue: null,
    page_attribution: { authors: ["Site Editor"], publishers: ["Example Media"], site_social_links: [] },
    observations: [
      { field: "name", value: "Alex Demo", evidence: "Heading: Alex Demo", extraction_method: "text_match" },
      { field: "organization", value: "ABC College", evidence: "Bio: ABC College", extraction_method: "text_match" }
    ]
  };
  const ui = fixture(async (url, options) => {
    if (url === "/api/search-plan") return response(200, preparedResponse);
    if (url === "/api/discover") return response(200, { status: "completed", message: "Candidates", issues: [], candidates: [candidate] });
    if (url === "/api/analyze-sources") {
      analysisBody = JSON.parse(options.body);
      return response(200, { status: "completed", message: "Source observations only", sources: [analyzed] });
    }
    assert.equal(url, "/api/correlate");
    correlationBody = JSON.parse(options.body);
    return response(200, {
      status: "completed",
      message: "Evidence-backed candidate assessment only.",
      seed_profile: [
        { field: "name", value: "Alex Demo", references: [{ source: "supplied_context", clue_id: null }] },
        { field: "organization", value: "ABC College", references: [{ source: "supplied_context", clue_id: null }] }
      ],
      cross_source_support: [],
      assessments: [{
        source_url: candidate.url, page_title: "Alex Demo", status: "supported",
        matched_fields: ["name", "organization"], conflicting_fields: [],
        rationale: "Multiple independent seed signals support this candidate.",
        comparisons: [{
          field: "name", seed_value: "Alex Demo", status: "match", source_values: ["Alex Demo"],
          explanation: "The source contains evidence matching the seed name.",
          evidence: [{ value: "Alex Demo", evidence: "Heading: Alex Demo", extraction_method: "text_match", source_url: candidate.url }]
        }]
      }]
    });
  });
  ui.get("#name").value = "Alex Demo";
  ui.get("#organization").value = "ABC College";
  await ui.get("#prepare").events.click();
  await ui.get("#discover").events.click();
  const checkbox = ui.get("#discovery-results").children[0].children[5].children[0];
  checkbox.checked = true;
  checkbox.events.change();
  await ui.get("#analyze-sources").events.click();
  assert.equal(analysisBody.supplied_context.name, "Alex Demo");
  assert.equal(analysisBody.supplied_context.organization, "ABC College");
  assert.match(ui.get("#analysis-results").textContent, /Page attribution/);
  assert.match(ui.get("#analysis-results").textContent, /Site Editor/);
  assert.equal(ui.get("#correlate").disabled, false);
  await ui.get("#correlate").events.click();
  assert.equal(correlationBody.supplied_context.name, "Alex Demo");
  assert.equal(correlationBody.sources.length, 1);
  assert.match(ui.get("#correlation-results").textContent, /Seed Identity Profile/);
  assert.match(ui.get("#correlation-results").textContent, /SUPPORTED/);
  assert.match(ui.get("#correlation-results").textContent, /Heading: Alex Demo/);
});

test("editing seed context clears analyzed records and correlation state", async () => {
  const candidate = {
    title: "Alex Demo", url: "https://example.org/profile", snippet: "Public profile", domain: "example.org",
    source_type: "web_page", provider: "tavily", rank: 1, discovered_by: [discoveryQuery]
  };
  const ui = fixture(async (url, options) => {
    if (url === "/api/search-plan") return response(200, preparedResponse);
    if (url === "/api/discover") return response(200, { status: "completed", message: "Candidates", issues: [], candidates: [candidate] });
    if (url === "/api/analyze-sources") return response(200, {
      status: "completed", message: "Source observations only", sources: [{
        candidate, status: "analyzed", final_url: candidate.url, page_title: "Alex Demo",
        observations: [{ field: "name", value: "Alex Demo", evidence: "Name: Alex Demo", extraction_method: "text_match" }],
        page_attribution: { authors: [], publishers: [], site_social_links: [] }
      }]
    });
    return response(200, { status: "completed", message: "done", seed_profile: [], cross_source_support: [], assessments: [] });
  });
  ui.get("#name").value = "Alex Demo";
  await ui.get("#prepare").events.click();
  await ui.get("#discover").events.click();
  const checkbox = ui.get("#discovery-results").children[0].children[5].children[0];
  checkbox.checked = true;
  checkbox.events.change();
  await ui.get("#analyze-sources").events.click();
  assert.equal(ui.get("#correlate").disabled, false);
  ui.get("#name").events.input();
  assert.equal(ui.get("#analysis-results").children.length, 0);
  assert.equal(ui.get("#correlation-results").children.length, 0);
  assert.equal(ui.get("#correlate").disabled, true);
});

test("reviewer can add a missed visible image clue without presenting it as OCR", async () => {
  let submitted;
  const ui = fixture(async (url, options) => {
    assert.equal(url, "/api/search-plan");
    submitted = JSON.parse(options.body);
    return response(200, { status: "prepared", label: "Prepared", message: "Local", queries: [] });
  });
  ui.get("#name").value = "Dr. S Rao Chintalpudi";
  ui.get("#add-visual-clue").events.click();
  const card = ui.get("#clue-list").children[0];
  assert.match(card.children[0].textContent, /reviewer-added/);
  assert.match(card.children[1].textContent, /not OCR/);
  card.children[3].value = "CMR Technical Campus";
  card.children[5].value = "organization";
  card.children[6].children[0].checked = true;
  await ui.get("#prepare").events.click();
  assert.equal(submitted.clues[0].original_text, "Reviewer-added visible image clue (not OCR)");
  assert.equal(submitted.clues[0].corrected_text, "CMR Technical Campus");
  assert.equal(submitted.clues[0].category, "organization");
  assert.equal(submitted.clues[0].selected, true);
});
test('platform queries display and reach discovery without replacing generic queries',async()=>{
 const targeted={query:'"Alex Demo" site:github.com',reason:'Platform candidate',references:[]};let body;
 const ui=fixture(async(url,options)=>{
  if(url==='/api/search-plan')return response(200,{...preparedResponse,platform_queries:[targeted]});
  body=JSON.parse(options.body);return response(200,{status:'no_results',message:'No candidates',issues:[],candidates:[]});
 });
 ui.get('#name').value='Alex Demo';await ui.get('#prepare').events.click();
 assert.match(ui.get('#query-results').textContent,/site:github.com/);
 await ui.get('#discover').events.click();assert.deepEqual(body.queries,[discoveryQuery]);assert.deepEqual(body.platform_queries,[targeted]);
 ui.get('#name').events.input();assert.equal(ui.get('#discover').disabled,true);
});
test('inaccessible platform candidates remain selectable for evidence report and edits invalidate them',async()=>{
 const candidate={title:'Alex',url:'https://linkedin.com/in/alex',domain:'linkedin.com',platform:'linkedin',snippet:'Indexed only',provider:'tavily',rank:1,discovered_by:[discoveryQuery]};let body;
 const ui=fixture(async(url,options)=>{
  if(url==='/api/search-plan')return response(200,preparedResponse);
  if(url==='/api/discover')return response(200,{status:'completed',message:'Candidates',issues:[],candidates:[candidate]});
  if(url==='/api/analyze-sources')return response(200,{status:'failed',message:'Restricted',sources:[{candidate,status:'restricted',observations:[],profile:{platform:'linkedin',access_status:'SOURCE_INACCESSIBLE'}}]});
  body=JSON.parse(options.body);return response(200,{status:'completed',message:'Insufficient evidence',seed_profile:[],assessments:[],cross_source_support:[]});
 });
 ui.get('#name').value='Alex';await ui.get('#prepare').events.click();await ui.get('#discover').events.click();
 assert.match(ui.get('#discovery-results').textContent,/Linkedin/i);
 const check=ui.get('#discovery-results').children[0].children[5].children[0];check.checked=true;check.events.change();
 await ui.get('#analyze-sources').events.click();assert.equal(ui.get('#correlate').disabled,false);
 await ui.get('#correlate').events.click();assert.equal(body.sources[0].status,'restricted');assert.equal(body.discovered_candidates.length,1);
 ui.get('#organization').events.input();assert.equal(ui.get('#correlation-results').children.length,0);
});
