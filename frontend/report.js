/* Stage 06: synthesis-first identity resolution report. Untrusted text never becomes HTML. */
function renderIdentityReport(container, report) {
  const el = (tag, text, cls) => {
    const node = document.createElement(tag);
    if (text != null) node.textContent = text;
    if (cls) node.className = cls;
    return node;
  };
  const label = value => String(value ?? '').replaceAll('_', ' ');
  const clip = (value, limit = 260) => {
    const text = String(value ?? '').replace(/\s+/g, ' ').trim();
    return text.length > limit ? text.slice(0, limit - 1).trimEnd() + '…' : text;
  };
  const platforms = {
    github:'GitHub', linkedin:'LinkedIn', x:'X', twitter:'Twitter', youtube:'YouTube',
    instagram:'Instagram', google_scholar:'Google Scholar', personal_site:'Personal Site',
    huggingface:'Hugging Face', substack:'Substack', faculty:'Faculty', institution:'Institution',
    conference:'Conference', event:'Event', publication:'Publication', other:'Other'
  };
  const statuses = {
    supported:'Supported association', partially_supported:'Partially supported', review_required:'Review required',
    conflicting:'Conflicting', insufficient:'Insufficient evidence', insufficient_evidence:'Insufficient evidence',
    partial:'Partial support', missing:'Missing', not_assessed:'Not assessed',
    consistent:'Temporally consistent', mixed:'Mixed temporal evidence', insufficient:'Insufficient evidence'
  };
  const access = {
    PUBLIC_PAGE_ANALYZED:'Direct page analyzed', SEARCH_SNIPPET_ONLY:'Indexed metadata only',
    SOURCE_INACCESSIBLE:'Direct page inaccessible'
  };
  const link = url => {
    try {
      const parsed = new URL(url);
      if (!['https:', 'http:'].includes(parsed.protocol) || parsed.username || parsed.password) throw Error();
      const a = el('a', parsed.hostname); a.href = parsed.href; a.target = '_blank'; a.rel = 'noopener noreferrer'; return a;
    } catch { return el('span', 'Source link unavailable'); }
  };
  const drawer = (title, cls='report-drawer') => {
    const node = el('details', null, cls); node.append(el('summary', title)); return node;
  };
  const excerpt = evidence => {
    const quote = el('blockquote');
    quote.append(el('p', clip(evidence.evidence, 320)), link(evidence.source_url), el('small', label(evidence.extraction_method)));
    return quote;
  };
  const section = (kicker, title, cls='report-section') => {
    const node = el('section', null, cls);
    if (kicker) node.append(el('p', kicker, 'report-kicker'));
    node.append(el('h4', title));
    container.append(node);
    return node;
  };

  const renderGraph = graph => {
    const wrap = el('div', null, 'relationship-graph-wrap');
    if (!graph?.nodes?.length) {
      wrap.append(el('p', 'No evidence-backed relationships are available yet.', 'graph-empty'));
      return wrap;
    }

    const nodeById = new Map((graph.nodes || []).map(node => [node.id, node]));
    const root = (graph.nodes || []).find(node => node.type === 'identity') || graph.nodes[0];
    const statusRank = { supported: 0, partial: 1, partially_supported: 1, uncertain: 2, conflicting: 3 };
    const visibleProfiles = (graph.nodes || [])
      .filter(node => node.type === 'profile' && node.status !== 'conflicting')
      .sort((a, b) => (statusRank[a.status] ?? 9) - (statusRank[b.status] ?? 9) || String(a.label).localeCompare(String(b.label)))
      .slice(0, 7);
    const coreNodes = (graph.nodes || [])
      .filter(node => ['organization', 'role', 'department'].includes(node.type))
      .slice(0, 4);
    const secondaryTypes = new Set(['project', 'event', 'publication', 'education', 'location']);
    const secondaryNodes = (graph.nodes || []).filter(node => secondaryTypes.has(node.type));
    const profileIds = new Set(visibleProfiles.map(node => node.id));
    const coreIds = new Set(coreNodes.map(node => node.id));
    const secondaryIds = new Set(secondaryNodes.map(node => node.id));
    const relevantEdges = (graph.edges || []).filter(edge => nodeById.has(edge.source) && nodeById.has(edge.target));

    const profileLabel = node => {
      const raw = String(node?.label || '').trim();
      const idx = raw.indexOf(':');
      if (idx < 0) return { platform: 'Public profile', value: raw };
      const key = raw.slice(0, idx).trim();
      return { platform: platforms[key] || label(key), value: raw.slice(idx + 1).trim() || raw };
    };
    const prettyType = type => ({
      organization: 'Organization', role: 'Role', department: 'Department', project: 'Project',
      event: 'Event', publication: 'Publication', education: 'Education', location: 'Location'
    }[type] || label(type));
    const lineText = (value, max = 24) => {
      const text = String(value || '').replace(/\s+/g, ' ').trim();
      if (text.length <= max) return [text];
      const words = text.split(' ');
      const lines = ['', ''];
      for (const word of words) {
        const target = lines[0].length < max ? 0 : 1;
        if (target === 1 && lines[1] && `${lines[1]} ${word}`.length > max) {
          lines[1] = `${lines[1].slice(0, Math.max(1, max - 1))}…`;
          break;
        }
        if (!lines[target]) lines[target] = word;
        else if (`${lines[target]} ${word}`.length <= max) lines[target] += ` ${word}`;
        else if (target === 0) lines[1] = word;
        else { lines[1] = `${lines[1].slice(0, Math.max(1, max - 1))}…`; break; }
      }
      if (!lines[1] && lines[0].length > max) lines[0] = `${lines[0].slice(0, max - 1)}…`;
      return lines.filter(Boolean).slice(0, 2);
    };

    const toolbar = el('div', null, 'graph-toolbar');
    const legend = el('div', null, 'graph-legend');
    for (const [kind, text] of [['supported', 'Supported'], ['partial', 'Partial / indirect']]) {
      const item = el('span', null, 'graph-legend-item');
      item.append(el('i', null, `graph-legend-line ${kind}`), el('span', text));
      legend.append(item);
    }
    toolbar.append(legend, el('p', 'Select a public profile to reveal its evidence-backed projects, events and other secondary connections.', 'graph-help'));
    wrap.append(toolbar);

    const stage = el('div', null, 'graph-stage');
    const caption = el('div', null, 'graph-caption');
    wrap.append(stage, caption);
    let selectedProfileId = null;

    const make = (tag, attrs = {}) => {
      const node = document.createElementNS('http://www.w3.org/2000/svg', tag);
      Object.entries(attrs).forEach(([key, value]) => node.setAttribute(key, String(value)));
      return node;
    };

    const render = () => {
      stage.replaceChildren();
      const selectedProfile = selectedProfileId ? nodeById.get(selectedProfileId) : null;
      const selectedSecondaryIds = new Set();
      if (selectedProfile) {
        for (const edge of relevantEdges) {
          if (edge.source === selectedProfile.id && secondaryIds.has(edge.target)) selectedSecondaryIds.add(edge.target);
          if (edge.target === selectedProfile.id && secondaryIds.has(edge.source)) selectedSecondaryIds.add(edge.source);
        }
      }
      const expandedNodes = secondaryNodes.filter(node => selectedSecondaryIds.has(node.id)).slice(0, 5);

      const width = Math.max(940, visibleProfiles.length * 170 + 140);
      const height = expandedNodes.length ? 610 : 465;
      const centerX = width / 2;
      const svg = make('svg', { viewBox: `0 0 ${width} ${height}`, role: 'img', 'aria-label': 'Evidence-backed identity relationship graph' });
      svg.setAttribute('class', 'relationship-graph-svg');

      const positions = new Map();
      positions.set(root.id, { x: centerX, y: 72, w: 270, h: 68 });

      const profileY = 205;
      const profileGap = width / Math.max(1, visibleProfiles.length + 1);
      visibleProfiles.forEach((node, index) => positions.set(node.id, { x: profileGap * (index + 1), y: profileY, w: 148, h: 66 }));

      const coreY = 365;
      const coreGap = width / Math.max(1, coreNodes.length + 1);
      coreNodes.forEach((node, index) => positions.set(node.id, { x: coreGap * (index + 1), y: coreY, w: 184, h: 62 }));

      if (expandedNodes.length) {
        const secondaryY = 520;
        const secondaryGap = width / Math.max(1, expandedNodes.length + 1);
        expandedNodes.forEach((node, index) => positions.set(node.id, { x: secondaryGap * (index + 1), y: secondaryY, w: 172, h: 60 }));
      }

      const pathBetween = (from, to, cls, relation, evidenceCount) => {
        if (!from || !to) return;
        const y1 = from.y + from.h / 2;
        const y2 = to.y - to.h / 2;
        const midY = y1 + (y2 - y1) * 0.48;
        const path = make('path', {
          d: `M ${from.x} ${y1} C ${from.x} ${midY}, ${to.x} ${midY}, ${to.x} ${y2}`,
          class: `graph-edge ${cls || 'uncertain'}`
        });
        const title = make('title');
        title.textContent = `${relation || 'relationship'} · ${evidenceCount || 0} evidence source(s)`;
        path.append(title);
        svg.append(path);
      };

      // Root → canonical public profiles.
      for (const node of visibleProfiles) {
        const edge = relevantEdges.find(item =>
          ((item.source === root.id && item.target === node.id) || (item.target === root.id && item.source === node.id))
        );
        pathBetween(positions.get(root.id), positions.get(node.id), edge?.status || node.status, edge?.relation || 'public trace', edge?.evidence_count || 0);
      }

      // Root → core identity context only. Keep projects/events hidden until profile expansion.
      for (const node of coreNodes) {
        const edge = relevantEdges.find(item =>
          ((item.source === root.id && item.target === node.id) || (item.target === root.id && item.source === node.id))
        );
        pathBetween(positions.get(root.id), positions.get(node.id), edge?.status || node.status, edge?.relation || prettyType(node.type), edge?.evidence_count || 0);
      }

      // Selected profile → secondary evidence nodes.
      if (selectedProfile) {
        for (const node of expandedNodes) {
          const edge = relevantEdges.find(item =>
            ((item.source === selectedProfile.id && item.target === node.id) || (item.target === selectedProfile.id && item.source === node.id))
          );
          pathBetween(positions.get(selectedProfile.id), positions.get(node.id), edge?.status || 'supported', edge?.relation || prettyType(node.type), edge?.evidence_count || 0);
        }

        // Show explicit cross-profile links only for the selected profile to avoid spaghetti lines.
        for (const edge of relevantEdges) {
          const otherId = edge.source === selectedProfile.id ? edge.target : edge.target === selectedProfile.id ? edge.source : null;
          if (!otherId || !profileIds.has(otherId) || !positions.has(otherId)) continue;
          const from = positions.get(selectedProfile.id), to = positions.get(otherId);
          const y = profileY - 44;
          const curve = make('path', {
            d: `M ${from.x} ${from.y - from.h / 2} Q ${(from.x + to.x) / 2} ${y - 46} ${to.x} ${to.y - to.h / 2}`,
            class: `graph-crosslink ${edge.status || 'partial'}`
          });
          const title = make('title');
          title.textContent = `${edge.relation || 'explicit profile link'} · ${edge.evidence_count || 0} evidence source(s)`;
          curve.append(title);
          svg.append(curve);
        }
      }

      const addTextLines = (group, lines, y, cls) => {
        const textNode = make('text', { x: 0, y, 'text-anchor': 'middle', class: cls });
        lines.forEach((line, index) => {
          const tspan = make('tspan', { x: 0, dy: index === 0 ? 0 : 15 });
          tspan.textContent = line;
          textNode.append(tspan);
        });
        group.append(textNode);
      };

      const addNode = (node, pos, kind) => {
        if (!node || !pos) return;
        const isProfile = kind === 'profile';
        const isSelected = isProfile && node.id === selectedProfileId;
        const group = make('g', {
          class: `graph-card graph-card-${kind} ${node.status || 'uncertain'}${isSelected ? ' selected' : ''}`,
          transform: `translate(${pos.x},${pos.y})`
        });
        if (isProfile) {
          group.setAttribute('role', 'button');
          group.setAttribute('tabindex', '0');
          group.setAttribute('aria-pressed', isSelected ? 'true' : 'false');
          group.setAttribute('aria-label', `${node.label}. ${isSelected ? 'Hide' : 'Show'} related evidence.`);
        }
        const rect = make('rect', { x: -pos.w / 2, y: -pos.h / 2, width: pos.w, height: pos.h, rx: kind === 'identity' ? 16 : 12, ry: kind === 'identity' ? 16 : 12 });
        const title = make('title'); title.textContent = node.label; rect.append(title); group.append(rect);

        if (kind === 'identity') {
          addTextLines(group, lineText(node.label, 30), -3, 'graph-card-title');
          const sub = make('text', { x: 0, y: 23, 'text-anchor': 'middle', class: 'graph-card-subtitle' });
          sub.textContent = 'Most supported identity'; group.append(sub);
        } else if (kind === 'profile') {
          const info = profileLabel(node);
          const platformText = make('text', { x: 0, y: -10, 'text-anchor': 'middle', class: 'graph-card-kicker' });
          platformText.textContent = info.platform; group.append(platformText);
          addTextLines(group, lineText(info.value, 19), 10, 'graph-card-title');
        } else {
          const typeText = make('text', { x: 0, y: -10, 'text-anchor': 'middle', class: 'graph-card-kicker' });
          typeText.textContent = prettyType(node.type); group.append(typeText);
          addTextLines(group, lineText(node.label, 23), 10, 'graph-card-title');
        }

        if (isProfile) {
          const activate = event => {
            if (event.type === 'keydown' && !['Enter', ' '].includes(event.key)) return;
            if (event.type === 'keydown') event.preventDefault();
            selectedProfileId = selectedProfileId === node.id ? null : node.id;
            render();
          };
          if (typeof group.addEventListener === 'function') {
            group.addEventListener('click', activate);
            group.addEventListener('keydown', activate);
          }
        }
        svg.append(group);
      };

      addNode(root, positions.get(root.id), 'identity');
      visibleProfiles.forEach(node => addNode(node, positions.get(node.id), 'profile'));
      coreNodes.forEach(node => addNode(node, positions.get(node.id), 'context'));
      expandedNodes.forEach(node => addNode(node, positions.get(node.id), 'secondary'));

      if (!visibleProfiles.length) {
        const message = make('text', { x: centerX, y: 205, 'text-anchor': 'middle', class: 'graph-no-profiles' });
        message.textContent = 'No supported public profiles are available for this case.';
        svg.append(message);
      }

      stage.append(svg);
      caption.replaceChildren();
      const visibleNodeCount = 1 + visibleProfiles.length + coreNodes.length + expandedNodes.length;
      caption.append(el('span', `${visibleProfiles.length} connected public profile${visibleProfiles.length === 1 ? '' : 's'} shown`));
      if (selectedProfile) caption.append(el('strong', `Expanded: ${profileLabel(selectedProfile).platform}`));
      if ((graph.nodes || []).length > visibleNodeCount) caption.append(el('span', 'Secondary evidence is intentionally hidden until requested.'));
    };

    render();
    return wrap;
  };

  container.replaceChildren();
  const profiles = report.connected_profiles || [];
  const resolved = report.most_supported_identity || {
    label: report.identity_label,
    status: report.status,
    username: report.supplied_context?.username,
    organization: report.supplied_context?.organization,
    role: report.supplied_context?.role,
    department: report.supplied_context?.department,
    corroborated_fields: (report.fields || []).filter(f => f.status === 'supported').map(f => f.field),
    unresolved_fields: (report.fields || []).filter(f => ['partial','missing','not_assessed'].includes(f.status)).map(f => f.field),
    conflicting_fields: (report.fields || []).filter(f => f.status === 'conflicting').map(f => f.field),
    connected_footprint_count: profiles.filter(p => ['supported','partially_supported'].includes(p.status)).length,
    rationale: report.summary
  };
  const footprint = profiles.filter(item => ['supported','partially_supported'].includes(item.status));
  const rejectedOrUnresolved = profiles.filter(item => ['conflicting','insufficient_evidence'].includes(item.status));

  // 1. MOST SUPPORTED IDENTITY
  const hero = el('section', null, 'resolution-hero resolution-focus');
  hero.append(
    el('p', '01 · MOST SUPPORTED IDENTITY', 'report-eyebrow'),
    el('h3', resolved.label || 'Identity under review'),
    el('span', statuses[resolved.status] || label(resolved.status), `report-status ${resolved.status}`),
    el('p', clip(resolved.rationale, 260), 'report-summary')
  );
  const identityFacts = el('div', null, 'identity-facts');
  for (const [field, value] of [
    ['Username', resolved.username], ['Organization', resolved.organization], ['Role', resolved.role], ['Department', resolved.department]
  ]) {
    if (!value) continue;
    const fact = el('div', null, 'identity-fact');
    fact.append(el('span', field), el('strong', value));
    identityFacts.append(fact);
  }
  if (identityFacts.children.length) hero.append(identityFacts);
  const metrics = el('div', null, 'report-metrics resolution-metrics');
  const metricRows = [
    [`${report.supported_fields ?? 0} / ${report.supplied_fields ?? 0}`, 'Seed signals corroborated'],
    [resolved.connected_footprint_count ?? footprint.length, 'Connected public traces'],
    [report.conflict_count ?? 0, 'Direct conflicts']
  ];
  if (report.ml_assessment) metricRows.push([`${Number(report.ml_assessment.score).toFixed(2)} · ${label(report.ml_assessment.band)}`, 'ML assist score']);
  for (const [value, title] of metricRows) {
    const metric = el('div'); metric.append(el('strong', value), el('span', title)); metrics.append(metric);
  }
  hero.append(metrics, el('p', 'Evidence-backed association — not absolute proof of identity.', 'report-footnote'));
  if (report.ml_assessment) hero.append(el('p', `ML assist: ${clip(report.ml_assessment.explanation, 220)} Score is not a calibrated identity probability.`, 'ml-disclaimer'));
  container.append(hero);

  // 2. CONNECTED PUBLIC FOOTPRINT
  const footprintSection = section('02 · CONNECTED PUBLIC FOOTPRINT', 'Records that currently align with the resolved identity');
  if (!footprint.length) {
    footprintSection.append(el('p', 'No public profile or identity record is sufficiently supported yet. Candidate records remain available under Conflicts & uncertainty.'));
  } else {
    const grid = el('div', null, 'footprint-grid');
    footprint.slice(0, 6).forEach(item => {
      const p = item.profile;
      const card = el('article', null, 'footprint-card');
      const top = el('div', null, 'footprint-card-head');
      const identity = p.username ? '@' + p.username : p.display_name || p.profile_slug || p.domain;
      const name = el('div');
      name.append(el('small', platforms[p.platform] || label(p.platform), 'platform-label'), el('strong', identity));
      top.append(name, el('span', statuses[item.status] || label(item.status), `report-status ${item.status}`));
      card.append(top);
      if (item.ml_score != null) card.append(el('small', `ML assist ${Number(item.ml_score).toFixed(2)} · ${label(item.ml_band)}`, 'profile-ml-score'));
      const signals = item.supporting.filter(v => !String(v).startsWith('explicit profile cross-link from '));
      const compact = signals.slice(0, 3).map(v => '✓ ' + label(v)).join(' · ');
      if (compact) card.append(el('p', compact, 'footprint-signals'));
      const links = item.supporting.filter(v => String(v).startsWith('explicit profile cross-link from ')).length;
      if (links) card.append(el('p', `${links} explicit cross-link${links === 1 ? '' : 's'} support this association.`, 'crosslink-note'));
      const meta = el('div', null, 'footprint-meta');
      meta.append(el('span', access[p.access_status] || label(p.access_status)), link(p.url));
      card.append(meta);

      const details = drawer('View evidence');
      details.append(el('p', clip(item.rationale, 220), 'profile-rationale'));
      if (item.missing.length) details.append(el('p', 'Missing: ' + item.missing.map(label).join(', '), 'missing-signal'));
      if (item.conflicts.length) details.append(el('p', 'Conflicts: ' + item.conflicts.join('; '), 'profile-conflict'));
      for (const evidence of (item.evidence || []).slice(0, 6)) details.append(excerpt(evidence));
      if (p.external_profile_links?.length) {
        const cross = drawer(`Explicit links (${p.external_profile_links.length})`);
        for (const edge of p.external_profile_links.slice(0, 8)) {
          const row = el('p', `${label(edge.relation_type)}: ${clip(edge.evidence, 150)} `); row.append(link(edge.target_url)); cross.append(row);
        }
        details.append(cross);
      }
      if (p.projects?.length) details.append(el('p', 'Related projects: ' + p.projects.slice(0, 6).join(', ')));
      card.append(details);
      grid.append(card);
    });
    footprintSection.append(grid);
    if (footprint.length > 6) {
      const more = drawer(`More connected traces (${footprint.length - 6})`);
      footprint.slice(6).forEach(item => {
        const p = item.profile;
        const row = el('p', `${platforms[p.platform] || label(p.platform)} · ${p.username ? '@'+p.username : p.display_name || p.domain} · ${statuses[item.status] || label(item.status)} `);
        row.append(link(p.url)); more.append(row);
      });
      footprintSection.append(more);
    }
  }

  // 3. TEMPORAL REASONING
  const temporal = report.temporal_assessment;
  const temporalSection = section('03 · TEMPORAL REASONING', 'Timeline & consistency');
  if (!temporal) {
    temporalSection.append(el('p', 'Temporal reasoning is unavailable for this report.'));
  } else {
    const head = el('div', null, 'temporal-head');
    head.append(el('span', statuses[temporal.status] || label(temporal.status), `report-status ${temporal.status}`), el('p', clip(temporal.summary, 260)));
    temporalSection.append(head);
    if (temporal.timeline?.length) {
      const timeline = el('div', null, 'timeline-list');
      temporal.timeline.slice(0,6).forEach(item=>{ const row=el('article', null, 'timeline-item'); row.append(el('strong', item.year_label, 'timeline-year'), el('span', item.title)); const d=drawer('Evidence'); d.append(el('p', clip(item.evidence,240)), link(item.source_url)); row.append(d); timeline.append(row); });
      temporalSection.append(timeline);
      if (temporal.timeline.length>6) { const more=drawer(`More timeline observations (${temporal.timeline.length-6})`); temporal.timeline.slice(6).forEach(item=>more.append(el('p', `${item.year_label} · ${item.title}`))); temporalSection.append(more); }
    } else temporalSection.append(el('p', 'No explicit dated observation was strong enough to place on a timeline.', 'missing-signal'));
    if (temporal.issues?.length) { const issues=drawer(`Temporal review items (${temporal.issues.length})`); temporal.issues.slice(0,8).forEach(item=>issues.append(el('p', `${item.severity==='conflict'?'✕':'?'} ${label(item.field)}: ${item.reason}`, item.severity==='conflict'?'profile-conflict':'missing-signal'))); temporalSection.append(issues); }
  }

  // 4. RELATIONSHIP GRAPH
  const graphSection = section('04 · RELATIONSHIP GRAPH', 'Evidence-backed identity connections');
  graphSection.append(renderGraph(report.relationship_graph));

  // 5. EVIDENCE
  const evidenceSection = section('05 · EVIDENCE', 'Why this identity is or is not supported');
  const evidenceGrid = el('div', null, 'evidence-summary-grid');
  for (const field of (report.fields || [])) {
    const row = el('article', null, `evidence-summary-row ${field.status}`);
    const sourceCount = (field.direct_sources || 0) + (field.indexed_sources || 0);
    row.append(
      el('span', label(field.field), 'evidence-field-label'),
      el('strong', field.supplied_value),
      el('span', statuses[field.status] || label(field.status), `report-status ${field.status}`),
      el('small', sourceCount ? `${sourceCount} supporting source${sourceCount === 1 ? '' : 's'}` : 'No corroborating source yet')
    );
    if ((field.evidence || []).length) {
      const details = drawer('Inspect evidence', 'field-evidence-drawer');
      details.append(el('p', clip(field.explanation, 210)));
      for (const item of field.evidence.slice(0, 5)) details.append(excerpt(item));
      row.append(details);
    }
    evidenceGrid.append(row);
  }
  if (!(report.fields || []).length) evidenceSection.append(el('p', 'No supplied comparable identity fields were available.'));
  else evidenceSection.append(evidenceGrid);

  const findings = report.additional_observations || [];
  if (findings.length) {
    const discovered = drawer(`What else public sources report (${findings.length})`, 'additional-findings-drawer');
    for (const item of findings.slice(0, 8)) {
      const line = drawer(`${item.value} · ${label(item.field)} · ${item.source_count} source${item.source_count === 1 ? '' : 's'}`);
      for (const evidence of (item.evidence || []).slice(0, 4)) line.append(excerpt(evidence));
      discovered.append(line);
    }
    if (findings.length > 8) discovered.append(el('p', `${findings.length - 8} additional observations are retained in the full evidence audit.`));
    evidenceSection.append(discovered);
  }

  // 6. CONFLICTS & UNCERTAINTY
  const conflictSection = section('06 · CONFLICTS & UNCERTAINTY', 'What prevents stronger association');
  const conflictingFields = (report.fields || []).filter(f => f.status === 'conflicting');
  const unresolvedFields = (report.fields || []).filter(f => ['partial','missing','not_assessed'].includes(f.status));
  if (!conflictingFields.length && !unresolvedFields.length && !rejectedOrUnresolved.length) {
    conflictSection.append(el('p', 'No direct contradictions detected in the assessed evidence.'));
  }
  for (const field of conflictingFields) {
    conflictSection.append(el('p', `✕ ${label(field.field)}: ${field.supplied_value} — direct conflicting evidence requires review.`, 'profile-conflict'));
  }
  for (const field of unresolvedFields) {
    conflictSection.append(el('p', `? ${label(field.field)}: ${statuses[field.status] || label(field.status)}.`, 'missing-signal'));
  }
  const inaccessibleCount = profiles.filter(p => p.profile.access_status !== 'PUBLIC_PAGE_ANALYZED').length;
  if (inaccessibleCount) conflictSection.append(el('p', `? ${inaccessibleCount} candidate record${inaccessibleCount === 1 ? ' has' : 's have'} limited direct access; indexed evidence is kept separate.`, 'missing-signal'));

  if (rejectedOrUnresolved.length) {
    const candidates = drawer(`Unresolved / conflicting candidates (${rejectedOrUnresolved.length})`, 'uncertain-candidates-drawer');
    rejectedOrUnresolved.slice(0, 10).forEach(item => {
      const p = item.profile;
      const row = drawer(`${platforms[p.platform] || label(p.platform)} · ${p.username ? '@'+p.username : p.display_name || p.domain} · ${statuses[item.status] || label(item.status)}`);
      row.append(el('p', clip(item.rationale, 200)), el('small', access[p.access_status] || label(p.access_status), 'access-label'), link(p.url));
      if (item.conflicts.length) row.append(el('p', item.conflicts.join('; '), 'profile-conflict'));
      if (item.missing.length) row.append(el('p', 'Missing: ' + item.missing.map(label).join(', '), 'missing-signal'));
      candidates.append(row);
    });
    conflictSection.append(candidates);
  }

  // Auditability: retained but deliberately outside the four-part decision flow.
  const audit = drawer(`Full evidence & source audit (${report.record_count ?? report.sources?.length ?? 0})`, 'report-source-audit');
  const auditRecords = [...(report.other_records || []), ...profiles.flatMap(p => p.profile.related_records || [])];
  const seen = new Set();
  for (const record of auditRecords) {
    if (seen.has(record.url)) continue; seen.add(record.url);
    const d = drawer(`${label(record.record_type)}: ${clip(record.title || record.url, 100)}`);
    d.append(el('p', record.disposition), link(record.url));
    if (record.snippet) d.append(el('blockquote', clip(record.snippet, 320)));
    audit.append(d);
  }
  for (const source of (report.sources || [])) {
    const url = source.final_url || source.candidate.url;
    if (seen.has(url)) continue; seen.add(url);
    const d = drawer(`${clip(source.page_title || source.candidate.title, 100)} · ${source.status}`);
    d.append(link(url));
    if (source.issue) d.append(el('p', clip(source.issue.message, 220)));
    for (const o of (source.observations || []).slice(0, 10)) d.append(el('blockquote', `${o.field}: ${clip(o.value, 120)}\n${clip(o.evidence, 240)}`));
    audit.append(d);
  }
  container.append(audit);

  const technical = drawer('Method & image provenance', 'report-source-audit');
  for (const clue of (report.reviewed_image_clues || [])) technical.append(el('p', `${clue.clue_id} · Original: ${clip(clue.original_text,100)} · Reviewed: ${clip(clue.corrected_text,100)} · ${clue.selected?'Selected':'Not selected'}`));
  if (!(report.reviewed_image_clues || []).length) technical.append(el('p', 'No image clues used in this report.'));
  technical.append(el('p', report.method || 'Deterministic evidence comparison.'));
  for (const note of (report.limitations || [])) technical.append(el('p', clip(note, 240)));
  container.append(technical);
}
