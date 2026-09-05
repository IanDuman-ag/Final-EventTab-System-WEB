(() => {
  const csrf = window.SS_CSRF || '';
  const urls = window.SS_URLS || {};

  let templates = [];
  let fieldDefs = { match: [], criteria: [] };
  let defaultOrder = { match: [], criteria: [] };
  try { templates = JSON.parse(document.getElementById('ss-templates-data')?.textContent || '[]'); } catch (_) {}
  try { fieldDefs = JSON.parse(document.getElementById('ss-field-defs')?.textContent || '{}'); } catch (_) {}
  try { defaultOrder = JSON.parse(document.getElementById('ss-default-order')?.textContent || '{}'); } catch (_) {}
  let matchPreviewEvents = [];
  let schoolBranding = { school_name: 'EventTab', intramurals_name: '', school_logo_url: '' };
  try { matchPreviewEvents = JSON.parse(document.getElementById('ss-match-preview-events')?.textContent || '[]'); } catch (_) {}
  try { schoolBranding = JSON.parse(document.getElementById('ss-school-branding')?.textContent || '{}'); } catch (_) {}


  const listPanel = document.querySelector('[data-panel="list"]');
  const wizardPanel = document.querySelector('[data-panel="wizard"]');
  const viewDialog = document.getElementById('view-dialog');
  const sectionList = document.getElementById('section-list');
  const editBody = document.getElementById('edit-component-body');
  const richPreview = document.getElementById('rich-preview');

  let viewingId = null;
  let currentOrder = [];
  let currentFields = {};
  let sections = [];
  let selectedSectionId = null;
  let editingColumnId = null;
  let dragSectionId = null;
  let previewTimer = null;
  let selectedPreviewEventId = '';
  let selectedParticipantIds = [];
  let builderBrand = {
    leftLogoDataUrl: '',
    rightLogoDataUrl: '',
    orgTitle: '',
    orgTagline: 'UNITY. PASSION. EXCELLENCE.',
  };


  const SECTION_CATALOG = {
    match: [
      { id: 'header', title: 'Header', description: 'Logo, Organization, Event Name', fieldKeys: ['school_logo', 'event_name'] },
      { id: 'event_info', title: 'Event Information', description: 'Date, Venue, Event Type', fieldKeys: ['date', 'time', 'venue', 'playing_area', 'event_classification', 'division', 'tournament_format', 'game_number', 'round'] },
      { id: 'participants', title: 'Participants', description: 'Participant List', fieldKeys: ['team_a', 'team_b'] },
      { id: 'scoring', title: 'Scoring Table', description: 'Scores and Rankings Table', fieldKeys: ['quarter_scores'], kind: 'scoring' },
      { id: 'result', title: 'Result / Summary', description: 'Top Placers / Summary', fieldKeys: ['final_score', 'winner', 'remarks'] },
      { id: 'officials', title: 'Officials', description: 'Signature / Names', fieldKeys: ['sig_referee', 'sig_scorer', 'sig_faculty'] },
    ],
    criteria: [
      { id: 'header', title: 'Header', description: 'Logo, Organization, Event Name', fieldKeys: ['school_logo', 'event_name'] },
      { id: 'event_info', title: 'Event Information', description: 'Stage and Event Details', fieldKeys: ['stage_name'] },
      { id: 'participants', title: 'Participants', description: 'Contestant Details', fieldKeys: ['contestant_number', 'contestant_name', 'department'] },
      { id: 'scoring', title: 'Scoring Table', description: 'Criteria Scores Table', fieldKeys: ['criteria_table', 'criteria_weight', 'maximum_score', 'judge_score'], kind: 'scoring' },
      { id: 'result', title: 'Result / Summary', description: 'Totals and Comments', fieldKeys: ['total_score', 'judge_comments'] },
      { id: 'officials', title: 'Officials', description: 'Signature / Names', fieldKeys: ['sig_judge', 'sig_faculty'] },
    ],
  };

  function defaultColumns(type) {
    if (type === 'criteria') {
      return [
        { id: 'criterion', name: 'Criterion', dataType: 'text', width: 28, align: 'left', calc: 'none', required: true, showPdf: true },
        { id: 'weight', name: 'Weight %', dataType: 'number', width: 14, align: 'center', calc: 'none', required: true, showPdf: true },
        { id: 'max', name: 'Max', dataType: 'number', width: 12, align: 'center', calc: 'none', required: true, showPdf: true },
        { id: 'score', name: 'Score', dataType: 'number', width: 14, align: 'center', calc: 'none', required: true, showPdf: true },
        { id: 'total', name: 'Weighted', dataType: 'number', width: 14, align: 'center', calc: 'sum', required: false, showPdf: true },
        { id: 'rank', name: 'Rank', dataType: 'number', width: 12, align: 'center', calc: 'rank', required: false, showPdf: true },
      ];
    }
    return [
      { id: 'participant', name: 'Participant', dataType: 'text', width: 24, align: 'left', calc: 'none', required: true, showPdf: true },
      { id: 'r1', name: 'Round 1', dataType: 'number', width: 12, align: 'center', calc: 'none', required: true, showPdf: true },
      { id: 'r2', name: 'Round 2', dataType: 'number', width: 12, align: 'center', calc: 'none', required: true, showPdf: true },
      { id: 'r3', name: 'Round 3', dataType: 'number', width: 12, align: 'center', calc: 'none', required: true, showPdf: true },
      { id: 'penalty', name: 'Penalty', dataType: 'number', width: 12, align: 'center', calc: 'none', required: false, showPdf: true },
      { id: 'total', name: 'Total', dataType: 'number', width: 12, align: 'center', calc: 'sum', required: false, showPdf: true },
      { id: 'rank', name: 'Rank', dataType: 'number', width: 10, align: 'center', calc: 'rank', required: false, showPdf: true },
    ];
  }

  function uid(prefix) {
    return `${prefix}_${Math.random().toString(36).slice(2, 9)}`;
  }

  function toast(message, isError) {
    let el = document.querySelector('.ss-toast');
    if (!el) {
      el = document.createElement('div');
      el.className = 'ss-toast';
      document.body.appendChild(el);
    }
    el.textContent = message;
    el.classList.toggle('is-error', !!isError);
    el.classList.add('is-on');
    clearTimeout(el._t);
    el._t = setTimeout(() => el.classList.remove('is-on'), 2800);
  }

  async function api(url, options = {}) {
    const res = await fetch(url, {
      credentials: 'same-origin',
      ...options,
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': csrf,
        'X-Requested-With': 'XMLHttpRequest',
        ...(options.headers || {}),
      },
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || data.success === false) throw new Error(data.message || `Request failed (${res.status})`);
    return data;
  }

  function escapeHtml(s) {
    return String(s ?? '')
      .replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }
  function escapeAttr(s) { return escapeHtml(s).replace(/'/g, '&#39;'); }

  function eventType() {
    return document.querySelector('input[name="event_type"]:checked')?.value || 'match';
  }

  function defsFor(type) {
    return fieldDefs[type] || [];
  }

  function defaultFields(type) {
    const out = {};
    defsFor(type).forEach((d) => { out[d.key] = true; });
    return out;
  }

  function findTemplate(id) {
    return templates.find((t) => String(t.id) === String(id));
  }

  function selectedCheckboxes() {
    return Array.from(document.querySelectorAll('.ss-row-check:checked'));
  }

  function updateBulkBar() {
    const selectAll = document.getElementById('ss-select-all');
    const bulkBar = document.getElementById('ss-bulk-bar');
    const bulkCount = document.getElementById('ss-bulk-count');
    const bulkBtn = document.getElementById('btn-bulk-delete');
    const all = Array.from(document.querySelectorAll('.ss-row-check'));
    const selected = selectedCheckboxes();
    const n = selected.length;
    if (bulkBar) bulkBar.hidden = n === 0;
    if (bulkCount) bulkCount.textContent = `${n} selected`;
    if (bulkBtn) bulkBtn.disabled = n === 0;
    if (selectAll) {
      selectAll.checked = all.length > 0 && n === all.length;
      selectAll.indeterminate = n > 0 && n < all.length;
    }
  }

  function removeTemplateRows(ids) {
    const idSet = new Set((ids || []).map(String));
    idSet.forEach((id) => {
      document.querySelector(`tr[data-id="${id}"]`)?.remove();
      templates = templates.filter((t) => String(t.id) !== id);
    });
    const tbody = document.getElementById('templates-tbody');
    if (tbody && !tbody.querySelector('tr[data-id]')) {
      tbody.innerHTML = '<tr class="ss-empty-row"><td colspan="6" class="ss-empty">No saved templates yet.</td></tr>';
    }
    updateBulkBar();
  }

  function buildDefaultSections(type, builder) {
    const catalog = SECTION_CATALOG[type] || SECTION_CATALOG.match;
    const saved = Array.isArray(builder?.sections) ? builder.sections : [];
    const byId = Object.fromEntries(saved.map((s) => [s.id, s]));
    const cols = Array.isArray(builder?.scoringColumns) && builder.scoringColumns.length
      ? builder.scoringColumns.map((c) => ({ ...c }))
      : defaultColumns(type);

    const list = catalog.map((base, index) => {
      const prev = byId[base.id] || {};
      return {
        ...base,
        availableFieldKeys: [...(base.fieldKeys || [])],
        fieldKeys: Array.isArray(prev.fieldKeys) && prev.fieldKeys.length
          ? prev.fieldKeys.filter((k) => (base.fieldKeys || []).includes(k))
          : [...(base.fieldKeys || [])],
        enabled: prev.enabled !== false,
        order: Number.isFinite(Number(prev.order)) ? Number(prev.order) : index + 1,
        columns: base.kind === 'scoring' ? cols : undefined,
      };
    });

    // Preserve custom user-added sections
    saved.forEach((s) => {
      if (!list.some((row) => row.id === s.id)) {
        list.push({
          id: s.id || uid('sec'),
          title: s.title || 'Custom Section',
          description: s.description || 'Custom block',
          fieldKeys: Array.isArray(s.fieldKeys) ? s.fieldKeys : [],
          enabled: s.enabled !== false,
          order: Number(s.order) || list.length + 1,
          kind: s.kind || 'custom',
          columns: s.kind === 'scoring' ? (s.columns || defaultColumns(type)) : undefined,
        });
      }
    });

    list.sort((a, b) => a.order - b.order);
    list.forEach((s, i) => { s.order = i + 1; });
    return list;
  }

  function syncFieldsFromSections() {
    const type = eventType();
    const fields = defaultFields(type);
    Object.keys(fields).forEach((k) => { fields[k] = false; });
    const order = [];
    sections.filter((s) => s.enabled).forEach((section) => {
      (section.fieldKeys || []).forEach((key) => {
        if (key in fields) {
          fields[key] = true;
          if (!order.includes(key)) order.push(key);
        }
      });
    });
    // Keep remaining known keys at end (disabled)
    (defaultOrder[type] || Object.keys(fields)).forEach((key) => {
      if (!order.includes(key)) order.push(key);
    });
    currentFields = fields;
    currentOrder = order;
  }

  function collectBuilder() {
    const scoring = sections.find((s) => s.kind === 'scoring');
    return {
      sections: sections.map((s) => ({
        id: s.id,
        title: s.title,
        enabled: !!s.enabled,
        order: s.order,
        fieldKeys: s.fieldKeys || [],
        kind: s.kind || 'fields',
      })),
      scoringColumns: (scoring?.columns || defaultColumns(eventType())).map((c) => ({ ...c })),
      leftLogoDataUrl: builderBrand.leftLogoDataUrl || '',
      rightLogoDataUrl: builderBrand.rightLogoDataUrl || '',
      // Back-compat for older saved templates
      logoDataUrl: builderBrand.leftLogoDataUrl || '',
      orgTitle: builderBrand.orgTitle || '',
      orgTagline: builderBrand.orgTagline || '',
      previewEventId: selectedPreviewEventId || '',
      selectedParticipantIds: selectedParticipantIds.slice(),
    };
  }

  function collectPayload() {
    syncFieldsFromSections();
    return {
      id: document.getElementById('tpl-id').value || null,
      name: document.getElementById('tpl-name').value.trim(),
      event_type: eventType(),
      event_id: document.getElementById('tpl-event-id').value || selectedPreviewEventId || null,
      category: '',
      description: '',
      paper_size: document.getElementById('tpl-paper').value || 'a4',
      orientation: document.getElementById('tpl-orientation').value || 'portrait',
      status: 'active',
      fields: currentFields,
      order: currentOrder,
      builder: collectBuilder(),
    };
  }

  function showList() {
    listPanel.hidden = false;
    listPanel.classList.add('is-active');
    wizardPanel.hidden = true;
    wizardPanel.classList.remove('is-active');
  }

  function showWizard(title) {
    listPanel.hidden = true;
    listPanel.classList.remove('is-active');
    wizardPanel.hidden = false;
    wizardPanel.classList.add('is-active');
    document.getElementById('wizard-title').textContent = title || 'Edit Template';
    document.getElementById('wizard-subtitle').textContent =
      eventType() === 'criteria'
        ? 'Criteria-based scoresheet · sections, columns, and live preview'
        : 'Match-based scoresheet · sections, columns, and live preview';
  }

  function renderSections() {
    if (!sectionList) return;
    sectionList.innerHTML = '';
    sections.forEach((section, index) => {
      const li = document.createElement('li');
      li.className = `ss-section-item${section.id === selectedSectionId ? ' is-selected' : ''}${section.enabled ? '' : ' is-off'}`;
      li.dataset.id = section.id;
      li.draggable = true;
      li.innerHTML = `
        <span class="ss-drag" title="Drag to reorder" aria-hidden="true">⠿</span>
        <button type="button" class="ss-section-main" data-select-section="${escapeAttr(section.id)}">
          <span class="ss-section-num">${index + 1}</span>
          <span class="ss-section-copy">
            <strong>${escapeHtml(section.title)}</strong>
          </span>
        </button>
        <span class="ss-section-actions">
          <button type="button" class="ss-icon-btn" data-edit-section="${escapeAttr(section.id)}" title="Edit">✎</button>
          <button type="button" class="ss-icon-btn danger" data-delete-section="${escapeAttr(section.id)}" title="Remove">🗑</button>
        </span>`;
      li.addEventListener('dragstart', () => { dragSectionId = section.id; li.classList.add('is-dragging'); });
      li.addEventListener('dragend', () => { dragSectionId = null; li.classList.remove('is-dragging'); });
      li.addEventListener('dragover', (e) => { e.preventDefault(); li.classList.add('is-drop'); });
      li.addEventListener('dragleave', () => li.classList.remove('is-drop'));
      li.addEventListener('drop', (e) => {
        e.preventDefault();
        li.classList.remove('is-drop');
        if (!dragSectionId || dragSectionId === section.id) return;
        const from = sections.findIndex((s) => s.id === dragSectionId);
        const to = sections.findIndex((s) => s.id === section.id);
        if (from < 0 || to < 0) return;
        const [moved] = sections.splice(from, 1);
        sections.splice(to, 0, moved);
        sections.forEach((s, i) => { s.order = i + 1; });
        renderSections();
        schedulePreview();
      });
      sectionList.appendChild(li);
    });
  }

  function labelForField(key) {
    const def = defsFor(eventType()).find((d) => d.key === key);
    return def?.label || key;
  }

  function selectedSection() {
    return sections.find((s) => s.id === selectedSectionId) || null;
  }

  function readLogoFile(input, side) {
    const file = input.files && input.files[0];
    if (!file) return;
    if (file.size > 800 * 1024) {
      toast('Logo must be under 800KB.', true);
      input.value = '';
      return;
    }
    const reader = new FileReader();
    reader.onload = () => {
      if (side === 'right') builderBrand.rightLogoDataUrl = String(reader.result || '');
      else builderBrand.leftLogoDataUrl = String(reader.result || '');
      renderEditPanel();
      schedulePreview();
      toast(`${side === 'right' ? 'Right' : 'Left'} logo updated.`);
    };
    reader.readAsDataURL(file);
  }

  function eventTeams(event) {
    if (!event) return [];
    if (Array.isArray(event.teams) && event.teams.length) return event.teams;
    return (event.team_names || []).map((name, i) => ({ id: `name-${i}`, name }));
  }

  function selectedParticipantNames() {
    const event = selectedPreviewEvent();
    const teams = eventTeams(event);
    if (!teams.length) return ['Team Alpha', 'Team Bravo', 'Team Charlie', 'Team Delta', 'Team Echo'];
    if (!selectedParticipantIds.length) return teams.map((t) => t.name);
    const idSet = new Set(selectedParticipantIds.map(String));
    const picked = teams.filter((t) => idSet.has(String(t.id))).map((t) => t.name);
    return picked.length ? picked : teams.map((t) => t.name);
  }

  function updatePreviewEventBadge() {
    const badge = document.getElementById('preview-event-badge');
    const label = document.getElementById('preview-event-label');
    const event = selectedPreviewEvent();
    if (!badge) return;
    if (event) {
      badge.hidden = false;
      badge.textContent = event.name;
      if (label) label.textContent = 'Event details come from the selected Match-Based Event.';
    } else {
      badge.hidden = true;
      if (label) label.textContent = 'Select an event from the table to fill header and event information.';
    }
  }

  function renderEditPanel() {
    const section = selectedSection();
    const title = document.getElementById('edit-component-title');
    const sub = document.getElementById('edit-component-sub');
    if (!section) {
      if (title) title.textContent = 'Edit Component';
      if (sub) sub.textContent = 'Select a section from the left to configure it.';
      editBody.innerHTML = '<p class="ss-empty-hint">Choose a section to edit its fields or columns.</p>';
      return;
    }
    if (title) title.textContent = `Edit Component: ${section.title}`;
    if (sub) sub.textContent = 'Configure this section. Changes update the live preview.';

    if (section.kind === 'scoring') {
      renderScoringEditor(section);
      return;
    }

    const keys = section.availableFieldKeys || section.fieldKeys || [];
    const isHeader = section.id === 'header';
    const isInfo = section.id === 'event_info';
    const isParticipants = section.id === 'participants';
    const event = selectedPreviewEvent();
    const leftLogo = builderBrand.leftLogoDataUrl || schoolBranding.school_logo_url || '';
    const rightLogo = builderBrand.rightLogoDataUrl || '';
    const teams = eventTeams(event);

    let bodyExtra = '';
    if (isHeader) {
      bodyExtra = `
      <div class="ss-edit-block ss-logo-block">
        <h5>Header Branding</h5>
        <label class="ss-field">Header Name
          <input type="text" id="org-title-input" maxlength="120"
            value="${escapeAttr(builderBrand.orgTitle || schoolBranding.intramurals_name || schoolBranding.school_name || '')}"
            placeholder="e.g. University Sports Fest 2026">
        </label>
        <label class="ss-field">Tagline
          <input type="text" id="org-tagline-input" maxlength="120"
            value="${escapeAttr(builderBrand.orgTagline || 'UNITY. PASSION. EXCELLENCE.')}">
        </label>
        <div class="ss-dual-logo-grid">
          <div>
            <label class="ss-field">Left Logo
              <input type="file" id="logo-left-input" accept="image/png,image/jpeg,image/webp,image/gif">
            </label>
            <div class="ss-logo-preview-row">
              <div class="ss-logo-preview">
                ${leftLogo ? `<img src="${escapeAttr(leftLogo)}" alt="Left logo">` : '<span>No logo</span>'}
              </div>
              <button type="button" class="ss-btn ss-btn-secondary" id="btn-clear-left-logo">Remove</button>
            </div>
          </div>
          <div>
            <label class="ss-field">Right Logo
              <input type="file" id="logo-right-input" accept="image/png,image/jpeg,image/webp,image/gif">
            </label>
            <div class="ss-logo-preview-row">
              <div class="ss-logo-preview">
                ${rightLogo ? `<img src="${escapeAttr(rightLogo)}" alt="Right logo">` : '<span>No logo</span>'}
              </div>
              <button type="button" class="ss-btn ss-btn-secondary" id="btn-clear-right-logo">Remove</button>
            </div>
          </div>
        </div>
        <small class="ss-hint">PNG/JPG under 800KB each. School logo is used on the left when none is uploaded.</small>
      </div>`;
    } else if (isInfo) {
      bodyExtra = `
      <div class="ss-edit-block ss-event-detail-block">
        <h5>Selected Event Details</h5>
        ${event ? `
        <dl class="ss-event-detail-grid">
          <div><dt>Event Name</dt><dd>${escapeHtml(event.name || '—')}</dd></div>
          <div><dt>Date</dt><dd>${escapeHtml(event.start_date_display || '—')}</dd></div>
          <div><dt>Time</dt><dd>${escapeHtml(event.time_display || '—')}</dd></div>
          <div><dt>Category</dt><dd>${escapeHtml(event.category || '—')}</dd></div>
          <div><dt>Venue</dt><dd>${escapeHtml(event.venue || '—')}</dd></div>
          <div><dt>Division</dt><dd>${escapeHtml(event.division || '—')}</dd></div>
          <div><dt>Format</dt><dd>${escapeHtml(event.tournament_type_label || '—')}</dd></div>
          <div><dt>Sport / Type</dt><dd>${escapeHtml(event.sport_label || '—')}</dd></div>
        </dl>` : '<p class="ss-empty-hint">No event selected. Use <strong>Create Scoresheet</strong> from the Match-Based Events table.</p>'}
      </div>`;
    } else if (isParticipants) {
      bodyExtra = `
      <div class="ss-edit-block">
        <h5>Select Participants</h5>
        ${teams.length ? `
        <div class="ss-participant-toolbar">
          <button type="button" class="ss-btn ss-btn-secondary" id="btn-select-all-participants">Select All</button>
          <button type="button" class="ss-btn ss-btn-secondary" id="btn-clear-participants">Clear</button>
        </div>
        <ul class="ss-mini-field-list ss-participant-list">
          ${teams.map((team) => `
            <li>
              <label class="ss-check-inline">
                <input type="checkbox" data-participant-id="${escapeAttr(team.id)}"
                  ${!selectedParticipantIds.length || selectedParticipantIds.map(String).includes(String(team.id)) ? 'checked' : ''}>
                <span>${escapeHtml(team.name)}</span>
              </label>
            </li>`).join('')}
        </ul>` : '<p class="ss-empty-hint">This event has no bracket teams yet. Add teams in Match-Based Event first.</p>'}
      </div>`;
    }

    editBody.innerHTML = `
      <div class="ss-edit-block">
        <label class="ss-field">Section Title
          <input type="text" id="section-title-input" value="${escapeAttr(section.title)}" maxlength="80">
        </label>
        <label class="ss-check-inline ss-enable-toggle">
          <input type="checkbox" id="section-enabled-input" ${section.enabled ? 'checked' : ''}>
          <span>Include this section in the scoresheet</span>
        </label>
      </div>
      ${bodyExtra}
      ${!isParticipants ? `
      <div class="ss-edit-block">
        <h5>Fields in this section</h5>
        <ul class="ss-mini-field-list">
          ${keys.map((key) => `
            <li>
              <label class="ss-check-inline">
                <input type="checkbox" data-section-field="${escapeAttr(key)}" ${(section.fieldKeys || []).includes(key) ? 'checked' : ''}>
                <span>${escapeHtml(labelForField(key))}</span>
              </label>
            </li>`).join('') || '<li class="ss-empty-hint">No mapped fields.</li>'}
        </ul>
      </div>` : ''}
    `;

    document.getElementById('section-title-input')?.addEventListener('input', (e) => {
      section.title = e.target.value;
      renderSections();
      schedulePreview();
    });
    document.getElementById('section-enabled-input')?.addEventListener('change', (e) => {
      section.enabled = e.target.checked;
      syncFieldsFromSections();
      renderSections();
      renderEditPanel();
      schedulePreview();
    });
    document.getElementById('org-title-input')?.addEventListener('input', (e) => {
      builderBrand.orgTitle = e.target.value;
      schedulePreview();
    });
    document.getElementById('org-tagline-input')?.addEventListener('input', (e) => {
      builderBrand.orgTagline = e.target.value;
      schedulePreview();
    });
    document.getElementById('logo-left-input')?.addEventListener('change', (e) => readLogoFile(e.target, 'left'));
    document.getElementById('logo-right-input')?.addEventListener('change', (e) => readLogoFile(e.target, 'right'));
    document.getElementById('btn-clear-left-logo')?.addEventListener('click', () => {
      builderBrand.leftLogoDataUrl = '';
      renderEditPanel();
      schedulePreview();
    });
    document.getElementById('btn-clear-right-logo')?.addEventListener('click', () => {
      builderBrand.rightLogoDataUrl = '';
      renderEditPanel();
      schedulePreview();
    });
    document.getElementById('btn-select-all-participants')?.addEventListener('click', () => {
      selectedParticipantIds = teams.map((t) => String(t.id));
      renderEditPanel();
      schedulePreview();
    });
    document.getElementById('btn-clear-participants')?.addEventListener('click', () => {
      selectedParticipantIds = [];
      renderEditPanel();
      schedulePreview();
    });
    editBody.querySelectorAll('[data-participant-id]').forEach((box) => {
      box.addEventListener('change', () => {
        const checked = Array.from(editBody.querySelectorAll('[data-participant-id]:checked'))
          .map((el) => el.getAttribute('data-participant-id'));
        selectedParticipantIds = checked;
        schedulePreview();
      });
    });
    editBody.querySelectorAll('[data-section-field]').forEach((box) => {
      box.addEventListener('change', () => {
        const key = box.getAttribute('data-section-field');
        if (!section.enabled) return;
        if (box.checked) {
          if (!section.fieldKeys.includes(key)) section.fieldKeys.push(key);
        } else {
          section.fieldKeys = (section.fieldKeys || []).filter((k) => k !== key);
        }
        syncFieldsFromSections();
        schedulePreview();
      });
    });
  }

  function renderScoringEditor(section) {
    if (!Array.isArray(section.columns) || !section.columns.length) {
      section.columns = defaultColumns(eventType());
    }
    const columns = section.columns;
    editBody.innerHTML = `
      <div class="ss-edit-block ss-edit-block--row">
        <label class="ss-check-inline">
          <input type="checkbox" id="section-enabled-input" ${section.enabled ? 'checked' : ''}>
          <span>Include Scoring Table</span>
        </label>
        <button type="button" class="ss-btn ss-btn-secondary" id="btn-add-column">+ Add Column</button>
      </div>
      <div class="ss-columns-list" id="columns-list">
        ${columns.map((col) => `
          <button type="button" class="ss-column-chip${editingColumnId === col.id ? ' is-active' : ''}" data-edit-column="${escapeAttr(col.id)}">
            <strong>${escapeHtml(col.name)}</strong>
            <span>${escapeHtml(col.dataType)}${col.calc && col.calc !== 'none' ? ' · fx' : ''}</span>
          </button>`).join('')}
      </div>
      <div class="ss-column-form" id="column-form"></div>`;

    document.getElementById('section-enabled-input')?.addEventListener('change', (e) => {
      section.enabled = e.target.checked;
      syncFieldsFromSections();
      renderSections();
      schedulePreview();
    });
    document.getElementById('btn-add-column')?.addEventListener('click', () => {
      const col = {
        id: uid('col'),
        name: `Column ${columns.length + 1}`,
        dataType: 'number',
        width: 12,
        align: 'center',
        calc: 'none',
        required: false,
        showPdf: true,
      };
      columns.push(col);
      editingColumnId = col.id;
      renderEditPanel();
      schedulePreview();
    });
    editBody.querySelectorAll('[data-edit-column]').forEach((btn) => {
      btn.addEventListener('click', () => {
        editingColumnId = btn.getAttribute('data-edit-column');
        renderEditPanel();
      });
    });
    renderColumnForm(section);
  }

  function renderColumnForm(section) {
    const host = document.getElementById('column-form');
    if (!host) return;
    const col = (section.columns || []).find((c) => c.id === editingColumnId) || section.columns[0];
    if (!col) {
      host.innerHTML = '<p class="ss-empty-hint">No columns yet. Click + Add Column.</p>';
      return;
    }
    editingColumnId = col.id;
    host.innerHTML = `
      <h5>Add / Edit Column</h5>
      <div class="ss-form-grid">
        <label class="ss-field">Column Name
          <input type="text" id="col-name" value="${escapeAttr(col.name)}" maxlength="60">
        </label>
        <label class="ss-field">Data Type
          <select id="col-type">
            <option value="text" ${col.dataType === 'text' ? 'selected' : ''}>Text</option>
            <option value="number" ${col.dataType === 'number' ? 'selected' : ''}>Number</option>
          </select>
        </label>
        <label class="ss-field">Width
          <input type="number" id="col-width" min="5" max="60" value="${escapeAttr(col.width || 12)}">
          <small class="ss-hint">Percent of table width</small>
        </label>
        <label class="ss-field">Alignment
          <select id="col-align">
            <option value="left" ${col.align === 'left' ? 'selected' : ''}>Left</option>
            <option value="center" ${col.align === 'center' ? 'selected' : ''}>Center</option>
            <option value="right" ${col.align === 'right' ? 'selected' : ''}>Right</option>
          </select>
        </label>
        <label class="ss-field">Calculation (Optional)
          <select id="col-calc">
            <option value="none" ${col.calc === 'none' ? 'selected' : ''}>None</option>
            <option value="sum" ${col.calc === 'sum' ? 'selected' : ''}>Sum / Total</option>
            <option value="rank" ${col.calc === 'rank' ? 'selected' : ''}>Rank</option>
          </select>
        </label>
      </div>
      <div class="ss-check-row">
        <label class="ss-check-inline"><input type="checkbox" id="col-required" ${col.required ? 'checked' : ''}><span>Required</span></label>
        <label class="ss-check-inline"><input type="checkbox" id="col-show-pdf" ${col.showPdf !== false ? 'checked' : ''}><span>Show in PDF</span></label>
      </div>
      <div class="ss-form-actions">
        <button type="button" class="ss-btn ss-btn-secondary" id="btn-cancel-column">Cancel</button>
        <button type="button" class="ss-btn ss-btn-secondary danger-outline" id="btn-delete-column">Delete</button>
        <button type="button" class="ss-btn ss-btn-primary" id="btn-save-column">Save Column</button>
      </div>`;

    document.getElementById('btn-save-column')?.addEventListener('click', () => {
      col.name = document.getElementById('col-name').value.trim() || col.name;
      col.dataType = document.getElementById('col-type').value;
      col.width = Number(document.getElementById('col-width').value) || 12;
      col.align = document.getElementById('col-align').value;
      col.calc = document.getElementById('col-calc').value;
      col.required = document.getElementById('col-required').checked;
      col.showPdf = document.getElementById('col-show-pdf').checked;
      editingColumnId = col.id;
      renderEditPanel();
      schedulePreview();
      toast('Column saved.');
    });
    document.getElementById('btn-cancel-column')?.addEventListener('click', () => {
      editingColumnId = null;
      renderEditPanel();
    });
    document.getElementById('btn-delete-column')?.addEventListener('click', () => {
      if ((section.columns || []).length <= 1) {
        toast('Keep at least one scoring column.', true);
        return;
      }
      section.columns = section.columns.filter((c) => c.id !== col.id);
      editingColumnId = section.columns[0]?.id || null;
      renderEditPanel();
      schedulePreview();
    });
  }

  function selectedPreviewEvent() {
    if (!selectedPreviewEventId) return null;
    return matchPreviewEvents.find((e) => String(e.id) === String(selectedPreviewEventId)) || null;
  }

  function formatPreviewDate(isoOrDisplay) {
    if (!isoOrDisplay) return '—';
    if (/[A-Za-z]/.test(isoOrDisplay)) return isoOrDisplay;
    const d = new Date(`${isoOrDisplay}T00:00:00`);
    if (Number.isNaN(d.getTime())) return isoOrDisplay;
    return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
  }

  function renderMatchEventsTable(filterText) {
    const tbody = document.getElementById('events-tbody');
    if (!tbody) return;
    const q = String(filterText || '').trim().toLowerCase();
    const rows = matchPreviewEvents.filter((ev) => {
      if (!q) return true;
      const hay = [ev.name, ev.venue, ev.division, ev.category, ev.tournament_type_label, ev.sport_label]
        .join(' ').toLowerCase();
      return hay.includes(q);
    });
    if (!rows.length) {
      tbody.innerHTML = `<tr class="ss-empty-row"><td colspan="9" class="ss-empty">${
        matchPreviewEvents.length
          ? 'No events match your search.'
          : 'No Match-Based Events yet. Create one under Events → Match-Based Event.'
      }</td></tr>`;
      return;
    }
    tbody.innerHTML = rows.map((ev) => {
      const hasTpl = !!ev.scoresheet_template_id;
      return `
        <tr data-event-id="${escapeAttr(ev.id)}">
          <td><strong>${escapeHtml(ev.name)}</strong></td>
          <td>${escapeHtml(ev.start_date_display || '—')}</td>
          <td>${escapeHtml(ev.time_display || '—')}</td>
          <td>${escapeHtml(ev.category || '—')}</td>
          <td>${escapeHtml(ev.venue || '—')}</td>
          <td>${escapeHtml(ev.division || '—')}</td>
          <td>${escapeHtml(ev.tournament_type_label || '—')}</td>
          <td>${hasTpl
            ? `<span class="ss-status-pill is-ready">${escapeHtml(ev.scoresheet_template_name || 'Ready')}</span>`
            : '<span class="ss-status-pill is-pending">Not created</span>'}</td>
          <td>
            <div class="ss-row-actions">
              ${hasTpl
                ? `<button type="button" class="ss-btn ss-btn-secondary ss-btn-sm" data-edit-event-scoresheet="${escapeAttr(ev.id)}" data-template-id="${escapeAttr(ev.scoresheet_template_id)}">Edit Scoresheet</button>`
                : `<button type="button" class="ss-btn ss-btn-primary ss-btn-sm" data-create-event-scoresheet="${escapeAttr(ev.id)}">Create Scoresheet</button>`}
            </div>
          </td>
        </tr>`;
    }).join('');
  }

  function populatePreviewEventSelect() {
    updatePreviewEventBadge();
  }

  function sampleRows(columns) {
    let names = selectedParticipantNames().slice(0, 8);
    if (!names.length) names = ['Team Alpha', 'Team Bravo', 'Team Charlie', 'Team Delta', 'Team Echo'];
    while (names.length < Math.min(5, Math.max(names.length, 2))) {
      names.push(`Team ${String.fromCharCode(65 + names.length)}`);
    }
    return names.map((name, idx) => {
      const row = {};
      columns.forEach((col) => {
        if (col.dataType === 'text' || col.id === 'participant' || col.id === 'criterion') {
          row[col.id] = col.id === 'criterion' ? `Criterion ${idx + 1}` : name;
        } else if (col.calc === 'sum') {
          row[col.id] = 70 + idx * 3;
        } else if (col.calc === 'rank') {
          row[col.id] = idx + 1;
        } else if (col.id === 'penalty') {
          row[col.id] = idx === 2 ? 2 : 0;
        } else if (col.id === 'weight') {
          row[col.id] = 20;
        } else if (col.id === 'max') {
          row[col.id] = 100;
        } else {
          row[col.id] = 18 + ((idx + 1) * 3) % 10;
        }
      });
      return row;
    });
  }

  function sectionFieldOn(section, key) {
    if (!section) return false;
    const keys = section.fieldKeys || [];
    return !keys.length || keys.includes(key);
  }

  function logoMarkup(url, fallbackText) {
    if (url) return `<img class="ss-sheet-logo-img" src="${escapeAttr(url)}" alt="Logo">`;
    return `<div class="ss-sheet-logo">${escapeHtml((fallbackText || 'LOGO').slice(0, 4).toUpperCase())}</div>`;
  }

  function renderRichPreview() {
    if (!richPreview) return;
    updatePreviewEventBadge();
    const type = eventType();
    const event = type === 'match' ? selectedPreviewEvent() : null;
    const tplName = document.getElementById('tpl-name').value.trim() || 'Official Scoresheet';
    const eventTitle = event?.name || tplName;
    const orgTitle = (
      builderBrand.orgTitle
      || schoolBranding.intramurals_name
      || schoolBranding.school_name
      || 'UNIVERSITY SPORTS FEST'
    );
    const orgTagline = builderBrand.orgTagline || 'UNITY. PASSION. EXCELLENCE.';
    const leftLogo = builderBrand.leftLogoDataUrl || schoolBranding.school_logo_url || '';
    const rightLogo = builderBrand.rightLogoDataUrl || '';
    const enabled = sections.filter((s) => s.enabled);
    const headerSec = enabled.find((s) => s.id === 'header');
    const infoSec = enabled.find((s) => s.id === 'event_info');
    const scoring = enabled.find((s) => s.kind === 'scoring');
    const columns = (scoring?.columns || defaultColumns(type)).filter((c) => c.showPdf !== false);
    const rows = sampleRows(columns);
    const hasHeader = !!headerSec;
    const hasInfo = !!infoSec;
    const hasParticipants = enabled.some((s) => s.id === 'participants');
    const hasResult = enabled.some((s) => s.id === 'result');
    const hasOfficials = enabled.some((s) => s.id === 'officials');
    const showLogo = hasHeader && sectionFieldOn(headerSec, 'school_logo');
    const showEventName = hasHeader && sectionFieldOn(headerSec, 'event_name');
    const participantNames = selectedParticipantNames();

    const infoDate = event ? (event.start_date_display || formatPreviewDate(event.start_date)) : 'Aug 22, 2026';
    const infoTime = event ? (event.time_display || '—') : '—';
    const infoType = event
      ? (event.sport_label || event.classification_label || 'Team Sport')
      : (type === 'criteria' ? 'Criteria-Based' : 'Team Sport');
    const infoCategory = event?.category || 'Sports';
    const infoVenue = event?.venue || 'Covered Court';
    const infoDivision = event?.division || (type === 'criteria' ? 'Open' : "Men's Division");
    const infoFormat = event?.tournament_type_label || 'Single Elimination';
    const teamA = participantNames[0] || event?.team_a || 'Engineering Tigers';
    const teamB = participantNames[1] || event?.team_b || 'Science Hawks';

    const infoCells = [];
    if (sectionFieldOn(infoSec, 'date')) {
      infoCells.push(`<div><span>Date</span><strong>${escapeHtml(infoDate)}</strong></div>`);
    }
    if (sectionFieldOn(infoSec, 'time')) {
      infoCells.push(`<div><span>Time</span><strong>${escapeHtml(infoTime)}</strong></div>`);
    }
    if (sectionFieldOn(infoSec, 'event_classification') || sectionFieldOn(infoSec, 'stage_name')) {
      infoCells.push(`<div><span>Category</span><strong>${escapeHtml(infoCategory)}</strong></div>`);
      infoCells.push(`<div><span>Event Type</span><strong>${escapeHtml(infoType)}</strong></div>`);
    }
    if (sectionFieldOn(infoSec, 'venue') || sectionFieldOn(infoSec, 'playing_area')) {
      infoCells.push(`<div><span>Venue</span><strong>${escapeHtml(infoVenue)}</strong></div>`);
    }
    if (sectionFieldOn(infoSec, 'division')) {
      infoCells.push(`<div><span>Division</span><strong>${escapeHtml(infoDivision)}</strong></div>`);
    }
    if (sectionFieldOn(infoSec, 'tournament_format') && type === 'match') {
      infoCells.push(`<div><span>Format</span><strong>${escapeHtml(infoFormat)}</strong></div>`);
    }
    if (!infoCells.length && hasInfo) {
      infoCells.push(
        `<div><span>Date</span><strong>${escapeHtml(infoDate)}</strong></div>`,
        `<div><span>Time</span><strong>${escapeHtml(infoTime)}</strong></div>`,
        `<div><span>Category</span><strong>${escapeHtml(infoCategory)}</strong></div>`,
        `<div><span>Venue</span><strong>${escapeHtml(infoVenue)}</strong></div>`,
        `<div><span>Division</span><strong>${escapeHtml(infoDivision)}</strong></div>`,
        `<div><span>Format</span><strong>${escapeHtml(infoFormat)}</strong></div>`
      );
    }

    const colHeads = columns.map((c) =>
      `<th style="width:${Number(c.width) || 12}%;text-align:${c.align || 'center'}">${escapeHtml(c.name)}${c.calc && c.calc !== 'none' ? ' <em>fx</em>' : ''}</th>`
    ).join('');
    const bodyRows = rows.map((row) => `
      <tr>${columns.map((c) =>
        `<td style="text-align:${c.align || 'center'}">${escapeHtml(row[c.id])}</td>`
      ).join('')}</tr>`).join('');

    const placers = rows.slice(0, 3).map((row, i) => {
      const label = i === 0 ? '1st' : i === 1 ? '2nd' : '3rd';
      const totalCol = columns.find((c) => c.calc === 'sum') || columns[columns.length - 2];
      const nameCol = columns.find((c) => c.dataType === 'text') || columns[0];
      return `<tr><td>${label}</td><td>${escapeHtml(row[nameCol.id])}</td><td>${escapeHtml(row[totalCol?.id])}</td></tr>`;
    }).join('');

    const leftHtml = showLogo
      ? logoMarkup(leftLogo, schoolBranding.school_name || 'LOGO')
      : '<div class="ss-sheet-logo ss-sheet-logo--spacer" aria-hidden="true"></div>';
    const rightHtml = showLogo
      ? (rightLogo
        ? logoMarkup(rightLogo, '2026')
        : `<div class="ss-sheet-logo ss-sheet-logo--alt">${escapeHtml((event?.start_date || '').slice(0, 4) || '2026')}</div>`)
      : '<div class="ss-sheet-logo ss-sheet-logo--spacer" aria-hidden="true"></div>';

    richPreview.innerHTML = `
      <article class="ss-sheet">
        ${hasHeader ? `
        <header class="ss-sheet-banner">
          ${leftHtml}
          <div class="ss-sheet-banner-copy">
            <strong>${escapeHtml(orgTitle)}</strong>
            <span>${escapeHtml(orgTagline)}</span>
            ${showEventName ? `<em class="ss-sheet-event-name">${escapeHtml(eventTitle)}</em>` : ''}
          </div>
          ${rightHtml}
        </header>` : ''}
        ${hasInfo ? `
        <section class="ss-sheet-info">
          <div class="ss-sheet-info-bar">${escapeHtml(String(eventTitle).toUpperCase())}</div>
          <div class="ss-sheet-info-grid">${infoCells.join('')}</div>
        </section>` : ''}
        ${hasParticipants && type === 'match' ? `
        <section class="ss-sheet-teams">
          <div><small>Team A</small><strong>${escapeHtml(teamA)}</strong></div>
          <div class="ss-sheet-vs">VS</div>
          <div><small>Team B</small><strong>${escapeHtml(teamB)}</strong></div>
        </section>
        ${participantNames.length > 2 ? `
        <section class="ss-sheet-participant-list">
          <h5>Participants</h5>
          <ul>${participantNames.map((n) => `<li>${escapeHtml(n)}</li>`).join('')}</ul>
        </section>` : ''}` : ''}
        ${hasParticipants && type === 'criteria' ? `
        <section class="ss-sheet-contestant">
          <div><span>No.</span><strong>07</strong></div>
          <div><span>Contestant</span><strong>Alex Rivera</strong></div>
          <div><span>Department</span><strong>College of Engineering</strong></div>
        </section>` : ''}
        ${scoring ? `
        <section class="ss-sheet-table-wrap">
          <table class="ss-sheet-table">
            <thead><tr>${colHeads}</tr></thead>
            <tbody>${bodyRows}</tbody>
          </table>
        </section>` : ''}
        ${hasResult ? `
        <section class="ss-sheet-result">
          <h5>Result / Top Placers</h5>
          <table>
            <thead><tr><th>Place</th><th>Participant</th><th>Total</th></tr></thead>
            <tbody>${placers}</tbody>
          </table>
        </section>` : ''}
        ${hasOfficials ? `
        <section class="ss-sheet-officials">
          ${(type === 'criteria'
            ? ['Head Judge', 'Judge', 'Faculty In-Charge']
            : ['Head Judge', 'Scorekeeper', 'Timekeeper']
          ).map((label) => `
            <div class="ss-sheet-sig">
              <span class="ss-sheet-sig-line"></span>
              <strong>${label}</strong>
            </div>`).join('')}
        </section>
        <p class="ss-sheet-footer">Thank you for your fair play and sportsmanship!</p>` : ''}
      </article>`;
  }

  function schedulePreview() {
    clearTimeout(previewTimer);
    previewTimer = setTimeout(() => {
      syncFieldsFromSections();
      renderRichPreview();
    }, 80);
  }

  function selectSection(id) {
    selectedSectionId = id;
    editingColumnId = null;
    renderSections();
    renderEditPanel();
  }

  function loadTemplateIntoForm(tpl, options = {}) {
    const boundEventId = options.eventId
      || tpl?.bound_event_id
      || tpl?.builder?.previewEventId
      || '';
    document.getElementById('tpl-id').value = tpl?.id || '';
    document.getElementById('tpl-event-id').value = boundEventId ? String(boundEventId) : '';
    document.getElementById('tpl-name').value = tpl?.name || '';
    document.getElementById('tpl-paper').value = tpl?.paper_size || 'a4';
    document.getElementById('tpl-orientation').value = tpl?.orientation || 'portrait';
    const type = tpl?.event_type || 'match';
    document.querySelectorAll('input[name="event_type"]').forEach((r) => { r.checked = r.value === type; });
    currentFields = { ...defaultFields(type), ...(tpl?.fields || {}) };
    currentOrder = (tpl?.order && tpl.order.length) ? tpl.order : (defaultOrder[type] || []);
    const builder = tpl?.builder || {};
    builderBrand = {
      leftLogoDataUrl: builder.leftLogoDataUrl || builder.logoDataUrl || '',
      rightLogoDataUrl: builder.rightLogoDataUrl || '',
      orgTitle: builder.orgTitle || schoolBranding.intramurals_name || schoolBranding.school_name || '',
      orgTagline: builder.orgTagline || 'UNITY. PASSION. EXCELLENCE.',
    };
    selectedPreviewEventId = boundEventId ? String(boundEventId) : '';
    selectedParticipantIds = Array.isArray(builder.selectedParticipantIds)
      ? builder.selectedParticipantIds.map(String)
      : [];
    if (!selectedParticipantIds.length && selectedPreviewEventId) {
      selectedParticipantIds = eventTeams(selectedPreviewEvent()).map((t) => String(t.id));
    }
    sections = buildDefaultSections(type, builder);
    if (!tpl?.builder?.sections?.length) {
      sections.forEach((section) => {
        const keys = section.fieldKeys || [];
        section.enabled = !keys.length || keys.some((k) => currentFields[k] !== false);
      });
    }
    selectedSectionId = sections[0]?.id || null;
    editingColumnId = null;
    const wizardLabel = options.wizardTitle
      || (tpl?.id ? `Edit Template: ${tpl.name || 'Scoresheet'}` : 'Create Scoresheet');
    showWizard(wizardLabel);
    populatePreviewEventSelect();
    renderSections();
    renderEditPanel();
    schedulePreview();
  }

  function openScoresheetForEvent(eventId, templateId) {
    const event = matchPreviewEvents.find((e) => String(e.id) === String(eventId));
    if (!event) {
      toast('Event not found.', true);
      return;
    }
    if (templateId) {
      const tpl = findTemplate(templateId);
      if (tpl) {
        loadTemplateIntoForm(tpl, {
          eventId: event.id,
          wizardTitle: `Edit Scoresheet: ${event.name}`,
        });
        return;
      }
      loadTemplateIntoForm({
        id: templateId,
        name: event.scoresheet_template_name || `${event.name} Scoresheet`,
        event_type: 'match',
        paper_size: 'a4',
        orientation: 'portrait',
        fields: defaultFields('match'),
        order: defaultOrder.match || [],
        builder: { previewEventId: String(event.id) },
        bound_event_id: event.id,
      }, {
        eventId: event.id,
        wizardTitle: `Edit Scoresheet: ${event.name}`,
      });
      return;
    }
    loadTemplateIntoForm({
      name: `${event.name} Scoresheet`,
      event_type: 'match',
      paper_size: 'a4',
      orientation: 'portrait',
      fields: defaultFields('match'),
      order: defaultOrder.match || [],
      builder: {
        previewEventId: String(event.id),
        orgTitle: schoolBranding.intramurals_name || schoolBranding.school_name || '',
        selectedParticipantIds: eventTeams(event).map((t) => String(t.id)),
      },
      bound_event_id: event.id,
    }, {
      eventId: event.id,
      wizardTitle: `Create Scoresheet: ${event.name}`,
    });
  }

  function openViewDialog(tpl) {
    viewingId = tpl.id;
    document.getElementById('view-dialog-title').textContent = tpl.name;
    document.getElementById('view-meta').innerHTML = `
      <dt>Event Type</dt><dd>${escapeHtml(tpl.event_type_label)}</dd>
      <dt>Paper</dt><dd>${escapeHtml((tpl.paper_size || 'a4').toUpperCase())} · ${escapeHtml(tpl.orientation || 'portrait')}</dd>
      <dt>Last Updated</dt><dd>${escapeHtml(tpl.updated_at || '—')}</dd>`;

    const prevSections = sections.map((s) => ({ ...s, columns: s.columns ? s.columns.map((c) => ({ ...c })) : undefined }));
    const prevType = eventType();
    const prevName = document.getElementById('tpl-name').value;

    sections = buildDefaultSections(tpl.event_type || 'match', tpl.builder || {});
    document.querySelectorAll('input[name="event_type"]').forEach((r) => {
      r.checked = r.value === (tpl.event_type || 'match');
    });
    document.getElementById('tpl-name').value = tpl.name || 'Official Scoresheet';
    renderRichPreview();

    const viewPreview = document.getElementById('view-preview');
    if (viewPreview) {
      viewPreview.className = 'ss-rich-preview ss-rich-preview--dialog';
      viewPreview.innerHTML = richPreview.innerHTML;
    }

    sections = prevSections;
    document.querySelectorAll('input[name="event_type"]').forEach((r) => {
      r.checked = r.value === prevType;
    });
    document.getElementById('tpl-name').value = prevName;
    if (wizardPanel && !wizardPanel.hidden) schedulePreview();

    viewDialog?.showModal();
  }

  async function downloadSamplePdf() {
    const payload = collectPayload();
    const res = await fetch(urls.sample, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf },
      body: JSON.stringify({
        event_type: payload.event_type,
        orientation: payload.orientation,
        paper_size: payload.paper_size,
        fields: payload.fields,
        order: payload.order,
      }),
    });
    if (!res.ok) throw new Error('Failed to download sample PDF');
    const blob = await res.blob();
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'sample-scoresheet.pdf';
    a.click();
    URL.revokeObjectURL(a.href);
  }

  // List events
  document.getElementById('ss-select-all')?.addEventListener('change', (e) => {
    document.querySelectorAll('.ss-row-check').forEach((cb) => { cb.checked = e.target.checked; });
    updateBulkBar();
  });
  document.getElementById('templates-tbody')?.addEventListener('change', (e) => {
    if (e.target.classList.contains('ss-row-check')) updateBulkBar();
  });
  document.getElementById('btn-bulk-delete')?.addEventListener('click', async () => {
    const selected = selectedCheckboxes();
    if (!selected.length) return;
    const names = selected.map((cb) => cb.getAttribute('data-name') || 'template');
    const assigned = selected.reduce((sum, cb) => sum + Number(cb.getAttribute('data-assigned') || 0), 0);
    let msg = `Delete ${selected.length} selected template(s)?\n\n${names.slice(0, 8).join('\n')}`;
    if (names.length > 8) msg += `\n…and ${names.length - 8} more`;
    msg += '\n\nThis cannot be undone.';
    if (assigned) msg += `\n\n${assigned} event assignment(s) will fall back to auto-matched templates.`;
    if (!window.confirm(msg)) return;
    const ids = selected.map((cb) => cb.value);
    try {
      const data = await api(urls.bulkDelete, { method: 'POST', body: JSON.stringify({ ids }) });
      toast(data.message || 'Templates deleted.');
      removeTemplateRows(data.deleted_ids || ids);
      setTimeout(() => window.location.reload(), 600);
    } catch (err) {
      toast(err.message || 'Bulk delete failed.', true);
    }
  });

  document.getElementById('btn-create-template')?.addEventListener('click', () => {
    loadTemplateIntoForm({
      event_type: 'match',
      paper_size: 'a4',
      orientation: 'portrait',
      fields: defaultFields('match'),
      order: defaultOrder.match || [],
      builder: {},
    }, { wizardTitle: 'Create Blank Template' });
  });

  document.getElementById('btn-cancel-wizard')?.addEventListener('click', showList);

  document.getElementById('event-search')?.addEventListener('input', (e) => {
    renderMatchEventsTable(e.target.value);
  });

  document.getElementById('events-tbody')?.addEventListener('click', (e) => {
    const createBtn = e.target.closest('[data-create-event-scoresheet]');
    const editBtn = e.target.closest('[data-edit-event-scoresheet]');
    if (createBtn) {
      openScoresheetForEvent(createBtn.getAttribute('data-create-event-scoresheet'));
      return;
    }
    if (editBtn) {
      openScoresheetForEvent(
        editBtn.getAttribute('data-edit-event-scoresheet'),
        editBtn.getAttribute('data-template-id')
      );
    }
  });

  document.querySelectorAll('input[name="event_type"]').forEach((r) => {
    r.addEventListener('change', () => {
      const type = eventType();
      currentFields = defaultFields(type);
      currentOrder = defaultOrder[type] || [];
      sections = buildDefaultSections(type, {});
      selectedSectionId = sections[0]?.id || null;
      editingColumnId = null;
      document.getElementById('wizard-subtitle').textContent =
        type === 'criteria'
          ? 'Criteria-based scoresheet · sections, columns, and live preview'
          : 'Match-based scoresheet · sections, columns, and live preview';
      populatePreviewEventSelect();
      renderSections();
      renderEditPanel();
      schedulePreview();
    });
  });

  document.getElementById('tpl-name')?.addEventListener('input', schedulePreview);

  sectionList?.addEventListener('click', (e) => {
    const selectBtn = e.target.closest('[data-select-section]');
    const editBtn = e.target.closest('[data-edit-section]');
    const deleteBtn = e.target.closest('[data-delete-section]');
    if (selectBtn || editBtn) {
      selectSection((selectBtn || editBtn).getAttribute(selectBtn ? 'data-select-section' : 'data-edit-section'));
      return;
    }
    if (deleteBtn) {
      const id = deleteBtn.getAttribute('data-delete-section');
      const section = sections.find((s) => s.id === id);
      if (!section) return;
      if (SECTION_CATALOG[eventType()]?.some((s) => s.id === id)) {
        section.enabled = false;
        toast(`"${section.title}" hidden from the scoresheet.`);
      } else {
        sections = sections.filter((s) => s.id !== id);
        toast('Custom section removed.');
      }
      if (selectedSectionId === id) {
        selectedSectionId = sections.find((s) => s.enabled)?.id || sections[0]?.id || null;
      }
      sections.forEach((s, i) => { s.order = i + 1; });
      syncFieldsFromSections();
      renderSections();
      renderEditPanel();
      schedulePreview();
    }
  });

  document.getElementById('btn-add-section')?.addEventListener('click', () => {
    const title = window.prompt('New section title', 'Custom Section');
    if (!title || !title.trim()) return;
    const section = {
      id: uid('sec'),
      title: title.trim(),
      fieldKeys: [],
      enabled: true,
      order: sections.length + 1,
      kind: 'custom',
    };
    sections.push(section);
    selectedSectionId = section.id;
    renderSections();
    renderEditPanel();
    schedulePreview();
  });

  document.getElementById('btn-preview-jump')?.addEventListener('click', () => {
    document.getElementById('preview-pane')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    schedulePreview();
  });

  document.getElementById('btn-save-template')?.addEventListener('click', async () => {
    const payload = collectPayload();
    if (!payload.name) {
      toast('Template name is required.', true);
      document.getElementById('tpl-name')?.focus();
      return;
    }
    try {
      const data = await api(urls.save, { method: 'POST', body: JSON.stringify(payload) });
      toast(data.message || 'Template saved.');
      window.location.href = window.location.pathname;
    } catch (err) {
      toast(err.message || 'Save failed.', true);
    }
  });

  document.getElementById('btn-download-sample')?.addEventListener('click', async () => {
    try { await downloadSamplePdf(); } catch (err) { toast(err.message || 'Download failed.', true); }
  });

  document.getElementById('templates-tbody')?.addEventListener('click', async (e) => {
    const viewBtn = e.target.closest('[data-view-template]');
    const editBtn = e.target.closest('[data-edit-template]');
    const dupBtn = e.target.closest('[data-duplicate-template]');
    const delBtn = e.target.closest('[data-delete-template]');
    if (viewBtn) {
      const tpl = findTemplate(viewBtn.getAttribute('data-view-template'));
      if (tpl) openViewDialog(tpl);
      return;
    }
    if (editBtn) {
      const tpl = findTemplate(editBtn.getAttribute('data-edit-template'));
      if (tpl) loadTemplateIntoForm(tpl);
      return;
    }
    if (dupBtn) {
      const id = dupBtn.getAttribute('data-duplicate-template');
      try {
        const data = await api(`${urls.duplicateBase}${id}/duplicate/`, { method: 'POST', body: '{}' });
        toast(data.message || 'Template duplicated.');
        window.location.reload();
      } catch (err) { toast(err.message || 'Duplicate failed.', true); }
      return;
    }
    if (delBtn) {
      const id = delBtn.getAttribute('data-delete-template');
      const name = delBtn.getAttribute('data-name') || 'this template';
      const assigned = Number(delBtn.getAttribute('data-assigned') || 0);
      let msg = `Delete "${name}"? This cannot be undone.`;
      if (assigned) msg += `\n\n${assigned} event(s) currently assigned will fall back to auto-matched templates.`;
      if (!window.confirm(msg)) return;
      try {
        const data = await api(`${urls.deleteBase}${id}/delete/`, { method: 'POST', body: '{}' });
        toast(data.message || 'Template deleted.');
        removeTemplateRows([data.deleted_id || id]);
        setTimeout(() => window.location.reload(), 600);
      } catch (err) { toast(err.message || 'Delete failed.', true); }
    }
  });

  document.getElementById('close-view')?.addEventListener('click', () => viewDialog?.close());
  document.querySelector('[data-close-view]')?.addEventListener('click', () => viewDialog?.close());
  document.getElementById('view-edit-btn')?.addEventListener('click', () => {
    const tpl = findTemplate(viewingId);
    viewDialog?.close();
    if (tpl) loadTemplateIntoForm(tpl);
  });

  renderMatchEventsTable();
})();
