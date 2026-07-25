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
  var events = [];

  try {
    events = JSON.parse($('#criteria-events-data').textContent || '[]');
  } catch (_) {
    events = [];
  }

  var TIE_METHODS = [
    { method: 'highest_selected_criterion', label: 'Highest Score in Selected Criterion' },
    { method: 'highest_chief_judge', label: 'Highest Chief Judge Score' },
    { method: 'lowest_deduction', label: 'Lowest Deduction' },
    { method: 'majority_decision', label: 'Majority Decision' },
    { method: 'manual_decision', label: 'Manual Decision' },
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
    $('#multiple-rounds-panel').classList.toggle('criteria-hidden', fmt !== 'multiple_rounds');
    $('#prelim-final-panel').classList.toggle('criteria-hidden', fmt !== 'preliminary_final');
    if (fmt === 'multiple_rounds') renderRoundsTable();
    updatePrelimMeter();
  }

  function renderRoundsTable() {
    var count = Math.max(2, Math.min(10, Number($('#round-count').value) || 2));
    $('#round-count').value = count;
    var tbody = $('#rounds-table tbody');
    var existing = $$('#rounds-table tbody tr');
    tbody.innerHTML = '';
    for (var i = 0; i < count; i++) {
      var prev = existing[i];
      var name = prev ? prev.querySelector('[data-round-name]').value : ('Round ' + (i + 1));
      var weight = prev ? prev.querySelector('[data-round-weight]').value : String(Math.floor(100 / count));
      var qualifiers = prev ? prev.querySelector('[data-round-qualifiers]').value : '4';
      var tr = document.createElement('tr');
      tr.innerHTML =
        '<td><input data-round-name value="' + escapeAttr(name) + '"></td>' +
        '<td><input type="number" min="0" max="100" step="0.1" data-round-weight value="' + escapeAttr(weight) + '"></td>' +
        '<td>' + (i < count - 1
          ? '<input type="number" min="0" data-round-qualifiers value="' + escapeAttr(qualifiers) + '">'
          : '<span>—</span><input type="hidden" data-round-qualifiers value="0">') +
        '</td>';
      tbody.appendChild(tr);
    }
    $$('#rounds-table [data-round-weight]').forEach(function (input) {
      input.addEventListener('input', updateRoundsMeter);
    });
    updateRoundsMeter();
  }

  function updateRoundsMeter() {
    var total = $$('#rounds-table [data-round-weight]').reduce(function (sum, input) {
      return sum + (Number(input.value) || 0);
    }, 0);
    var meter = $('#rounds-weight-meter');
    meter.textContent = 'Round total: ' + round2(total) + '%';
    meter.classList.toggle('is-ok', Math.abs(total - 100) < 0.01);
    meter.classList.toggle('is-bad', Math.abs(total - 100) >= 0.01);
  }

  function updatePrelimMeter() {
    var total = (Number($('#prelim-percentage').value) || 0) + (Number($('#final-percentage').value) || 0);
    var meter = $('#prelim-weight-meter');
    meter.textContent = 'Prelim + Final total: ' + round2(total) + '%';
    meter.classList.toggle('is-ok', Math.abs(total - 100) < 0.01);
    meter.classList.toggle('is-bad', Math.abs(total - 100) >= 0.01);
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
    if (fmt === 'multiple_rounds') {
      return $$('#rounds-table tbody tr').map(function (tr) {
        return {
          name: $('[data-round-name]', tr).value.trim(),
          weight: Number($('[data-round-weight]', tr).value) || 0,
          qualifiers: Number($('[data-round-qualifiers]', tr).value) || 0,
        };
      });
    }
    return {
      prelim_participants: Number($('#prelim-participants').value) || 0,
      finalists: Number($('#finalists').value) || 0,
      carry_prelim_scores: $('#carry-prelim').value === 'yes',
      prelim_percentage: Number($('#prelim-percentage').value) || 0,
      final_percentage: Number($('#final-percentage').value) || 0,
    };
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
      if (fmt === 'multiple_rounds') {
        updateRoundsMeter();
        var total = $$('#rounds-table [data-round-weight]').reduce(function (sum, input) {
          return sum + (Number(input.value) || 0);
        }, 0);
        if (Math.abs(total - 100) >= 0.01) return 'Total round percentage must equal 100%.';
      }
      if (fmt === 'preliminary_final') {
        updatePrelimMeter();
        var p = Number($('#prelim-participants').value) || 0;
        var f = Number($('#finalists').value) || 0;
        var pct = (Number($('#prelim-percentage').value) || 0) + (Number($('#final-percentage').value) || 0);
        if (p < 2 || f < 1 || f >= p) return 'Preliminary participants must be greater than finalists.';
        if (Math.abs(pct - 100) >= 0.01) return 'Preliminary and final percentages must total 100%.';
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
      multiple_rounds: 'Multiple Rounds',
      preliminary_final: 'Preliminary and Final Round',
    };
    var panel = participationType() === 'team' ? '#team-options' : '#candidate-options';
    var names = $$(panel + ' .participant-check:checked').map(function (box) { return box.dataset.name; });
    var judgeNames = $$('.judge-check:checked').map(function (box) { return box.dataset.name; });
    var chief = $('#chief-judge');
    var faculty = form.faculty_account;
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
      ['Round Configuration', fmt === 'single_performance' ? 'Single performance' : JSON.stringify(collectRounds())],
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
    renderCriteria();
    renderDeductions();
    renderPoints();
    renderRoundsTable();
    syncParticipantPanels();
    syncFormatPanels();
    updateJudgeCount();
    setStep(1);
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
    $$('input[name="event_format"]').forEach(function (input) {
      input.checked = input.value === event.event_format;
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
    if (event.event_format === 'multiple_rounds' && Array.isArray(event.rounds_config)) {
      $('#round-count').value = String(Math.max(2, event.rounds_config.length));
      renderRoundsTable();
      $$('#rounds-table tbody tr').forEach(function (tr, index) {
        var row = event.rounds_config[index];
        if (!row) return;
        $('[data-round-name]', tr).value = row.name || '';
        $('[data-round-weight]', tr).value = row.weight;
        $('[data-round-qualifiers]', tr).value = row.qualifiers || 0;
      });
      updateRoundsMeter();
    }
    if (event.event_format === 'preliminary_final') {
      var cfg = Array.isArray(event.rounds_config) ? (event.rounds_config[0] || {}) : (event.rounds_config || {});
      $('#prelim-participants').value = cfg.prelim_participants || 8;
      $('#finalists').value = cfg.finalists || 4;
      $('#carry-prelim').value = cfg.carry_prelim_scores ? 'yes' : 'no';
      $('#prelim-percentage').value = cfg.prelim_percentage || 40;
      $('#final-percentage').value = cfg.final_percentage || 60;
    }
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
      ['Event Classification', event.classification_label],
      ['Division', event.division],
      ['Participation Type', event.participation_label],
      ['Venue', event.venue],
      ['Event Date Range', event.start_date + ' to ' + event.end_date],
      ['Participants', (event.participant_names || []).join(', ') || '—'],
      ['Event Format', event.event_format_label],
      ['Round Configuration', JSON.stringify(event.rounds_config || [])],
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
    ];
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
  $('#round-count').addEventListener('change', renderRoundsTable);
  $('#prelim-percentage').addEventListener('input', updatePrelimMeter);
  $('#final-percentage').addEventListener('input', updatePrelimMeter);
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
