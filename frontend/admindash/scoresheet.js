(function () {
  'use strict';

  var $ = function (sel, root) { return (root || document).querySelector(sel); };
  var $$ = function (sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); };

  var csrf = window.SS_CSRF || '';
  var urls = window.SS_URLS || {};
  var templates = [];
  try { templates = JSON.parse($('#ss-templates-data').textContent || '[]'); } catch (_) { templates = []; }

  var state = {
    templateId: null,
    orientation: 'portrait',
    zoom: 0.7,
    elements: [],
    selectedId: null,
    undo: [],
    redo: [],
    dragComp: null,
  };

  var DEFAULT_LAYOUT = [
    { id: 'logo', type: 'rect', x: 40, y: 36, w: 70, h: 70, props: { fill: '#ffffff', stroke: '#0b2c5c', strokeWidth: 2, radius: 35 } },
    { id: 'logo_text', type: 'text', x: 48, y: 58, w: 54, h: 30, props: { text: 'LOGO', fontSize: 11, fontWeight: 'bold', align: 'center', color: '#0b2c5c' } },
    { id: 'title', type: 'text', x: 130, y: 40, w: 420, h: 36, props: { text: 'UNIVERSITY OF EXCELLENCE\nINTRAMURALS 2025', fontSize: 16, fontWeight: 'bold', align: 'center', color: '#0b2c5c' } },
    { id: 'motto', type: 'text', x: 130, y: 82, w: 420, h: 20, props: { text: 'Unity. Sportsmanship. Excellence.', fontSize: 10, align: 'center', color: '#5a6f86' } },
    { id: 'meta_box', type: 'rect', x: 560, y: 36, w: 194, h: 78, props: { fill: '#f5f8fc', stroke: '#c5d3e4', strokeWidth: 1 } },
    { id: 'meta_text', type: 'text', x: 572, y: 46, w: 170, h: 60, props: { text: 'GAME NO. {{GameNumber}}\nDATE: {{Date}}\nVENUE: {{Venue}}', fontSize: 10, align: 'left', color: '#1d3554' } },
    { id: 'event_label', type: 'text', x: 40, y: 130, w: 714, h: 24, props: { text: 'EVENT: {{EventName}}', fontSize: 13, fontWeight: 'bold', align: 'left', color: '#0b2c5c' } },
    { id: 'team_a_box', type: 'rect', x: 40, y: 170, w: 300, h: 90, props: { fill: '#ffffff', stroke: '#0b2c5c', strokeWidth: 1.5 } },
    { id: 'team_a_label', type: 'text', x: 50, y: 182, w: 280, h: 66, props: { text: 'TEAM A\n{{TeamA}}', fontSize: 14, fontWeight: 'bold', align: 'center', color: '#0b2c5c' } },
    { id: 'vs', type: 'text', x: 360, y: 200, w: 74, h: 30, props: { text: 'VS', fontSize: 18, fontWeight: 'bold', align: 'center', color: '#155bd7' } },
    { id: 'team_b_box', type: 'rect', x: 454, y: 170, w: 300, h: 90, props: { fill: '#ffffff', stroke: '#0b2c5c', strokeWidth: 1.5 } },
    { id: 'team_b_label', type: 'text', x: 464, y: 182, w: 280, h: 66, props: { text: 'TEAM B\n{{TeamB}}', fontSize: 14, fontWeight: 'bold', align: 'center', color: '#0b2c5c' } },
    { id: 'score_a_box', type: 'rect', x: 40, y: 290, w: 220, h: 80, props: { fill: '#f8fbff', stroke: '#9fb4cc', strokeWidth: 1 } },
    { id: 'score_a', type: 'text', x: 50, y: 300, w: 200, h: 60, props: { text: 'SCORE\n{{ScoreA}}', fontSize: 14, fontWeight: 'bold', align: 'center', color: '#0b2c5c' } },
    { id: 'winner_box', type: 'rect', x: 280, y: 290, w: 234, h: 80, props: { fill: '#ffffff', stroke: '#155bd7', strokeWidth: 1.5 } },
    { id: 'winner', type: 'text', x: 290, y: 300, w: 214, h: 60, props: { text: 'WINNER\n{{Winner}}', fontSize: 13, fontWeight: 'bold', align: 'center', color: '#155bd7' } },
    { id: 'score_b_box', type: 'rect', x: 534, y: 290, w: 220, h: 80, props: { fill: '#f8fbff', stroke: '#9fb4cc', strokeWidth: 1 } },
    { id: 'score_b', type: 'text', x: 544, y: 300, w: 200, h: 60, props: { text: 'SCORE\n{{ScoreB}}', fontSize: 14, fontWeight: 'bold', align: 'center', color: '#0b2c5c' } },
    { id: 'remarks_title', type: 'text', x: 40, y: 400, w: 200, h: 22, props: { text: 'REMARKS', fontSize: 12, fontWeight: 'bold', color: '#0b2c5c' } },
    { id: 'remark_1', type: 'line', x: 40, y: 440, w: 714, h: 2, props: { stroke: '#9fb4cc', strokeWidth: 1 } },
    { id: 'remark_2', type: 'line', x: 40, y: 470, w: 714, h: 2, props: { stroke: '#9fb4cc', strokeWidth: 1 } },
    { id: 'remark_3', type: 'line', x: 40, y: 500, w: 714, h: 2, props: { stroke: '#9fb4cc', strokeWidth: 1 } },
    { id: 'sig_prep', type: 'signature', x: 40, y: 560, w: 220, h: 90, props: { label: 'PREPARED BY:\n(Scorer)' } },
    { id: 'sig_check', type: 'signature', x: 287, y: 560, w: 220, h: 90, props: { label: 'CHECKED BY:\n(Referee)' } },
    { id: 'sig_appr', type: 'signature', x: 534, y: 560, w: 220, h: 90, props: { label: 'APPROVED BY:\n(Event Coordinator)' } },
  ];

  function uid(prefix) {
    return (prefix || 'el') + Math.random().toString(36).slice(2, 9);
  }

  function clone(obj) {
    return JSON.parse(JSON.stringify(obj));
  }

  function pushHistory() {
    state.undo.push(clone(state.elements));
    if (state.undo.length > 40) state.undo.shift();
    state.redo = [];
  }

  function switchTab(name) {
    $$('.ss-tab').forEach(function (tab) {
      var on = tab.dataset.tab === name;
      tab.classList.toggle('is-active', on);
      tab.setAttribute('aria-selected', on ? 'true' : 'false');
    });
    $$('.ss-panel').forEach(function (panel) {
      panel.classList.toggle('is-active', panel.dataset.panel === name);
    });
    if (name === 'design') {
      renderCanvas();
      renderPreview();
    }
  }

  function canvasEl() { return $('#design-canvas'); }

  function selected() {
    return state.elements.find(function (el) { return el.id === state.selectedId; }) || null;
  }

  function applyZoom() {
    var canvas = canvasEl();
    canvas.style.transform = 'scale(' + state.zoom + ')';
    $('#zoom-label').textContent = Math.round(state.zoom * 100) + '%';
    var stage = $('#canvas-stage');
    var w = state.orientation === 'landscape' ? 1123 : 794;
    var h = state.orientation === 'landscape' ? 794 : 1123;
    canvas.style.width = w + 'px';
    canvas.style.height = h + 'px';
    canvas.classList.toggle('is-landscape', state.orientation === 'landscape');
    stage.style.minHeight = Math.max(320, h * state.zoom + 40) + 'px';
  }

  function elementDefaults(type) {
    var base = { id: uid(type), type: type, x: 80, y: 80, w: 180, h: 40, props: {} };
    if (type === 'text') {
      base.props = { text: 'Text label', fontSize: 14, fontWeight: 'normal', align: 'left', color: '#000000', fontFamily: 'Poppins' };
    } else if (type === 'line') {
      base.h = 2; base.w = 240; base.props = { stroke: '#9fb4cc', strokeWidth: 1 };
    } else if (type === 'rect') {
      base.w = 200; base.h = 80; base.props = { fill: '#ffffff', stroke: '#0b2c5c', strokeWidth: 1 };
    } else if (type === 'table') {
      base.w = 320; base.h = 140; base.props = { rows: 4, cols: 3, stroke: '#9fb4cc' };
    } else if (type === 'image') {
      base.w = 90; base.h = 90; base.props = { label: 'LOGO', fill: '#ffffff', stroke: '#0b2c5c' };
    } else if (type === 'input') {
      base.props = { text: '{{Field}}', fontSize: 12, align: 'left', color: '#1d3554', fill: '#ffffff', stroke: '#c5d3e4' };
    } else if (type === 'signature') {
      base.w = 200; base.h = 80; base.props = { label: 'Signature\n(Name / Role)' };
    }
    return base;
  }

  function styleFor(el) {
    var p = el.props || {};
    var style = {
      left: el.x + 'px',
      top: el.y + 'px',
      width: el.w + 'px',
      height: el.h + 'px',
    };
    if (el.type === 'text' || el.type === 'input') {
      style.fontSize = (p.fontSize || 12) + 'px';
      style.fontWeight = p.fontWeight || 'normal';
      style.fontStyle = p.italic ? 'italic' : 'normal';
      style.textDecoration = p.underline ? 'underline' : 'none';
      style.textAlign = p.align || 'left';
      style.color = p.color || '#000';
      style.fontFamily = p.fontFamily || 'Poppins, Arial, sans-serif';
      if (el.type === 'input') {
        style.border = '1px solid ' + (p.stroke || '#c5d3e4');
        style.background = p.fill || '#fff';
      }
    }
    if (el.type === 'rect' || el.type === 'image') {
      style.background = p.fill || '#fff';
      style.border = (p.strokeWidth || 1) + 'px solid ' + (p.stroke || '#0b2c5c');
      if (p.radius) style.borderRadius = p.radius + 'px';
    }
    if (el.type === 'line') {
      style.background = p.stroke || '#9fb4cc';
      style.height = Math.max(1, p.strokeWidth || 1) + 'px';
    }
    if (el.type === 'signature') {
      style.fontSize = '11px';
      style.fontWeight = '700';
      style.color = p.color || '#0b2c5c';
      style.textAlign = 'center';
    }
    return style;
  }

  function contentFor(el) {
    var p = el.props || {};
    if (el.type === 'text' || el.type === 'input') return p.text || '';
    if (el.type === 'signature') return p.label || 'Signature';
    if (el.type === 'image') return p.src ? '' : (p.label || 'IMAGE / LOGO');
    return '';
  }

  function fillElementNode(node, el) {
    node.innerHTML = '';
    var p = el.props || {};
    if (el.type === 'image' && p.src) {
      var img = document.createElement('img');
      img.src = p.src;
      img.alt = p.label || 'Logo';
      node.appendChild(img);
    } else {
      node.appendChild(document.createTextNode(contentFor(el)));
    }
  }

  function deleteSelected() {
    if (!state.selectedId) return;
    pushHistory();
    state.elements = state.elements.filter(function (el) { return el.id !== state.selectedId; });
    state.selectedId = null;
    syncPropsPanel();
    renderCanvas();
    renderPreview();
  }

  function renderCanvas() {
    var canvas = canvasEl();
    canvas.innerHTML = '';
    applyZoom();
    state.elements.forEach(function (el) {
      var node = document.createElement('div');
      node.className = 'ss-el ss-el-' + el.type + (el.id === state.selectedId ? ' is-selected' : '');
      node.dataset.id = el.id;
      var style = styleFor(el);
      Object.keys(style).forEach(function (key) { node.style[key] = style[key]; });
      fillElementNode(node, el);
      if (el.id === state.selectedId && el.type !== 'line') {
        var handle = document.createElement('span');
        handle.className = 'ss-handle';
        node.appendChild(handle);
        bindResize(handle, el);
      }
      bindMove(node, el);
      node.addEventListener('mousedown', function (e) {
        if (e.target.classList.contains('ss-handle')) return;
        state.selectedId = el.id;
        syncPropsPanel();
        renderCanvas();
        renderPreview();
      });
      canvas.appendChild(node);
    });
  }

  function bindMove(node, el) {
    node.addEventListener('mousedown', function (e) {
      if (e.target.classList.contains('ss-handle')) return;
      e.preventDefault();
      var startX = e.clientX;
      var startY = e.clientY;
      var origX = el.x;
      var origY = el.y;
      var moved = false;
      function onMove(ev) {
        var dx = (ev.clientX - startX) / state.zoom;
        var dy = (ev.clientY - startY) / state.zoom;
        if (!moved && (Math.abs(dx) > 1 || Math.abs(dy) > 1)) {
          pushHistory();
          moved = true;
        }
        el.x = Math.max(0, Math.round(origX + dx));
        el.y = Math.max(0, Math.round(origY + dy));
        node.style.left = el.x + 'px';
        node.style.top = el.y + 'px';
      }
      function onUp() {
        document.removeEventListener('mousemove', onMove);
        document.removeEventListener('mouseup', onUp);
        if (moved) renderPreview();
      }
      document.addEventListener('mousemove', onMove);
      document.addEventListener('mouseup', onUp);
    });
  }

  function bindResize(handle, el) {
    handle.addEventListener('mousedown', function (e) {
      e.preventDefault();
      e.stopPropagation();
      pushHistory();
      var startX = e.clientX;
      var startY = e.clientY;
      var origW = el.w;
      var origH = el.h;
      function onMove(ev) {
        el.w = Math.max(20, Math.round(origW + (ev.clientX - startX) / state.zoom));
        el.h = Math.max(el.type === 'line' ? 1 : 16, Math.round(origH + (ev.clientY - startY) / state.zoom));
        renderCanvas();
      }
      function onUp() {
        document.removeEventListener('mousemove', onMove);
        document.removeEventListener('mouseup', onUp);
        renderPreview();
      }
      document.addEventListener('mousemove', onMove);
      document.addEventListener('mouseup', onUp);
    });
  }

  function syncPropsPanel() {
    var el = selected();
    var deleteBtn = $('#btn-delete-selected');
    var imageControls = $('#prop-image-controls');
    var thumbWrap = $('#prop-image-thumb-wrap');
    var thumb = $('#prop-image-thumb');
    var clearBtn = $('#btn-clear-image');
    if (deleteBtn) deleteBtn.disabled = !el;
    if (!el) {
      if (imageControls) imageControls.classList.add('is-hidden');
      if (thumbWrap) thumbWrap.hidden = true;
      return;
    }
    var p = el.props || {};
    $('#prop-font').value = p.fontFamily || 'Poppins';
    $('#prop-size').value = String(p.fontSize || 14);
    $('#prop-bold').classList.toggle('is-active', p.fontWeight === 'bold');
    $('#prop-italic').classList.toggle('is-active', !!p.italic);
    $('#prop-underline').classList.toggle('is-active', !!p.underline);
    $$('.ss-style-row [data-align]').forEach(function (btn) {
      btn.classList.toggle('is-active', (p.align || 'left') === btn.dataset.align);
    });
    var color = p.color || '#000000';
    $('#prop-color').value = color;
    $('#prop-color-hex').value = color;
    $('#prop-text').value = p.text || p.label || '';
    if (imageControls) {
      imageControls.classList.toggle('is-hidden', el.type !== 'image');
    }
    if (el.type === 'image') {
      if (p.src) {
        thumb.src = p.src;
        thumbWrap.hidden = false;
        clearBtn.disabled = false;
      } else {
        thumb.removeAttribute('src');
        thumbWrap.hidden = true;
        clearBtn.disabled = true;
      }
    }
  }

  function updateSelectedFromProps() {
    var el = selected();
    if (!el) return;
    el.props = el.props || {};
    el.props.fontFamily = $('#prop-font').value;
    el.props.fontSize = Number($('#prop-size').value) || 14;
    el.props.fontWeight = $('#prop-bold').classList.contains('is-active') ? 'bold' : 'normal';
    el.props.italic = $('#prop-italic').classList.contains('is-active');
    el.props.underline = $('#prop-underline').classList.contains('is-active');
    var alignBtn = $('.ss-style-row [data-align].is-active');
    el.props.align = alignBtn ? alignBtn.dataset.align : 'left';
    el.props.color = $('#prop-color-hex').value || '#000000';
    var text = $('#prop-text').value;
    if (el.type === 'signature' || el.type === 'image') el.props.label = text;
    else el.props.text = text;
    renderCanvas();
    renderPreview();
  }

  function renderPreview() {
    var host = $('#live-preview');
    host.innerHTML = '';
    var page = document.createElement('div');
    page.className = 'ss-preview-page';
    var w = state.orientation === 'landscape' ? 1123 : 794;
    var h = state.orientation === 'landscape' ? 794 : 1123;
    page.style.width = w + 'px';
    page.style.height = h + 'px';
    var scale = Math.min(host.clientWidth / w, 220 / h) || 0.25;
    page.style.transform = 'scale(' + scale + ')';
    host.style.height = Math.max(180, h * scale) + 'px';
    state.elements.forEach(function (el) {
      var node = document.createElement('div');
      node.className = 'ss-el ss-el-' + el.type;
      var style = styleFor(el);
      Object.keys(style).forEach(function (key) { node.style[key] = style[key]; });
      fillElementNode(node, el);
      page.appendChild(node);
    });
    host.appendChild(page);
  }

  function loadTemplate(tpl) {
    state.templateId = tpl.id;
    state.orientation = tpl.orientation || 'portrait';
    state.elements = clone(tpl.layout && tpl.layout.length ? tpl.layout : DEFAULT_LAYOUT);
    state.selectedId = null;
    state.undo = [];
    state.redo = [];
    $('#tpl-name').value = tpl.name || '';
    $('#tpl-event-type').value = tpl.event_type || 'match';
    $('#tpl-category').value = tpl.category === '—' ? '' : (tpl.category || '');
    $$('.ss-orient button').forEach(function (btn) {
      btn.classList.toggle('is-active', btn.dataset.orient === state.orientation);
    });
    switchTab('design');
  }

  function newTemplate() {
    state.templateId = null;
    state.orientation = 'portrait';
    state.elements = clone(DEFAULT_LAYOUT);
    state.selectedId = null;
    state.undo = [];
    state.redo = [];
    $('#tpl-name').value = 'New Scoresheet Template';
    $('#tpl-event-type').value = 'match';
    $('#tpl-category').value = '';
    $$('.ss-orient button').forEach(function (btn) {
      btn.classList.toggle('is-active', btn.dataset.orient === 'portrait');
    });
    switchTab('design');
  }

  function buildExportPayload() {
    return {
      name: ($('#tpl-name').value || 'Imported Scoresheet').trim(),
      event_type: $('#tpl-event-type').value || 'match',
      category: ($('#tpl-category').value || '').trim(),
      paper_size: $('#tpl-paper').value || 'a4',
      orientation: state.orientation || 'portrait',
      layout: state.elements || [],
      version: 1,
    };
  }

  function applyImportedTemplate(data) {
    if (!data || typeof data !== 'object') {
      throw new Error('Invalid template file.');
    }
    var layout = data.layout;
    if (!Array.isArray(layout) || !layout.length) {
      throw new Error('Template file must include a non-empty layout array.');
    }
    state.templateId = null;
    state.orientation = data.orientation === 'landscape' ? 'landscape' : 'portrait';
    state.elements = clone(layout);
    state.selectedId = null;
    state.undo = [];
    state.redo = [];
    $('#tpl-name').value = (data.name || 'Imported Scoresheet').toString().slice(0, 200);
    $('#tpl-event-type').value = data.event_type === 'criteria' ? 'criteria' : 'match';
    $('#tpl-category').value = (data.category || '').toString().slice(0, 100);
    if (data.paper_size) $('#tpl-paper').value = data.paper_size;
    $$('.ss-orient button').forEach(function (btn) {
      btn.classList.toggle('is-active', btn.dataset.orient === state.orientation);
    });
    switchTab('design');
    syncPropsPanel();
    renderCanvas();
    renderPreview();
  }

  function exportTemplateFile() {
    var payload = buildExportPayload();
    if (!payload.layout.length) {
      alert('Add at least one component before exporting.');
      return;
    }
    var blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
    var url = URL.createObjectURL(blob);
    var a = document.createElement('a');
    var safeName = (payload.name || 'scoresheet-template').replace(/[^\w\-]+/g, '_').slice(0, 60);
    a.href = url;
    a.download = safeName + '.json';
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  }

  function importTemplateFile(file) {
    if (!file) return;
    var reader = new FileReader();
    reader.onload = function () {
      try {
        var data = JSON.parse(String(reader.result || '{}'));
        applyImportedTemplate(data);
        alert('Template imported. Review the design, then Save Template to keep it.');
      } catch (err) {
        alert(err.message || 'Could not import template JSON.');
      }
    };
    reader.onerror = function () {
      alert('Failed to read the template file.');
    };
    reader.readAsText(file);
  }

  function getCookie(name) {
    var match = document.cookie.match(new RegExp('(^| )' + name + '=([^;]+)'));
    return match ? decodeURIComponent(match[2]) : '';
  }

  async function apiPost(url, body) {
    var token = csrf || getCookie('csrftoken');
    var res = await fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': token,
      },
      body: JSON.stringify(body),
      credentials: 'same-origin',
    });
    var data = await res.json().catch(function () { return {}; });
    if (!res.ok || data.success === false) {
      throw new Error(data.message || 'Request failed.');
    }
    return data;
  }

  async function saveTemplate() {
    var name = $('#tpl-name').value.trim();
    if (!name) {
      alert('Template name is required.');
      return;
    }
    try {
      var data = await apiPost(urls.save, {
        id: state.templateId,
        name: name,
        event_type: $('#tpl-event-type').value,
        category: $('#tpl-category').value.trim(),
        paper_size: $('#tpl-paper').value,
        orientation: state.orientation,
        layout: state.elements,
        status: 'active',
      });
      state.templateId = data.template.id;
      var idx = templates.findIndex(function (t) { return t.id === data.template.id; });
      if (idx >= 0) templates[idx] = data.template;
      else templates.unshift(data.template);
      refreshTemplatesTable();
      alert(data.message || 'Template saved.');
    } catch (err) {
      alert(err.message || 'Save failed.');
    }
  }

  function refreshTemplatesTable() {
    var tbody = $('#templates-table tbody');
    if (!templates.length) {
      tbody.innerHTML = '<tr><td colspan="6" class="ss-empty">No templates yet.</td></tr>';
      return;
    }
    tbody.innerHTML = templates.map(function (t) {
      return '<tr data-id="' + t.id + '">' +
        '<td><strong>' + escapeHtml(t.name) + '</strong></td>' +
        '<td>' + escapeHtml(t.event_type_label) + '</td>' +
        '<td>' + escapeHtml(t.category) + '</td>' +
        '<td>' + escapeHtml(t.updated_at) + '</td>' +
        '<td><span class="ss-badge ss-badge--' + escapeHtml(t.status) + '">' + escapeHtml(t.status_label) + '</span></td>' +
        '<td><div class="ss-row-actions">' +
        '<button type="button" class="ss-icon-btn" data-edit-template="' + t.id + '" title="Edit"><svg viewBox="0 0 24 24"><path d="M3 17.25V21h3.75L17.81 9.94l-3.75-3.75L3 17.25ZM20.71 7.04a1 1 0 0 0 0-1.41l-2.34-2.34a1 1 0 0 0-1.41 0l-1.83 1.83 3.75 3.75 1.83-1.83Z"/></svg></button>' +
        '<button type="button" class="ss-icon-btn danger" data-delete-template="' + t.id + '" data-name="' + escapeAttr(t.name) + '" title="Delete"><svg viewBox="0 0 24 24"><path d="M6 7h12v2H6V7Zm2 3h8l-1 11H9L8 10Zm3-6h2l1 2H10l1-2Z"/></svg></button>' +
        '</div></td></tr>';
    }).join('');
    bindTableActions();
    $('#templates-count').textContent = 'Showing 1 to ' + templates.length + ' of ' + templates.length + ' templates';
  }

  function escapeHtml(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  function escapeAttr(value) {
    return escapeHtml(value).replace(/"/g, '&quot;');
  }

  function bindTableActions() {
    $$('[data-edit-template]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var tpl = templates.find(function (t) { return String(t.id) === String(btn.dataset.editTemplate); });
        if (tpl) loadTemplate(tpl);
      });
    });
    $$('[data-delete-template]').forEach(function (btn) {
      btn.addEventListener('click', async function () {
        if (!confirm('Delete template "' + (btn.dataset.name || '') + '"?')) return;
        try {
          await apiPost(urls.deleteBase + btn.dataset.deleteTemplate + '/delete/', {});
          templates = templates.filter(function (t) { return String(t.id) !== String(btn.dataset.deleteTemplate); });
          refreshTemplatesTable();
        } catch (err) {
          alert(err.message || 'Delete failed.');
        }
      });
    });
  }

  // Tabs
  $$('.ss-tab').forEach(function (tab) {
    tab.addEventListener('click', function () { switchTab(tab.dataset.tab); });
  });
  $$('[data-tab-jump]').forEach(function (btn) {
    btn.addEventListener('click', function () { switchTab(btn.dataset.tabJump); });
  });
  $('#btn-create').addEventListener('click', newTemplate);
  $('#btn-import-template').addEventListener('click', function () {
    $('#import-template-file').click();
  });
  $('#import-template-file').addEventListener('change', function () {
    var file = this.files && this.files[0];
    this.value = '';
    importTemplateFile(file);
  });
  $('#btn-export-template').addEventListener('click', exportTemplateFile);

  // Palette drag / click add
  $$('.ss-palette [data-component]').forEach(function (btn) {
    btn.addEventListener('dragstart', function (e) {
      state.dragComp = btn.dataset.component;
      e.dataTransfer.setData('text/plain', btn.dataset.component);
    });
    btn.addEventListener('click', function () {
      pushHistory();
      var el = elementDefaults(btn.dataset.component);
      state.elements.push(el);
      state.selectedId = el.id;
      renderCanvas();
      syncPropsPanel();
      renderPreview();
    });
  });
  canvasEl().addEventListener('dragover', function (e) { e.preventDefault(); });
  canvasEl().addEventListener('drop', function (e) {
    e.preventDefault();
    var type = e.dataTransfer.getData('text/plain') || state.dragComp;
    if (!type) return;
    pushHistory();
    var rect = canvasEl().getBoundingClientRect();
    var el = elementDefaults(type);
    el.x = Math.max(0, Math.round((e.clientX - rect.left) / state.zoom - el.w / 2));
    el.y = Math.max(0, Math.round((e.clientY - rect.top) / state.zoom - el.h / 2));
    state.elements.push(el);
    state.selectedId = el.id;
    renderCanvas();
    syncPropsPanel();
    renderPreview();
  });

  // Orientation
  $$('.ss-orient button').forEach(function (btn) {
    btn.addEventListener('click', function () {
      state.orientation = btn.dataset.orient;
      $$('.ss-orient button').forEach(function (b) {
        b.classList.toggle('is-active', b === btn);
      });
      applyZoom();
      renderPreview();
    });
  });

  // Toolbar
  $('#btn-undo').addEventListener('click', function () {
    if (!state.undo.length) return;
    state.redo.push(clone(state.elements));
    state.elements = state.undo.pop();
    state.selectedId = null;
    renderCanvas();
    renderPreview();
  });
  $('#btn-redo').addEventListener('click', function () {
    if (!state.redo.length) return;
    state.undo.push(clone(state.elements));
    state.elements = state.redo.pop();
    state.selectedId = null;
    renderCanvas();
    renderPreview();
  });
  $('#btn-delete-el').addEventListener('click', deleteSelected);
  $('#btn-delete-selected').addEventListener('click', deleteSelected);
  $('#btn-import-image').addEventListener('click', function () {
    if (!selected() || selected().type !== 'image') {
      alert('Select an Image / Logo component first.');
      return;
    }
    $('#prop-image-file').click();
  });
  $('#prop-image-file').addEventListener('change', function () {
    var el = selected();
    var file = this.files && this.files[0];
    this.value = '';
    if (!el || el.type !== 'image' || !file) return;
    if (file.size > 2 * 1024 * 1024) {
      alert('Please choose an image under 2 MB.');
      return;
    }
    var reader = new FileReader();
    reader.onload = function () {
      pushHistory();
      el.props = el.props || {};
      el.props.src = String(reader.result || '');
      if (!el.props.label) el.props.label = 'Logo';
      syncPropsPanel();
      renderCanvas();
      renderPreview();
    };
    reader.readAsDataURL(file);
  });
  $('#btn-clear-image').addEventListener('click', function () {
    var el = selected();
    if (!el || el.type !== 'image' || !el.props || !el.props.src) return;
    pushHistory();
    delete el.props.src;
    syncPropsPanel();
    renderCanvas();
    renderPreview();
  });
  $('#btn-zoom-in').addEventListener('click', function () {
    state.zoom = Math.min(1.4, Math.round((state.zoom + 0.1) * 10) / 10);
    applyZoom();
  });
  $('#btn-zoom-out').addEventListener('click', function () {
    state.zoom = Math.max(0.4, Math.round((state.zoom - 0.1) * 10) / 10);
    applyZoom();
  });

  // Properties
  ['change', 'input'].forEach(function (evt) {
    $('#prop-font').addEventListener(evt, updateSelectedFromProps);
    $('#prop-size').addEventListener(evt, updateSelectedFromProps);
    $('#prop-text').addEventListener(evt, updateSelectedFromProps);
    $('#prop-color').addEventListener(evt, function () {
      $('#prop-color-hex').value = $('#prop-color').value;
      updateSelectedFromProps();
    });
    $('#prop-color-hex').addEventListener(evt, function () {
      if (/^#[0-9a-fA-F]{6}$/.test($('#prop-color-hex').value)) {
        $('#prop-color').value = $('#prop-color-hex').value;
        updateSelectedFromProps();
      }
    });
  });
  $('#prop-bold').addEventListener('click', function () {
    this.classList.toggle('is-active');
    updateSelectedFromProps();
  });
  $('#prop-italic').addEventListener('click', function () {
    this.classList.toggle('is-active');
    updateSelectedFromProps();
  });
  $('#prop-underline').addEventListener('click', function () {
    this.classList.toggle('is-active');
    updateSelectedFromProps();
  });
  $$('.ss-style-row [data-align]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      $$('.ss-style-row [data-align]').forEach(function (b) { b.classList.remove('is-active'); });
      btn.classList.add('is-active');
      updateSelectedFromProps();
    });
  });

  $('#btn-save-template').addEventListener('click', saveTemplate);

  $('#btn-preview-full').addEventListener('click', function () {
    var body = $('#preview-dialog-body');
    body.innerHTML = '';
    var page = document.createElement('div');
    page.className = 'ss-canvas';
    if (state.orientation === 'landscape') page.classList.add('is-landscape');
    page.style.transform = 'none';
    page.style.margin = '0 auto';
    state.elements.forEach(function (el) {
      var node = document.createElement('div');
      node.className = 'ss-el ss-el-' + el.type;
      var style = styleFor(el);
      Object.keys(style).forEach(function (key) { node.style[key] = style[key]; });
      fillElementNode(node, el);
      page.appendChild(node);
    });
    body.appendChild(page);
    $('#preview-dialog').showModal();
  });
  $('#close-preview').addEventListener('click', function () { $('#preview-dialog').close(); });

  $('#btn-generate-pdf').addEventListener('click', function () {
    $('#generate-dialog').showModal();
  });
  $$('[data-close-generate]').forEach(function (btn) {
    btn.addEventListener('click', function () { $('#generate-dialog').close(); });
  });
  $('#generate-form').addEventListener('submit', async function (e) {
    e.preventDefault();
    var form = e.target;
    var payload = {};
    ['GameNumber', 'Date', 'Venue', 'EventName', 'TeamA', 'TeamB', 'ScoreA', 'ScoreB', 'Winner'].forEach(function (key) {
      payload[key] = form[key].value;
    });
    try {
      var data = await apiPost(urls.generate, {
        template_id: state.templateId,
        layout: state.elements,
        orientation: state.orientation,
        event_label: form.event_label.value,
        item_label: form.item_label.value,
        event_id: form.event_id.value || null,
        payload: payload,
      });
      $('#generate-dialog').close();
      if (data.download_url) window.location.href = data.download_url;
      else alert(data.message || 'Generated.');
      setTimeout(function () { window.location.reload(); }, 400);
    } catch (err) {
      alert(err.message || 'Generate failed.');
    }
  });

  // Init
  bindTableActions();
  state.elements = clone(DEFAULT_LAYOUT);
  applyZoom();
  renderCanvas();
  renderPreview();
  syncPropsPanel();
  window.addEventListener('resize', function () {
    if ($('.ss-panel[data-panel="design"]').classList.contains('is-active')) renderPreview();
  });
})();
