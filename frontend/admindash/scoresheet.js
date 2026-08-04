(() => {
  const csrf = window.SS_CSRF || '';
  const urls = window.SS_URLS || {};
  const SAMPLE = {
    GameNumber: '12', Round: 'Semifinal', Date: 'May 20, 2025', Time: '2:00 PM',
    Venue: 'Main Gym', PlayingArea: 'Court 1', EventName: 'Basketball Men',
    Classification: 'Intramurals', Division: 'Men', TournamentFormat: 'Single Elimination',
    TeamA: 'Engineering Tigers', TeamB: 'Science Hawks', StageName: 'Preliminary Round',
    Contestant: 'Alex Rivera', ContestantNumber: '07', Department: 'College of Engineering',
    CriteriaWeight: '100%', MaximumScore: '100', FacultyInCharge: 'Prof. Santos',
  };

  let templates = [];
  let fieldDefs = { match: [], criteria: [] };
  let defaultOrder = { match: [], criteria: [] };
  try { templates = JSON.parse(document.getElementById('ss-templates-data')?.textContent || '[]'); } catch (_) {}
  try { fieldDefs = JSON.parse(document.getElementById('ss-field-defs')?.textContent || '{}'); } catch (_) {}
  try { defaultOrder = JSON.parse(document.getElementById('ss-default-order')?.textContent || '{}'); } catch (_) {}

  const listPanel = document.querySelector('[data-panel="list"]');
  const wizardPanel = document.querySelector('[data-panel="wizard"]');
  const fieldList = document.getElementById('field-list');
  const viewDialog = document.getElementById('view-dialog');
  let step = 1;
  let viewingId = null;
  let currentOrder = [];
  let currentFields = {};
  let previewTimer = null;
  let dragKey = null;

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
      ...options,
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': csrf,
        ...(options.headers || {}),
      },
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || data.success === false) throw new Error(data.message || `Request failed (${res.status})`);
    return data;
  }

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

  function escapeHtml(s) {
    return String(s ?? '')
      .replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }
  function escapeAttr(s) { return escapeHtml(s).replace(/'/g, '&#39;'); }

  function findTemplate(id) {
    return templates.find((t) => String(t.id) === String(id));
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
    document.getElementById('wizard-title').textContent = title || 'Create Template';
    goStep(1);
  }

  function goStep(n) {
    step = n;
    document.querySelectorAll('.ss-step').forEach((el) => {
      el.hidden = Number(el.dataset.step) !== n;
    });
    document.querySelectorAll('[data-step-indicator]').forEach((el) => {
      const s = Number(el.dataset.stepIndicator);
      el.classList.toggle('is-active', s === n);
      el.classList.toggle('is-done', s < n);
    });
    if (n >= 2) schedulePreview();
  }

  function readOrderFromDom() {
    return [...fieldList.querySelectorAll('.ss-field-row')].map((row) => row.dataset.key);
  }

  function readFieldsFromDom() {
    const out = {};
    fieldList.querySelectorAll('.ss-field-row').forEach((row) => {
      out[row.dataset.key] = row.querySelector('input[type="checkbox"]').checked;
    });
    return out;
  }

  function renderFieldList() {
    const type = eventType();
    const defs = defsFor(type);
    const labelMap = Object.fromEntries(defs.map((d) => [d.key, d.label]));
    const order = currentOrder.length ? currentOrder : (defaultOrder[type] || defs.map((d) => d.key));
    const fields = { ...defaultFields(type), ...currentFields };
    fieldList.innerHTML = '';
    order.forEach((key) => {
      if (!labelMap[key]) return;
      const on = !!fields[key];
      const li = document.createElement('li');
      li.className = `ss-field-row${on ? ' is-on' : ''}`;
      li.dataset.key = key;
      li.draggable = true;
      li.innerHTML = `
        <span class="ss-drag" title="Drag to reorder" aria-hidden="true">⠿</span>
        <label class="ss-check-inline">
          <input type="checkbox" ${on ? 'checked' : ''}>
          <span>${escapeHtml(labelMap[key])}</span>
        </label>
        <span class="ss-reorder">
          <button type="button" class="ss-icon-move" data-move="-1" title="Move up">↑</button>
          <button type="button" class="ss-icon-move" data-move="1" title="Move down">↓</button>
        </span>`;
      li.querySelector('input').addEventListener('change', (e) => {
        li.classList.toggle('is-on', e.target.checked);
        currentFields = readFieldsFromDom();
        schedulePreview();
      });
      li.querySelectorAll('[data-move]').forEach((btn) => {
        btn.addEventListener('click', () => moveRow(key, Number(btn.dataset.move)));
      });
      li.addEventListener('dragstart', () => { dragKey = key; li.classList.add('is-dragging'); });
      li.addEventListener('dragend', () => { dragKey = null; li.classList.remove('is-dragging'); syncOrder(); });
      li.addEventListener('dragover', (e) => { e.preventDefault(); li.classList.add('is-drop'); });
      li.addEventListener('dragleave', () => li.classList.remove('is-drop'));
      li.addEventListener('drop', (e) => {
        e.preventDefault();
        li.classList.remove('is-drop');
        if (!dragKey || dragKey === key) return;
        const orderNow = readOrderFromDom();
        const from = orderNow.indexOf(dragKey);
        const to = orderNow.indexOf(key);
        if (from < 0 || to < 0) return;
        orderNow.splice(from, 1);
        orderNow.splice(to, 0, dragKey);
        currentOrder = orderNow;
        currentFields = readFieldsFromDom();
        renderFieldList();
        schedulePreview();
      });
      fieldList.appendChild(li);
    });
    currentOrder = readOrderFromDom();
    currentFields = readFieldsFromDom();
  }

  function moveRow(key, delta) {
    const orderNow = readOrderFromDom();
    const idx = orderNow.indexOf(key);
    const next = idx + delta;
    if (idx < 0 || next < 0 || next >= orderNow.length) return;
    [orderNow[idx], orderNow[next]] = [orderNow[next], orderNow[idx]];
    currentOrder = orderNow;
    currentFields = readFieldsFromDom();
    renderFieldList();
    schedulePreview();
  }

  function syncOrder() {
    currentOrder = readOrderFromDom();
    currentFields = readFieldsFromDom();
    schedulePreview();
  }

  function collectPayload() {
    if (step >= 2) {
      currentOrder = readOrderFromDom();
      currentFields = readFieldsFromDom();
    }
    return {
      id: document.getElementById('tpl-id').value || null,
      name: document.getElementById('tpl-name').value.trim(),
      event_type: eventType(),
      category: document.getElementById('tpl-category').value.trim(),
      description: document.getElementById('tpl-description').value.trim(),
      paper_size: document.getElementById('tpl-paper').value,
      orientation: document.getElementById('tpl-orientation').value,
      status: document.getElementById('tpl-status').value,
      fields: currentFields,
      order: currentOrder,
    };
  }

  function substitute(text, payload) {
    return String(text || '').replace(/\{\{\s*([A-Za-z0-9_]+)\s*\}\}/g, (_, key) => {
      const v = payload[key];
      return v == null || v === '' ? `{{${key}}}` : String(v);
    });
  }

  function paintPreview(container, elements, orientation) {
    if (!container) return;
    container.innerHTML = '';
    container.classList.toggle('is-landscape', orientation === 'landscape');
    (elements || []).forEach((el) => {
      const node = document.createElement('div');
      node.className = 'ss-pv-el';
      node.style.left = `${el.x || 0}px`;
      node.style.top = `${el.y || 0}px`;
      node.style.width = `${el.w || 100}px`;
      node.style.height = `${el.h || 24}px`;
      const props = el.props || {};
      const t = (el.type || 'text').toLowerCase();
      if (t === 'rect' || t === 'rectangle') {
        node.classList.add('ss-pv-rect');
        node.style.background = props.fill || '#fff';
        node.style.borderColor = props.stroke || '#0b2c5c';
        node.style.borderWidth = `${props.strokeWidth || 1}px`;
      } else if (t === 'line') {
        node.classList.add('ss-pv-line');
        node.style.borderTopColor = props.stroke || '#9fb4cc';
        node.style.borderTopWidth = `${props.strokeWidth || 1}px`;
      } else if (t === 'table') {
        const rows = Number(props.rows || 4);
        const cols = Number(props.cols || 3);
        node.classList.add('ss-pv-table');
        node.style.gridTemplateColumns = `repeat(${cols}, 1fr)`;
        node.style.gridTemplateRows = `repeat(${rows}, 1fr)`;
        for (let i = 0; i < rows * cols; i += 1) node.appendChild(document.createElement('span'));
      } else if (t === 'signature') {
        node.classList.add('ss-pv-sig');
        node.textContent = substitute(props.label || 'Signature', SAMPLE);
      } else {
        node.classList.add('ss-pv-text');
        node.dataset.align = props.align || 'left';
        node.style.color = props.color || '#0b2c5c';
        node.style.fontSize = `${props.fontSize || 12}px`;
        node.style.fontWeight = props.fontWeight === 'bold' ? '700' : '500';
        node.style.alignItems = 'center';
        node.style.padding = '2px 4px';
        node.textContent = substitute(props.text || '', SAMPLE);
      }
      container.appendChild(node);
    });
  }

  function scalePreview(container) {
    const frame = container?.closest('.ss-preview-frame');
    if (!frame || !container) return;
    const avail = frame.clientWidth - 32;
    const pageW = container.classList.contains('is-landscape') ? 1123 : 794;
    const scale = Math.min(1, avail / pageW);
    container.style.transform = `scale(${scale})`;
    container.style.marginBottom = `${container.offsetHeight * (scale - 1)}px`;
  }

  async function fetchPreviewElements() {
    const payload = collectPayload();
    const data = await api(urls.preview, {
      method: 'POST',
      body: JSON.stringify({
        event_type: payload.event_type,
        orientation: payload.orientation,
        fields: payload.fields,
        order: payload.order,
      }),
    });
    return data.elements || [];
  }

  function schedulePreview() {
    clearTimeout(previewTimer);
    previewTimer = setTimeout(async () => {
      try {
        const elements = await fetchPreviewElements();
        const orientation = document.getElementById('tpl-orientation')?.value || 'portrait';
        const targets = [
          document.getElementById('live-preview-step2'),
          document.getElementById('live-preview-step3'),
        ];
        targets.forEach((el) => {
          if (!el || el.closest('.ss-step')?.hidden) return;
          paintPreview(el, elements, orientation);
          scalePreview(el);
        });
      } catch (err) {
        /* preview is best-effort while editing */
      }
    }, 220);
  }

  function loadTemplateIntoForm(tpl) {
    document.getElementById('tpl-id').value = tpl?.id || '';
    document.getElementById('tpl-name').value = tpl?.name || '';
    document.getElementById('tpl-category').value = (tpl?.category === '—' ? '' : (tpl?.category || ''));
    document.getElementById('tpl-description').value = tpl?.description || '';
    document.getElementById('tpl-paper').value = tpl?.paper_size || 'a4';
    document.getElementById('tpl-orientation').value = tpl?.orientation || 'portrait';
    document.getElementById('tpl-status').value = tpl?.status || 'active';
    const type = tpl?.event_type || 'match';
    document.querySelectorAll('input[name="event_type"]').forEach((r) => { r.checked = r.value === type; });
    document.getElementById('builder-title').textContent =
      type === 'criteria' ? 'Criteria-Based Template' : 'Match-Based Template';
    currentFields = { ...defaultFields(type), ...(tpl?.fields || {}) };
    currentOrder = (tpl?.order && tpl.order.length) ? tpl.order : (defaultOrder[type] || []);
    renderFieldList();
    showWizard(tpl?.id ? 'Edit Template' : 'Create Template');
  }

  function openViewDialog(tpl) {
    viewingId = tpl.id;
    document.getElementById('view-dialog-title').textContent = tpl.name;
    document.getElementById('view-meta').innerHTML = `
      <dt>Event Type</dt><dd>${escapeHtml(tpl.event_type_label)}</dd>
      <dt>Category</dt><dd>${escapeHtml(tpl.category || '—')}</dd>
      <dt>Description</dt><dd>${escapeHtml(tpl.description || '—')}</dd>
      <dt>Paper</dt><dd>${escapeHtml((tpl.paper_size || 'a4').toUpperCase())} · ${escapeHtml(tpl.orientation || 'portrait')}</dd>
      <dt>Status</dt><dd>${escapeHtml(tpl.status_label || tpl.status)}</dd>
      <dt>Last Updated</dt><dd>${escapeHtml(tpl.updated_at || '—')}</dd>`;
    paintPreview(document.getElementById('view-preview'), tpl.elements || [], tpl.orientation || 'portrait');
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

  async function printSample() {
    try {
      const elements = await fetchPreviewElements();
      const orientation = document.getElementById('tpl-orientation')?.value || 'portrait';
      const win = window.open('', '_blank');
      if (!win) { toast('Allow pop-ups to print the sample.', true); return; }
      const w = orientation === 'landscape' ? 1123 : 794;
      const h = orientation === 'landscape' ? 794 : 1123;
      win.document.write(`<!DOCTYPE html><html><head><title>Sample Scoresheet</title>
        <style>body{margin:0;font-family:Arial,Helvetica,sans-serif;color:#0b2c5c}
        .page{position:relative;width:${w}px;min-height:${h}px;margin:0 auto}
        .el{position:absolute;box-sizing:border-box;overflow:hidden;white-space:pre-wrap;line-height:1.25}
        @media print{body{margin:0}}</style></head><body><div class="page" id="p"></div></body></html>`);
      win.document.close();
      const page = win.document.getElementById('p');
      elements.forEach((el) => {
        const node = win.document.createElement('div');
        node.className = 'el';
        node.style.left = `${el.x || 0}px`;
        node.style.top = `${el.y || 0}px`;
        node.style.width = `${el.w || 100}px`;
        node.style.height = `${el.h || 24}px`;
        const props = el.props || {};
        const t = (el.type || 'text').toLowerCase();
        if (t === 'rect' || t === 'rectangle') {
          node.style.background = props.fill || '#fff';
          node.style.border = `${props.strokeWidth || 1}px solid ${props.stroke || '#0b2c5c'}`;
        } else if (t === 'line') {
          node.style.borderTop = `${props.strokeWidth || 1}px solid ${props.stroke || '#9fb4cc'}`;
          node.style.height = '0';
        } else if (t === 'table') {
          const rows = Number(props.rows || 4);
          const cols = Number(props.cols || 3);
          node.style.display = 'grid';
          node.style.gridTemplateColumns = `repeat(${cols},1fr)`;
          node.style.gridTemplateRows = `repeat(${rows},1fr)`;
          node.style.border = '1px solid #9fb4cc';
          for (let i = 0; i < rows * cols; i += 1) {
            const cell = win.document.createElement('span');
            cell.style.border = '1px solid #d5e0ec';
            node.appendChild(cell);
          }
        } else if (t === 'signature') {
          node.style.border = '1px dashed #9fb4cc';
          node.style.display = 'flex';
          node.style.alignItems = 'flex-end';
          node.style.justifyContent = 'center';
          node.style.padding = '8px';
          node.style.textAlign = 'center';
          node.style.fontSize = '11px';
          node.style.fontWeight = '700';
          node.textContent = substitute(props.label || 'Signature', SAMPLE);
        } else {
          node.style.color = props.color || '#0b2c5c';
          node.style.fontSize = `${props.fontSize || 12}px`;
          node.style.fontWeight = props.fontWeight === 'bold' ? '700' : '500';
          node.style.textAlign = props.align || 'left';
          node.style.padding = '2px 4px';
          node.textContent = substitute(props.text || '', SAMPLE);
        }
        page.appendChild(node);
      });
      setTimeout(() => { win.focus(); win.print(); }, 250);
    } catch (err) {
      toast(err.message || 'Print failed.', true);
    }
  }

  // Events
  document.getElementById('btn-create-template')?.addEventListener('click', () => {
    loadTemplateIntoForm({
      event_type: 'match', paper_size: 'a4', orientation: 'portrait', status: 'active',
      fields: defaultFields('match'), order: defaultOrder.match || [],
    });
  });

  document.getElementById('btn-cancel-wizard')?.addEventListener('click', showList);
  document.querySelectorAll('[data-cancel-step]').forEach((btn) => btn.addEventListener('click', showList));

  document.getElementById('btn-step1-next')?.addEventListener('click', () => {
    const name = document.getElementById('tpl-name').value.trim();
    if (!name) {
      toast('Template name is required.', true);
      document.getElementById('tpl-name')?.focus();
      return;
    }
    const type = eventType();
    document.getElementById('builder-title').textContent =
      type === 'criteria' ? 'Criteria-Based Template' : 'Match-Based Template';
    if (!currentOrder.length) currentOrder = defaultOrder[type] || [];
    if (!Object.keys(currentFields).length) currentFields = defaultFields(type);
    renderFieldList();
    goStep(2);
  });

  document.getElementById('btn-step2-back')?.addEventListener('click', () => goStep(1));
  document.getElementById('btn-step2-next')?.addEventListener('click', () => {
    syncOrder();
    goStep(3);
  });
  document.getElementById('btn-step3-back')?.addEventListener('click', () => goStep(2));

  document.querySelectorAll('input[name="event_type"]').forEach((r) => {
    r.addEventListener('change', () => {
      const type = eventType();
      currentFields = defaultFields(type);
      currentOrder = defaultOrder[type] || [];
      document.getElementById('builder-title').textContent =
        type === 'criteria' ? 'Criteria-Based Template' : 'Match-Based Template';
      if (step >= 2) renderFieldList();
      schedulePreview();
    });
  });

  document.getElementById('tpl-orientation')?.addEventListener('change', schedulePreview);
  document.getElementById('btn-select-all-fields')?.addEventListener('click', () => {
    currentFields = defaultFields(eventType());
    renderFieldList();
    schedulePreview();
  });
  document.getElementById('btn-clear-fields')?.addEventListener('click', () => {
    const cleared = defaultFields(eventType());
    Object.keys(cleared).forEach((k) => { cleared[k] = false; });
    currentFields = cleared;
    renderFieldList();
    schedulePreview();
  });

  document.getElementById('btn-save-template')?.addEventListener('click', async () => {
    const payload = collectPayload();
    if (!payload.name) { toast('Template name is required.', true); return; }
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
  document.getElementById('btn-print-sample')?.addEventListener('click', printSample);

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
        window.location.reload();
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

  window.addEventListener('resize', () => {
    document.querySelectorAll('.ss-preview-page').forEach(scalePreview);
  });
})();
