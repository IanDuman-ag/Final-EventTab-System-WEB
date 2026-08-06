(function () {
  'use strict';

  var $ = function (sel, root) { return (root || document).querySelector(sel); };
  var $$ = function (sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); };

  var form = $('#wizard-form');
  var wizard = $('#event-wizard');
  var step = 1;
  var criteria = [];
  var deductions = [];
  var points = [];
  var stages = [];
  var events = [];

  try {
    events = JSON.parse($('#criteria-events-data').textContent || '[]');
  } catch (_) {
    events = [];
  }

  var STAGE_NAME_HINTS = [
    'Production Number',
    'Talent Competition',
    'Swimwear Competition',
    'Evening Gown',
    'Speech Round',
    'Question and Answer',
    'Final Interview',
  ];

  var QUALIFICATION_METHODS = [
    { value: 'top_ranking', label: 'Top Ranking' },
    { value: 'minimum_score', label: 'Minimum Score' },
    { value: 'manual_selection', label: 'Manual Selection by Faculty' },
  ];

  var TIE_METHODS = [
    { method: 'highest_selected_criterion', label: 'Highest Score in Priority Criterion' },
    { method: 'highest_chief_judge', label: 'Highest Chief Judge Score' },
    { method: 'lowest_deduction', label: 'Lowest Deduction' },
    { method: 'manual_decision', label: 'Manual Faculty Decision' },
  ];

  function showError(msg) {
    var el = $('#wizard-error');
    if (el) el.textContent = msg || '';
  }

  function setStep(next) {
    step = next;
    $$('.wizard-panel').forEach(function (panel) {
      panel.classList.toggle('active', Number(panel.dataset.step) === step);
    });
    $$('[data-step-marker]').forEach(function (marker) {
      var n = Number(marker.dataset.stepMarker);
      marker.classList.toggle('active', n === step);
      marker.classList.toggle('done', n < step);
    });
    wizard.classList.toggle('at-review', step === 5);
    $('#wizard-back').style.display = step === 1 ? 'none' : '';
    showError('');
    if (step === 5) buildReview();
  }

  function participationType() {
    return ($('#participation-type') && $('#participation-type').value) || 'team';
  }

  function eventFormat() {
    var checked = $('input[name="event_format"]:checked');
    return checked ? checked.value : 'single_performance';
  }

  function syncParticipantPanels() {
    var kind = participationType();
    $('#team-options').classList.toggle('criteria-hidden', kind !== 'team');
    $('#candidate-options').classList.toggle('criteria-hidden', kind !== 'individual');
    var legend = $('#participant-legend-label');
    if (legend) {
      legend.textContent = kind === 'individual' ? 'Participating Individuals' : 'Participating Teams';
    }
    $$('#team-options .participant-check').forEach(function (box) {
      if (kind !== 'team') box.checked = false;
    });
    $$('#candidate-options .participant-check').forEach(function (box) {
      if (kind !== 'individual') box.checked = false;
    });
    updateParticipantCount();
  }

  function updateParticipantCount() {
    var count = $$('.participant-check:checked').filter(function (box) {
      return box.offsetParent !== null || !box.closest('.criteria-hidden');
    }).length;
    // count only visible panel
    var panel = participationType() === 'team' ? '#team-options' : '#candidate-options';
    count = $$(panel + ' .participant-check:checked').length;
    $('#selected-participant-count').textContent = count + ' selected';
  }

  function syncFormatPanels() {
    var fmt = eventFormat();
    $('#multiple-stage-panel').classList.toggle('criteria-hidden', fmt !== 'multiple_stage');
    if (fmt === 'multiple_stage') {
      if (!stages.length) stages = defaultStages(Number($('#stage-count').value) || 2);
      $('#stage-count').value = String(stages.length);
      renderStagesTable();
    }
  }

  function defaultStages(count) {
    count = Math.max(2, Math.min(20, Number(count) || 2));
    var weight = Math.floor(100 / count);
    var rows = [];
    for (var i = 0; i < count; i++) {
      var isFinal = i === count - 1;
      rows.push({
        id: uid('s'),
        name: STAGE_NAME_HINTS[i] || ('Stage ' + (i + 1) + ' Portion'),
        weight: isFinal ? (100 - weight * (count - 1)) : weight,
        qualification_method: isFinal ? null : 'top_ranking',
        qualifiers: isFinal ? null : Math.max(1, count - i),
        minimum_score: null,
        carry_previous_scores: i > 0,
        require_faculty_confirmation: !isFinal,
        is_final: isFinal,
      });
    }
    return rows;
  }

  function syncStagesFromDom() {
    var rows = $$('#stages-table tbody tr');
    if (!rows.length) return;
    stages = rows.map(function (tr, index) {
      var isFinal = index === rows.length - 1;
      var methodEl = $('[data-stage-method]', tr);
      var qualEl = $('[data-stage-qualifiers]', tr);
      var minEl = $('[data-stage-min-score]', tr);
      var carryEl = $('[data-stage-carry]', tr);
      var confirmEl = $('[data-stage-confirm]', tr);
      return {
        id: tr.dataset.id || uid('s'),
        name: ($('[data-stage-name]', tr).value || '').trim(),
        weight: Number($('[data-stage-weight]', tr).value) || 0,
        qualification_method: isFinal ? null : ((methodEl && methodEl.value) || 'top_ranking'),
        qualifiers: isFinal ? null : (Number(qualEl && qualEl.value) || 0),
        minimum_score: (!isFinal && methodEl && methodEl.value === 'minimum_score')
          ? (Number(minEl && minEl.value) || 0)
          : null,
        carry_previous_scores: index === 0 ? false : !!(carryEl && carryEl.value === 'yes'),
        require_faculty_confirmation: isFinal ? false : !!(confirmEl && confirmEl.checked),
        is_final: isFinal,
      };
    });
    updateStagesMeter();
    renderStageFlow();
  }

  function updateStagesMeter() {
    var total = stages.reduce(function (sum, row) { return sum + (Number(row.weight) || 0); }, 0);
    var meter = $('#stages-weight-meter');
    if (!meter) return;
    meter.textContent = 'Stage total: ' + round2(total) + '%';
    meter.classList.toggle('is-ok', Math.abs(total - 100) < 0.01);
    meter.classList.toggle('is-bad', Math.abs(total - 100) >= 0.01);
  }

  function renderStageFlow() {
    var flow = $('#stage-flow');
    if (!flow) return;
    if (!stages.length) {
      flow.innerHTML = '';
      return;
    }
    flow.innerHTML = stages.map(function (stage, index) {
      var chip =
        '<span class="stage-flow-chip' + (stage.is_final ? ' is-final' : '') + '">' +
        '<em>Stage ' + (index + 1) + '</em>' +
        '<strong>' + escapeHtml(stage.name || 'Untitled') + '</strong>' +
        '<span>' + escapeHtml(String(stage.weight || 0)) + '%</span>' +
        '</span>';
      return chip + (index < stages.length - 1 ? '<span class="stage-flow-arrow">→</span>' : '');
    }).join('');
  }

  function methodOptionsHtml(selected) {
    return QUALIFICATION_METHODS.map(function (method) {
      return '<option value="' + method.value + '"' +
        (selected === method.value ? ' selected' : '') + '>' +
        method.label + '</option>';
    }).join('');
  }

  function renderStagesTable() {
    var tbody = $('#stages-table tbody');
    if (!tbody) return;
    tbody.innerHTML = '';
    stages.forEach(function (stage, index) {
      var isFinal = index === stages.length - 1;
      stage.is_final = isFinal;
      if (isFinal) {
        stage.qualification_method = null;
        stage.qualifiers = null;
        stage.require_faculty_confirmation = false;
      }
      var tr = document.createElement('tr');
      tr.dataset.id = stage.id;
      var methodCell = isFinal
        ? '<span class="stage-final-label">Final Stage · No Further Qualification</span>'
        : '<select data-stage-method>' + methodOptionsHtml(stage.qualification_method || 'top_ranking') + '</select>' +
          ((stage.qualification_method || 'top_ranking') === 'minimum_score'
            ? '<input type="number" min="0" step="0.1" data-stage-min-score placeholder="Min score" value="' +
              escapeAttr(stage.minimum_score != null ? stage.minimum_score : '') + '">'
            : '<input type="hidden" data-stage-min-score value="">');
      var qualifiersCell = isFinal
        ? '<span class="stage-final-label">—</span>'
        : '<input type="number" min="1" data-stage-qualifiers value="' +
          escapeAttr(stage.qualifiers != null ? stage.qualifiers : 1) + '">';
      var carryCell = index === 0
        ? '<span class="stage-final-label">N/A</span>'
        : '<select data-stage-carry>' +
          '<option value="yes"' + (stage.carry_previous_scores ? ' selected' : '') + '>Yes</option>' +
          '<option value="no"' + (!stage.carry_previous_scores ? ' selected' : '') + '>No</option>' +
          '</select>';
      var confirmCell = isFinal
        ? '<span class="stage-final-label">Final review on publish</span>'
        : '<label class="inline-check"><input type="checkbox" data-stage-confirm' +
          (stage.require_faculty_confirmation ? ' checked' : '') + '> Required</label>';
      tr.innerHTML =
        '<td>' + (index + 1) + '</td>' +
        '<td><input data-stage-name list="stage-name-suggestions" placeholder="e.g. Talent Competition" value="' +
          escapeAttr(stage.name) + '"></td>' +
        '<td><input type="number" min="0" max="100" step="0.1" data-stage-weight value="' +
          escapeAttr(stage.weight) + '"></td>' +
        '<td>' + methodCell + '</td>' +
        '<td>' + qualifiersCell + '</td>' +
        '<td>' + carryCell + '</td>' +
        '<td>' + confirmCell + '</td>' +
        '<td><div class="criteria-editor-actions">' +
        '<button type="button" data-stage-up' + (index === 0 ? ' disabled' : '') + '>Up</button>' +
        '<button type="button" data-stage-down' + (index === stages.length - 1 ? ' disabled' : '') + '>Down</button>' +
        '<button type="button" data-stage-del' + (stages.length <= 2 ? ' disabled' : '') + '>Delete</button>' +
        '</div></td>';
      tbody.appendChild(tr);
    });

    if (!$('#stage-name-suggestions')) {
      var datalist = document.createElement('datalist');
      datalist.id = 'stage-name-suggestions';
      datalist.innerHTML = STAGE_NAME_HINTS.map(function (name) {
        return '<option value="' + escapeAttr(name) + '"></option>';
      }).join('');
      form.appendChild(datalist);
    }

    bindStageRowEvents();
    updateStagesMeter();
    renderStageFlow();
  }

  function bindStageRowEvents() {
    $$('#stages-table tbody tr').forEach(function (tr) {
      $$('input,select', tr).forEach(function (input) {
        input.addEventListener('input', function () {
          if (input.hasAttribute('data-stage-method')) {
            syncStagesFromDom();
            renderStagesTable();
            return;
          }
          syncStagesFromDom();
        });
        input.addEventListener('change', function () {
          if (input.hasAttribute('data-stage-method')) {
            syncStagesFromDom();
            renderStagesTable();
            return;
          }
          syncStagesFromDom();
        });
      });
      var up = $('[data-stage-up]', tr);
      var down = $('[data-stage-down]', tr);
      var del = $('[data-stage-del]', tr);
      if (up) up.addEventListener('click', function () { moveStage(tr.dataset.id, -1); });
      if (down) down.addEventListener('click', function () { moveStage(tr.dataset.id, 1); });
      if (del) del.addEventListener('click', function () {
        if (stages.length <= 2) return;
        syncStagesFromDom();
        stages = stages.filter(function (row) { return row.id !== tr.dataset.id; });
        $('#stage-count').value = String(stages.length);
        renderStagesTable();
      });
    });
  }

  function moveStage(id, delta) {
    syncStagesFromDom();
    var index = stages.findIndex(function (row) { return row.id === id; });
    var next = index + delta;
    if (index < 0 || next < 0 || next >= stages.length) return;
    var copy = stages.slice();
    var tmp = copy[index];
    copy[index] = copy[next];
    copy[next] = tmp;
    stages = copy;
    renderStagesTable();
  }

  function resizeStages(count) {
    syncStagesFromDom();
    count = Math.max(2, Math.min(20, Number(count) || 2));
    if (count === stages.length) {
      renderStagesTable();
      return;
    }
    if (count > stages.length) {
      while (stages.length < count) {
        stages.push({
          id: uid('s'),
          name: STAGE_NAME_HINTS[stages.length] || ('Stage ' + (stages.length + 1) + ' Portion'),
          weight: 0,
          qualification_method: 'top_ranking',
          qualifiers: 3,
          minimum_score: null,
          carry_previous_scores: true,
          require_faculty_confirmation: true,
          is_final: false,
        });
      }
    } else {
      stages = stages.slice(0, count);
    }
    stages.forEach(function (stage, index) {
      stage.is_final = index === stages.length - 1;
      if (stage.is_final) {
        stage.qualification_method = null;
        stage.qualifiers = null;
        stage.require_faculty_confirmation = false;
      } else if (!stage.qualification_method) {
        stage.qualification_method = 'top_ranking';
        stage.qualifiers = stage.qualifiers || 3;
        stage.require_faculty_confirmation = true;
      }
    });
    $('#stage-count').value = String(stages.length);
    renderStagesTable();
  }

  function round2(n) { return Math.round(n * 100) / 100; }

  function escapeAttr(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;');
  }

  function escapeHtml(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  function uid(prefix) {
    return prefix + Math.random().toString(36).slice(2, 9);
  }

  function defaultCriteria() {
    return [
      { id: uid('c'), name: 'Content / Presentation', description: 'Quality and organization.', weight: 40, max_score: 100 },
      { id: uid('c'), name: 'Delivery', description: 'Clarity, confidence, and stage presence.', weight: 35, max_score: 100 },
      { id: uid('c'), name: 'Creativity', description: 'Originality and impact.', weight: 25, max_score: 100 },
    ];
  }

  function defaultPoints(classification) {
    if (classification === 'minor') {
      return [
        { label: '1st Place', points: 10 },
        { label: '2nd Place', points: 7 },
        { label: '3rd Place', points: 5 },
        { label: '4th Place', points: 3 },
      ];
    }
    return [
      { label: '1st Place', points: 15 },
      { label: '2nd Place', points: 10 },
      { label: '3rd Place', points: 7 },
      { label: '4th Place', points: 5 },
    ];
  }

  function renderCriteria() {
    var tbody = $('#criteria-table tbody');
    tbody.innerHTML = '';
    criteria.forEach(function (row, index) {
      var tr = document.createElement('tr');
      tr.dataset.id = row.id;
      tr.innerHTML =
        '<td><input data-c-name value="' + escapeAttr(row.name) + '"></td>' +
        '<td><textarea data-c-desc>' + escapeHtml(row.description) + '</textarea></td>' +
        '<td><input type="number" min="0" step="0.1" data-c-weight value="' + escapeAttr(row.weight) + '"></td>' +
        '<td><input type="number" min="0" step="0.1" data-c-max value="' + escapeAttr(row.max_score) + '"></td>' +
        '<td><div class="criteria-editor-actions">' +
        '<button type="button" data-up ' + (index === 0 ? 'disabled' : '') + '>Up</button>' +
        '<button type="button" data-down ' + (index === criteria.length - 1 ? 'disabled' : '') + '>Down</button>' +
        '<button type="button" data-del>Delete</button>' +
        '</div></td>';
      tbody.appendChild(tr);
    });
    bindCriteriaRowEvents();
    syncCriteriaFromDom();
    renderTieBreaks();
  }

  function bindCriteriaRowEvents() {
    $$('#criteria-table tbody tr').forEach(function (tr) {
      $$('input,textarea', tr).forEach(function (input) {
        input.addEventListener('input', function () {
          syncCriteriaFromDom();
          renderTieBreaks();
        });
      });
      var up = $('[data-up]', tr);
      var down = $('[data-down]', tr);
      var del = $('[data-del]', tr);
      if (up) up.addEventListener('click', function () { moveCriterion(tr.dataset.id, -1); });
      if (down) down.addEventListener('click', function () { moveCriterion(tr.dataset.id, 1); });
      if (del) del.addEventListener('click', function () {
        criteria = criteria.filter(function (row) { return row.id !== tr.dataset.id; });
        renderCriteria();
      });
    });
  }

  function syncCriteriaFromDom() {
    criteria = $$('#criteria-table tbody tr').map(function (tr) {
      return {
        id: tr.dataset.id,
        name: $('[data-c-name]', tr).value.trim(),
        description: $('[data-c-desc]', tr).value.trim(),
        weight: Number($('[data-c-weight]', tr).value) || 0,
        max_score: Number($('[data-c-max]', tr).value) || 0,
      };
    });
    var total = criteria.reduce(function (sum, row) { return sum + row.weight; }, 0);
    var meter = $('#criteria-weight-meter');
    meter.textContent = 'Criteria total: ' + round2(total) + '%';
    meter.classList.toggle('is-ok', Math.abs(total - 100) < 0.01);
    meter.classList.toggle('is-bad', Math.abs(total - 100) >= 0.01);
  }

  function moveCriterion(id, delta) {
    var index = criteria.findIndex(function (row) { return row.id === id; });
    var next = index + delta;
    if (index < 0 || next < 0 || next >= criteria.length) return;
    var copy = criteria.slice();
    var tmp = copy[index];
    copy[index] = copy[next];
    copy[next] = tmp;
    criteria = copy;
    renderCriteria();
  }

  function renderDeductions() {
    var enabled = $('#deductions-enabled').checked;
    $('#deductions-panel').classList.toggle('criteria-hidden', !enabled);
    var tbody = $('#deductions-table tbody');
    tbody.innerHTML = '';
    deductions.forEach(function (row, index) {
      var tr = document.createElement('tr');
      tr.dataset.index = String(index);
      tr.innerHTML =
        '<td><input data-d-name value="' + escapeAttr(row.name) + '"></td>' +
        '<td><input data-d-desc value="' + escapeAttr(row.description) + '"></td>' +
        '<td><select data-d-type><option value="fixed"' + (row.deduction_type === 'fixed' ? ' selected' : '') + '>Fixed Points</option>' +
        '<option value="percentage"' + (row.deduction_type === 'percentage' ? ' selected' : '') + '>Percentage</option></select></td>' +
        '<td><input type="number" min="0" step="0.1" data-d-value value="' + escapeAttr(row.value) + '"></td>' +
        '<td><button type="button" data-d-del>Delete</button></td>';
      tbody.appendChild(tr);
    });
    $$('#deductions-table [data-d-del]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var tr = btn.closest('tr');
        deductions.splice(Number(tr.dataset.index), 1);
        renderDeductions();
      });
    });
    $$('#deductions-table input, #deductions-table select').forEach(function (input) {
      input.addEventListener('change', syncDeductionsFromDom);
      input.addEventListener('input', syncDeductionsFromDom);
    });
  }

  function syncDeductionsFromDom() {
    deductions = $$('#deductions-table tbody tr').map(function (tr) {
      return {
        name: $('[data-d-name]', tr).value.trim(),
        description: $('[data-d-desc]', tr).value.trim(),
        deduction_type: $('[data-d-type]', tr).value,
        value: Number($('[data-d-value]', tr).value) || 0,
      };
    });
  }

  function renderPoints() {
    var editor = $('#points-editor');
    editor.innerHTML = '';
    points.forEach(function (row, index) {
      var div = document.createElement('div');
      div.innerHTML =
        '<input data-p-label value="' + escapeAttr(row.label) + '" aria-label="Rank label">' +
        '<input type="number" min="0" data-p-points value="' + escapeAttr(row.points) + '" aria-label="Points">' +
        '<button type="button" data-p-up ' + (index === 0 ? 'disabled' : '') + '>Up</button>' +
        '<button type="button" data-p-down ' + (index === points.length - 1 ? 'disabled' : '') + '>Down</button>' +
        '<button type="button" data-p-del>Delete</button>';
      div.style.gridTemplateColumns = '1fr 90px auto auto auto';
      editor.appendChild(div);
    });
    $$('#points-editor [data-p-del]').forEach(function (btn, index) {
      btn.addEventListener('click', function () {
        points.splice(index, 1);
        renderPoints();
      });
    });
    $$('#points-editor [data-p-up]').forEach(function (btn, index) {
      btn.addEventListener('click', function () {
        if (index === 0) return;
        var tmp = points[index - 1];
        points[index - 1] = points[index];
        points[index] = tmp;
        renderPoints();
      });
    });
    $$('#points-editor [data-p-down]').forEach(function (btn, index) {
      btn.addEventListener('click', function () {
        if (index >= points.length - 1) return;
        var tmp = points[index + 1];
        points[index + 1] = points[index];
        points[index] = tmp;
        renderPoints();
      });
    });
    $$('#points-editor input').forEach(function (input) {
      input.addEventListener('input', syncPointsFromDom);
    });
  }

  function syncPointsFromDom() {
    points = $$('#points-editor > div').map(function (row) {
      return {
        label: $('[data-p-label]', row).value.trim(),
        points: Number($('[data-p-points]', row).value) || 0,
      };
    });
  }

  function renderTieBreaks(preferred) {
    var wrap = $('#tie-break-list');
    var existing = preferred || $$('#tie-break-list [data-method]').map(function (el) {
      return {
        method: el.dataset.method,
        criterion_id: ($('[data-tie-criterion]', el) && $('[data-tie-criterion]', el).value) || '',
        checked: $('input[type=checkbox]', el).checked,
      };
    });
    wrap.innerHTML = '';
    TIE_METHODS.forEach(function (item, index) {
      var prior = existing.find(function (row) { return row.method === item.method; });
      var checked = preferred ? !!prior : (prior ? prior.checked : index < 3);
      var label = document.createElement('label');
      label.dataset.method = item.method;
      var criterionSelect = '';
      if (item.method === 'highest_selected_criterion') {
        criterionSelect = '<select data-tie-criterion>' + criteria.map(function (row) {
          var selected = prior && prior.criterion_id === row.id ? ' selected' : '';
          return '<option value="' + escapeAttr(row.id) + '"' + selected + '>' + escapeHtml(row.name || 'Criterion') + '</option>';
        }).join('') + '</select>';
      } else {
        criterionSelect = '<span></span>';
      }
      label.innerHTML =
        '<input type="checkbox"' + (checked ? ' checked' : '') + '>' +
        '<span><strong>' + escapeHtml(item.label) + '</strong></span>' +
        criterionSelect;
      wrap.appendChild(label);
    });
  }

  function collectParticipants() {
    var panel = participationType() === 'team' ? '#team-options' : '#candidate-options';
    return $$(panel + ' .participant-check:checked').map(function (box) {
      return Number(box.value);
    });
  }

  function collectRounds() {
    var fmt = eventFormat();
    if (fmt === 'single_performance') return [];
    syncStagesFromDom();
    return stages.map(function (stage, index) {
      var isFinal = index === stages.length - 1;
      return {
        stage_number: index + 1,
        name: stage.name,
        weight: Number(stage.weight) || 0,
        qualification_method: isFinal ? null : (stage.qualification_method || 'top_ranking'),
        qualifiers: isFinal ? null : (Number(stage.qualifiers) || 0),
        minimum_score: (!isFinal && stage.qualification_method === 'minimum_score')
          ? (Number(stage.minimum_score) || 0)
          : null,
        carry_previous_scores: index === 0 ? false : !!stage.carry_previous_scores,
        require_faculty_confirmation: isFinal ? false : !!stage.require_faculty_confirmation,
        is_final: isFinal,
        status: index === 0 ? 'open' : 'locked',
      };
    });
  }

  function collectScoreSettings() {
    return {
      min_score: Number($('#min-score').value) || 0,
      max_score: Number($('#max-score').value) || 100,
      allow_decimal: $('#allow-decimal').checked,
      decimal_places: Number($('#decimal-places').value) || 0,
      show_criteria_description: $('#show-criteria-description').checked,
    };
  }

  function collectResultProcessing() {
    var out = {};
    $$('[data-rp]').forEach(function (input) {
      out[input.dataset.rp] = input.checked;
    });
    if (eventFormat() === 'multiple_stage') {
      out.stage_tiebreak_method = $('#stage-tiebreak-method').value || 'highest_selected_criterion';
    }
    return out;
  }

  function collectJudgeSettings() {
    var out = {};
    $$('[data-js]').forEach(function (input) {
      out[input.dataset.js] = input.checked;
    });
    return out;
  }

  function collectTieBreaks() {
    return $$('#tie-break-list label').filter(function (label) {
      return $('input[type=checkbox]', label).checked;
    }).map(function (label) {
      var row = { method: label.dataset.method };
      var criterion = $('[data-tie-criterion]', label);
      if (criterion) row.criterion_id = criterion.value;
      return row;
    });
  }

  function collectJudges() {
    return $$('.judge-check:checked').map(function (box) { return Number(box.value); });
  }

  function updateJudgeCount() {
    $('#selected-judge-count').textContent = collectJudges().length + ' selected';
  }

  function syncHiddenFields() {
    syncCriteriaFromDom();
    syncDeductionsFromDom();
    syncPointsFromDom();
    $('#participant-ids').value = JSON.stringify(collectParticipants());
    $('#rounds-config').value = JSON.stringify(collectRounds());
    $('#criteria-config').value = JSON.stringify(criteria);
    $('#score-settings').value = JSON.stringify(collectScoreSettings());
    $('#deductions-config').value = JSON.stringify(deductions);
    $('#result-processing-config').value = JSON.stringify(collectResultProcessing());
    $('#judge-settings').value = JSON.stringify(collectJudgeSettings());
    $('#tie-break-rules').value = JSON.stringify(collectTieBreaks());
    $('#judge-ids').value = JSON.stringify(collectJudges());
    $('#points-config').value = JSON.stringify(points);
  }

  function validateStep(current) {
    if (current === 1) {
      if (!form.event_name.value.trim()) return 'Event Name is required.';
      if (!form.category.value) return 'Category is required.';
      if (!form.event_classification.value) return 'Event Classification is required.';
      if (!form.venue.value.trim()) return 'Venue is required.';
      if (!form.start_date.value || !form.end_date.value) return 'Start Date and End Date are required.';
      if (form.end_date.value < form.start_date.value) return 'End Date cannot precede Start Date.';
    }
    if (current === 2) {
      if (collectParticipants().length < 1) return 'Select at least one participant or team.';
      var fmt = eventFormat();
      if (fmt === 'multiple_stage') {
        syncStagesFromDom();
        if (stages.length < 2) return 'Multiple Stage Competition requires at least two stages.';
        var names = {};
        var participantCount = collectParticipants().length;
        for (var i = 0; i < stages.length; i++) {
          var stage = stages[i];
          var nameKey = (stage.name || '').trim().toLowerCase();
          if (!nameKey) return 'Each stage needs a unique custom name.';
          if (/^round\s*\d+$/i.test(stage.name.trim())) {
            return 'Use a descriptive stage name instead of generic Round labels.';
          }
          if (names[nameKey]) return 'Each stage name must be unique.';
          names[nameKey] = true;
          if ((Number(stage.weight) || 0) <= 0) return 'Every stage weight must be greater than zero.';
          if (!stage.is_final) {
            if (!stage.qualification_method) return 'Select a qualification method for every non-final stage.';
            var quals = Number(stage.qualifiers) || 0;
            if (quals < 1) return 'Each qualification stage needs at least one qualifier.';
            if (quals > participantCount) {
              return 'Number of qualifiers cannot exceed the number of selected contestants.';
            }
            if (stage.qualification_method === 'minimum_score' && (Number(stage.minimum_score) || 0) < 0) {
              return 'Minimum score must be zero or greater.';
            }
          }
        }
        var total = stages.reduce(function (sum, row) { return sum + (Number(row.weight) || 0); }, 0);
        if (Math.abs(total - 100) >= 0.01) return 'Total stage weight must equal 100%.';
      }
    }
    if (current === 3) {
      syncCriteriaFromDom();
      if (!criteria.length) return 'Create at least one judging criterion.';
      var cTotal = criteria.reduce(function (sum, row) { return sum + row.weight; }, 0);
      if (Math.abs(cTotal - 100) >= 0.01) return 'Total criteria weight must equal 100%.';
      if (criteria.some(function (row) { return !row.name || row.max_score <= 0; })) {
        return 'Each criterion needs a name and maximum score.';
      }
      var settings = collectScoreSettings();
      if (settings.min_score >= settings.max_score) return 'Minimum score must be less than maximum score.';
      if ($('#deductions-enabled').checked) {
        syncDeductionsFromDom();
        if (deductions.some(function (row) { return !row.name; })) return 'Each penalty needs a name.';
      }
    }
    if (current === 4) {
      if (!$('#chief-judge').value) return 'Chief Judge is required.';
      if (!form.faculty_account.value) return 'Faculty In Charge is required.';
      var judges = collectJudges();
      if (!judges.length) return 'Assign at least one judge.';
      if ($('#remove-high-low').checked && judges.length < 3) {
        return 'Remove Highest and Lowest Judge Scores requires at least three judges.';
      }
      syncPointsFromDom();
      var labels = {};
      for (var i = 0; i < points.length; i++) {
        var key = (points[i].label || '').toLowerCase();
        if (!key) return 'Championship point ranks require labels.';
        if (labels[key]) return 'Championship point rankings must be unique.';
        labels[key] = true;
      }
    }
    return '';
  }

  function buildReview() {
    syncHiddenFields();
    var fmt = eventFormat();
    var method = ($('input[name="criteria_score_method"]:checked') || {}).value || '';
    var methodLabels = {
      weighted_percentage: 'Weighted Percentage',
      raw_score: 'Raw Score',
      average_score: 'Average Score',
      ranking_based: 'Ranking-Based Score',
    };
    var formatLabels = {
      single_performance: 'Single Performance',
      multiple_stage: 'Multiple Stage Competition',
      multiple_rounds: 'Multiple Stage Competition',
      preliminary_final: 'Multiple Stage Competition',
    };
    var panel = participationType() === 'team' ? '#team-options' : '#candidate-options';
    var names = $$(panel + ' .participant-check:checked').map(function (box) { return box.dataset.name; });
    var judgeNames = $$('.judge-check:checked').map(function (box) { return box.dataset.name; });
    var chief = $('#chief-judge');
    var faculty = form.faculty_account;
    var stageSummary = 'Single performance';
    if (fmt === 'multiple_stage') {
      stageSummary = collectRounds().map(function (stage) {
        return stage.name + ' (' + stage.weight + '%)';
      }).join(' → ');
    }
    var rows = [
      ['Event Name', form.event_name.value],
      ['Category', form.category.value],
      ['Event Classification', form.event_classification.options[form.event_classification.selectedIndex].text],
      ['Division', form.division.value || '—'],
      ['Participation Type', participationType() === 'team' ? 'Team' : 'Individual'],
      ['Venue', form.venue.value],
      ['Event Date Range', form.start_date.value + ' to ' + form.end_date.value],
      ['Participants', names.join(', ') || '—'],
      ['Event Format', formatLabels[fmt] || fmt],
      ['Competition Structure', stageSummary],
      ['Stage Tie-Break', fmt === 'multiple_stage' ? ($('#stage-tiebreak-method').selectedOptions[0].text || '—') : '—'],
      ['Judging Criteria', criteria.map(function (row) { return row.name + ' (' + row.weight + '%)'; }).join(' · ') || '—'],
      ['Scoring Method', methodLabels[method] || method],
      ['Score Settings', 'Min ' + $('#min-score').value + ' / Max ' + $('#max-score').value + ' · Decimals ' + $('#decimal-places').value],
      ['Penalties', $('#deductions-enabled').checked ? (deductions.map(function (row) { return row.name; }).join(', ') || 'Enabled') : 'Disabled'],
      ['Chief Judge', chief.options[chief.selectedIndex] ? chief.options[chief.selectedIndex].text : '—'],
      ['Assigned Judges', judgeNames.join(', ') || '—'],
      ['Faculty In Charge', faculty.options[faculty.selectedIndex] ? faculty.options[faculty.selectedIndex].text : '—'],
      ['Result Processing', Object.keys(collectResultProcessing()).filter(function (key) { return collectResultProcessing()[key]; }).join(', ') || '—'],
      ['Tie-Breaking', collectTieBreaks().map(function (row) { return row.method; }).join(' → ') || '—'],
      ['Championship Points', points.map(function (row) { return row.label + ': ' + row.points; }).join(' · ') || '—'],
      ['Intended Status', $('#status-preview').value === 'published' ? 'Published' : 'Draft'],
    ];
    $('#review-summary').innerHTML = rows.map(function (row) {
      return '<div><span>' + escapeHtml(row[0]) + '</span><strong>' + escapeHtml(row[1]) + '</strong></div>';
    }).join('');
  }

  function resetWizard() {
    form.reset();
    form.action = '/admin/events/criteria/';
    $('#publication-status').value = 'draft';
    $('#wizard-title').textContent = 'Create criteria event';
    criteria = defaultCriteria();
    deductions = [];
    points = defaultPoints('major');
    stages = defaultStages(2);
    $('#stage-count').value = '2';
    $('#stage-tiebreak-method').value = 'highest_selected_criterion';
    renderCriteria();
    renderDeductions();
    renderPoints();
    renderStagesTable();
    syncParticipantPanels();
    syncFormatPanels();
    updateJudgeCount();
    setStep(1);
    var specialSel = document.getElementById('special-event-type');
    if (specialSel) specialSel.value = '';
    var typeInput = document.getElementById('special-event-type-input');
    if (typeInput) typeInput.value = '';
    if (window.PageantWizard && typeof window.PageantWizard.hydrate === 'function') {
      // Force exit pageant mode after reset
      var cat = form.category;
      if (cat) cat.dispatchEvent(new Event('change'));
    }
  }

  function normalizeLoadedFormat(value) {
    if (value === 'multiple_rounds' || value === 'preliminary_final') return 'multiple_stage';
    return value || 'single_performance';
  }

  function stagesFromLegacyConfig(event) {
    var config = event.rounds_config || [];
    if (event.event_format === 'preliminary_final') {
      var cfg = Array.isArray(config) ? (config[0] || {}) : (config || {});
      return [
        {
          id: uid('s'),
          name: 'Preliminary Round',
          weight: Number(cfg.prelim_percentage) || 40,
          qualification_method: 'top_ranking',
          qualifiers: Number(cfg.finalists) || 4,
          minimum_score: null,
          carry_previous_scores: false,
          require_faculty_confirmation: true,
          is_final: false,
        },
        {
          id: uid('s'),
          name: 'Final Round',
          weight: Number(cfg.final_percentage) || 60,
          qualification_method: null,
          qualifiers: null,
          minimum_score: null,
          carry_previous_scores: !!cfg.carry_prelim_scores,
          require_faculty_confirmation: false,
          is_final: true,
        },
      ];
    }
    if (Array.isArray(config) && config.length) {
      return config.map(function (row, index) {
        var isFinal = index === config.length - 1 || !!row.is_final;
        return {
          id: uid('s'),
          name: row.name || STAGE_NAME_HINTS[index] || ('Stage ' + (index + 1) + ' Portion'),
          weight: Number(row.weight) || 0,
          qualification_method: isFinal ? null : (row.qualification_method || 'top_ranking'),
          qualifiers: isFinal ? null : (row.qualifiers != null ? row.qualifiers : 3),
          minimum_score: row.minimum_score != null ? row.minimum_score : null,
          carry_previous_scores: index === 0 ? false : (row.carry_previous_scores !== false),
          require_faculty_confirmation: isFinal ? false : (row.require_faculty_confirmation !== false),
          is_final: isFinal,
        };
      });
    }
    return defaultStages(2);
  }

  function fillEditor(event) {
    form.action = '/admin/events/criteria/' + event.id + '/edit/';
    $('#wizard-title').textContent = 'Edit criteria event';
    form.event_name.value = event.name || '';
    form.category.value = event.category || '';
    form.event_classification.value = event.classification || '';
    form.participation_type.value = event.participation_type || 'team';
    form.division.value = event.division === '—' ? '' : (event.division || '');
    form.venue.value = event.venue || '';
    form.start_date.value = event.start_date || '';
    form.end_date.value = event.end_date || '';
    $('#status-preview').value = event.publication_status || 'draft';
    var loadedFormat = normalizeLoadedFormat(event.event_format);
    $$('input[name="event_format"]').forEach(function (input) {
      input.checked = input.value === loadedFormat;
    });
    $$('input[name="criteria_score_method"]').forEach(function (input) {
      input.checked = input.value === event.criteria_score_method;
    });
    syncParticipantPanels();
    var ids = (event.participant_ids || []).map(String);
    $$('.participant-check').forEach(function (box) {
      box.checked = ids.indexOf(box.value) !== -1;
    });
    updateParticipantCount();
    stages = stagesFromLegacyConfig(event);
    $('#stage-count').value = String(Math.max(2, stages.length));
    var stageTie = ((event.result_processing_config || {}).stage_tiebreak_method) || 'highest_selected_criterion';
    $('#stage-tiebreak-method').value = stageTie;
    syncFormatPanels();
    criteria = (event.judging_criteria_config || []).map(function (row) {
      return {
        id: row.id || uid('c'),
        name: row.name || '',
        description: row.description || '',
        weight: row.weight || 0,
        max_score: row.max_score || 0,
      };
    });
    if (!criteria.length) criteria = defaultCriteria();
    renderCriteria();
    var settings = event.score_settings || {};
    $('#min-score').value = settings.min_score != null ? settings.min_score : 0;
    $('#max-score').value = settings.max_score != null ? settings.max_score : 100;
    $('#decimal-places').value = String(settings.decimal_places != null ? settings.decimal_places : 2);
    $('#allow-decimal').checked = !!settings.allow_decimal;
    $('#show-criteria-description').checked = !!settings.show_criteria_description;
    $('#deductions-enabled').checked = !!event.deductions_enabled;
    deductions = event.deductions_config || [];
    renderDeductions();
    $('#chief-judge').value = event.chief_judge_id || '';
    form.faculty_account.value = event.faculty_account_id || '';
    if (form.scoresheet_template) form.scoresheet_template.value = event.scoresheet_template_id || '';
    var judgeIds = (event.judge_ids || []).map(String);
    $$('.judge-check').forEach(function (box) {
      box.checked = judgeIds.indexOf(box.value) !== -1;
    });
    updateJudgeCount();
    var rp = event.result_processing_config || {};
    $$('[data-rp]').forEach(function (input) {
      input.checked = !!rp[input.dataset.rp];
    });
    var js = event.judge_settings || {};
    $$('[data-js]').forEach(function (input) {
      input.checked = !!js[input.dataset.js];
    });
    points = event.points_config && event.points_config.length
      ? event.points_config.slice()
      : defaultPoints(event.classification);
    renderPoints();
    form.apply_championship_points.checked = event.apply_championship_points !== false;
    $('#points-fieldset').classList.toggle('points-disabled', !form.apply_championship_points.checked);
    renderTieBreaks((event.tie_break_rules || []).map(function (row) {
      return {
        method: row.method,
        criterion_id: row.criterion_id || '',
        checked: true,
      };
    }));
    setStep(1);
    if (window.PageantWizard && event.is_pageant) {
      window.PageantWizard.hydrate(event);
    } else if (window.PageantWizard) {
      var specialSel = document.getElementById('special-event-type');
      if (specialSel) specialSel.value = event.special_event_type || '';
      var cat = form.category;
      if (cat) cat.dispatchEvent(new Event('change'));
    }
  }

  function openWizard() {
    wizard.showModal();
  }

  function closeWizard() {
    wizard.close();
  }

  function findEvent(id) {
    return events.find(function (row) { return String(row.id) === String(id); });
  }

  function renderView(event) {
    var settings = event.score_settings || {};
    var rp = event.result_processing_config || {};
    var rows = [
      ['Event Name', event.name],
      ['Category', event.category],
      ['Special Event Type', event.is_pageant ? 'Pageant' : (event.special_event_type || '—')],
      ['Pageant Format', event.pageant_format_label || ''],
      ['Event Classification', event.classification_label],
      ['Division', event.division],
      ['Participation Type', event.participation_label],
      ['Venue', event.venue],
      ['Event Date Range', event.start_date + ' to ' + event.end_date],
      ['Participants', (event.participant_names || []).join(', ') || '—'],
      ['Event Format', event.event_format_label],
      ['Competition Structure', (event.rounds_config || []).map(function (row) {
        return (row.name || 'Stage') + ' (' + (row.weight != null ? row.weight : '—') + '%)';
      }).join(' → ') || 'Single performance'],
      ['Recommended Next', (event.next_actions || []).join(' · ') || ''],
      ['Judging Criteria', (event.judging_criteria_config || []).map(function (row) {
        return row.name + ' (' + row.weight + '%)';
      }).join(' · ') || '—'],
      ['Scoring Method', event.criteria_score_method_label],
      ['Score Settings', 'Min ' + (settings.min_score != null ? settings.min_score : '—') +
        ' / Max ' + (settings.max_score != null ? settings.max_score : '—') +
        ' · Decimals ' + (settings.decimal_places != null ? settings.decimal_places : '—')],
      ['Penalties and Deductions', event.deductions_enabled
        ? ((event.deductions_config || []).map(function (row) { return row.name; }).join(', ') || 'Enabled')
        : 'Disabled'],
      ['Assigned Judges', (event.judge_names || []).join(', ') || '—'],
      ['Chief Judge', event.chief_judge_name],
      ['Faculty In Charge', event.faculty_name],
      ['Result Processing', Object.keys(rp).filter(function (key) { return rp[key]; }).join(', ') || '—'],
      ['Tie-Breaking Rules', (event.tie_break_rules || []).map(function (row) { return row.method; }).join(' → ') || '—'],
      ['Championship Points', (event.points_config || []).map(function (row) {
        return row.label + ': ' + row.points;
      }).join(' · ') || '—'],
      ['Status', event.publication_label],
    ].filter(function (row) { return row[1] !== '' && row[1] != null; });
    $('#view-summary').innerHTML = rows.map(function (row) {
      return '<div><span>' + escapeHtml(row[0]) + '</span><strong>' + escapeHtml(row[1]) + '</strong></div>';
    }).join('');
    $('#view-event').showModal();
  }

  // Events
  $('#open-wizard').addEventListener('click', function () {
    resetWizard();
    openWizard();
  });
  $('#close-wizard').addEventListener('click', closeWizard);
  $('#wizard-back').addEventListener('click', function () {
    if (step > 1) setStep(step - 1);
  });
  $('#wizard-next').addEventListener('click', function () {
    var error = validateStep(step);
    if (error) {
      showError(error);
      return;
    }
    setStep(step + 1);
  });
  $$('.final-action').forEach(function (btn) {
    btn.addEventListener('click', function () {
      $('#publication-status').value = btn.dataset.status;
    });
  });
  form.addEventListener('submit', function (e) {
    if (window.PageantWizard && window.PageantWizard.isActive()) {
      // Pageant wizard owns validation + hidden-field sync.
      window.PageantWizard.sync();
      return;
    }
    var error = validateStep(1) || validateStep(2) || validateStep(3) || validateStep(4);
    if (error) {
      e.preventDefault();
      showError(error);
      return;
    }
    syncHiddenFields();
  });

  $('#participation-type').addEventListener('change', syncParticipantPanels);
  $$('input[name="event_format"]').forEach(function (input) {
    input.addEventListener('change', syncFormatPanels);
  });
  $('#stage-count').addEventListener('change', function () {
    resizeStages($('#stage-count').value);
  });
  $('#add-stage').addEventListener('click', function () {
    resizeStages((stages.length || 2) + 1);
  });
  $('#participant-search').addEventListener('input', function () {
    var q = $('#participant-search').value.trim().toLowerCase();
    var panel = participationType() === 'team' ? '#team-options' : '#candidate-options';
    $$(panel + ' label[data-search]').forEach(function (label) {
      label.hidden = q && label.dataset.search.indexOf(q) === -1;
    });
  });
  document.addEventListener('change', function (e) {
    if (e.target.classList.contains('participant-check')) updateParticipantCount();
    if (e.target.classList.contains('judge-check')) updateJudgeCount();
  });
  $('#add-criterion').addEventListener('click', function () {
    criteria.push({ id: uid('c'), name: '', description: '', weight: 0, max_score: 100 });
    renderCriteria();
  });
  $('#deductions-enabled').addEventListener('change', renderDeductions);
  $('#add-deduction').addEventListener('click', function () {
    deductions.push({ name: '', description: '', deduction_type: 'fixed', value: 0 });
    renderDeductions();
  });
  $('#add-points-row').addEventListener('click', function () {
    points.push({ label: (points.length + 1) + 'th Place', points: 0 });
    renderPoints();
  });
  form.event_classification.addEventListener('change', function () {
    if (!points.length || points.length === 4) {
      points = defaultPoints(form.event_classification.value);
      renderPoints();
    }
  });
  form.apply_championship_points.addEventListener('change', function () {
    $('#points-fieldset').classList.toggle('points-disabled', !form.apply_championship_points.checked);
  });
  $('#status-preview').addEventListener('change', function () {
    $('#publication-status').value = $('#status-preview').value;
  });

  $$('[data-view]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var event = findEvent(btn.dataset.view);
      if (event) renderView(event);
    });
  });
  $$('[data-edit]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var event = findEvent(btn.dataset.edit);
      if (!event) return;
      resetWizard();
      fillEditor(event);
      openWizard();
    });
  });
  $$('[data-delete]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      if (btn.dataset.finalized === '1' && !window.CRITERIA_IS_SUPERUSER) {
        alert('Events with finalized results can only be deleted by an administrator.');
        return;
      }
      $('#delete-name').textContent = btn.dataset.name || 'this event';
      $('#delete-form').action = '/admin/events/criteria/' + btn.dataset.delete + '/delete/';
      $('#delete-event').showModal();
    });
  });
  $('[data-cancel-delete]').addEventListener('click', function () { $('#delete-event').close(); });
  $('[data-close-view]').addEventListener('click', function () { $('#view-event').close(); });

  // Init
  resetWizard();
  if (window.CRITERIA_EVENT_EDIT_ID) {
    var editEvent = findEvent(window.CRITERIA_EVENT_EDIT_ID);
    if (editEvent) {
      fillEditor(editEvent);
      openWizard();
    }
  } else if (window.CRITERIA_OPEN_CREATE) {
    openWizard();
  }
}());
