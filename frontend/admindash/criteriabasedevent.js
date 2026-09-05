(function () {
  'use strict';

  var $ = function (sel, root) { return (root || document).querySelector(sel); };
  var $$ = function (sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); };

  var form = $('#wizard-form');
  var wizard = $('#event-wizard');
  var activeStep = 1;
  var mode = 'standard';
  var criteria = [];
  var deductions = [];
  var points = [];
  var stages = [];
  var mobileApprovalRequired = true;
  var expandedRoundId = null;
  var roundDragId = null;
  var roundAdjustmentWarning = '';
  var events = [];
  var scoringWorkflow = {
    eventId: null, eventName: '', categories: [], selectedCategoryId: null,
    editingCategoryId: null, editingCriterionId: null, criteria: [],
  };

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

  function maxWizardStep() {
    return mode === 'pageant' ? 5 : 7;
  }

  function setStep(next) {
    activeStep = Math.max(1, Math.min(maxWizardStep(), Number(next) || 1));
    $$('.wizard-panel').forEach(function (panel) {
      var pageantPanel = panel.hasAttribute('data-pageant');
      var visible = mode === 'pageant'
        ? pageantPanel && Number(panel.dataset.pageantStep) === activeStep
        : !pageantPanel && Number(panel.dataset.step) === activeStep;
      panel.hidden = !visible;
      panel.classList.toggle('active', visible);
    });
    $$('[data-step-marker]').forEach(function (marker) {
      var n = Number(marker.dataset.stepMarker);
      marker.classList.toggle('active', n === activeStep);
      marker.classList.toggle('done', n < activeStep);
    });
    var activeMarker = $('[data-step-marker="' + activeStep + '"]');
    if (activeMarker) {
      $('#wizard-steps').dataset.mobileStep = 'Step ' + activeStep + ' of ' + maxWizardStep() + ' — ' + $('small', activeMarker).textContent;
      $('#wizard-steps').style.setProperty('--pageant-progress', (activeStep / maxWizardStep() * 100) + '%');
    }
    wizard.classList.toggle('at-review', activeStep === maxWizardStep());
    $('#wizard-back').style.display = activeStep === 1 ? 'none' : '';
    showError('');
    if (mode === 'pageant' && window.PageantWizard) window.PageantWizard.render(activeStep);
    if (mode === 'standard' && activeStep === 7) {
      syncCriteriaFromScoringWorkflow();
      buildReview();
    }
    if (mode === 'standard' && activeStep === 3) {
      syncEventFormatFromRounds();
      renderRoundSetup();
    }
    if (mode === 'standard' && activeStep === 4) {
      loadCategories().then(function () {
        renderScoringCategories();
        renderScoringCriteria();
      }).catch(function (error) { showError(error.message); });
    }
    if (mode === 'standard' && activeStep === 5) {
      renderJudgeAssignmentDashboard();
    }
    var body = $('.wizard-body');
    if (body) body.scrollTop = 0;
    var heading = $('.wizard-panel.active h3');
    if (heading) {
      heading.setAttribute('tabindex', '-1');
      heading.focus({ preventScroll: true });
    }
  }

  function setMode(nextMode) {
    mode = nextMode === 'pageant' ? 'pageant' : 'standard';
    wizard.classList.toggle('pageant-mode', mode === 'pageant');
    $('#wizard-steps').setAttribute('aria-label', mode === 'pageant' ? 'Pageant setup progress' : 'Creation progress');
    var labels = mode === 'pageant'
      ? ['Details', 'Candidates', 'Segments & Scoring', 'Judges, Advancement & Awards', 'Review & Publish']
      : ['Event Details', 'Participants', 'Rounds', 'Scoring Structure', 'Score Collection', 'Awards', 'Review & Publish'];
    $$('[data-step-marker]').forEach(function (marker, index) {
      var label = labels[index];
      marker.hidden = !label;
      if (label) $('small', marker).textContent = label;
    });
    activeStep = 1;
    setStep(activeStep);
  }

  function participationType() {
    return ($('#participation-type') && $('#participation-type').value) || 'group';
  }

  function isIndividualParticipation() {
    return participationType() === 'individual';
  }

  function eventFormat() {
    var picker = $('#event-format-select');
    if (picker) return picker.value || 'single_performance';
    return stages.length >= 2 ? 'multiple_stage' : 'single_performance';
  }

  function syncEventFormatFromRounds() {
    var picker = $('#event-format-select');
    var fmt = stages.length >= 2 ? 'multiple_stage' : (picker ? picker.value : 'single_performance');
    if (picker) picker.value = fmt;
    if ($('#stage-count')) $('#stage-count').value = String(stages.length);
    var lastTie = '';
    stages.forEach(function (row) {
      if (row.tie_handling) lastTie = row.tie_handling;
    });
    if (lastTie && $('#stage-tiebreak-method')) $('#stage-tiebreak-method').value = lastTie;
  }

  function syncFormatPanels() {
    if (eventFormat() === 'single_performance' && stages.length !== 1) {
      stages = defaultStages(1);
      expandedRoundId = stages[0].id;
    }
    syncEventFormatFromRounds();
    renderRoundSetup();
  }

  function participantCount() {
    return collectParticipants().length;
  }

  function participantNoun() {
    return isIndividualParticipation() ? 'participants' : 'groups';
  }

  function participantNounLower() {
    return isIndividualParticipation() ? 'participants' : 'groups';
  }

  function isFinalRoundAt(round, index, total) {
    return round.round_type === 'final' || (!!round.is_final && round.round_type !== 'elimination');
  }

  function isAdvanceAllRound(round, isFinal) {
    var method = (round.qualification_method || round.advancement_rule || '').toLowerCase();
    return !isFinal && (method === 'advance_all' || method === 'no_elimination');
  }

  function hasQualifierAdvancement(round) {
    var method = (round.qualification_method || round.advancement_rule || '').toLowerCase();
    return Number(round.qualifiers) > 0 && ['top_ranking', 'minimum_score', 'manual_selection', 'highest_total'].indexOf(method) !== -1;
  }

  function roundParticipantCount(index, list) {
    var rows = list || stages;
    var count = participantCount();
    for (var i = 0; i < index && i < rows.length; i++) {
      var round = rows[i] || {};
      var isFinal = isFinalRoundAt(round, i, rows.length);
      if (isFinal) break;
      if (isAdvanceAllRound(round, isFinal)) continue;
      var qualifiers = Number(round.qualifiers) || 0;
      if (qualifiers > 0) count = Math.min(count, qualifiers);
    }
    return count;
  }

  function qualifierWarning(from, to) {
    return 'Only ' + to + ' ' + participantNounLower() + ' are available. The qualifier count has been adjusted from ' + from + ' to ' + to + '.';
  }

  function renderRoundWarning() {
    var el = $('#round-adjustment-warning');
    if (!el) return;
    el.hidden = !roundAdjustmentWarning;
    el.textContent = roundAdjustmentWarning || '';
  }

  function roundFormIndex() {
    var editId = $('#round-edit-id').value;
    if (editId) {
      var existing = stages.findIndex(function (row) { return String(row.id) === String(editId); });
      if (existing >= 0) return existing;
    }
    var requested = Number($('#round-order').value) || (stages.length + 1);
    return Math.max(0, Math.min(stages.length, requested - 1));
  }

  function syncRoundQualifierLimits() {
    var input = $('#round-qualifiers');
    if (!input) return;
    var max = roundParticipantCount(roundFormIndex());
    input.max = String(max);
    var isFinal = formRoundEndsEvent();
    var method = ($('#round-advancement') && $('#round-advancement').value) || 'top_ranking';
    if (isFinal) return;
    if (method === 'advance_all' || $('#round-type').value === 'scoring') {
      input.value = max ? String(max) : '';
      return;
    }
    var current = Number(input.value) || 0;
    if (max > 0 && current > max) {
      roundAdjustmentWarning = qualifierWarning(current, max);
      input.value = String(max);
    } else if (max > 0 && current < 1) {
      input.value = '1';
    } else if (max < 1) {
      input.value = '';
    }
  }

  function revalidateRoundQualifiers() {
    var count = participantCount();
    var firstWarning = '';
    stages = stages.map(function (round, index) {
      var copy = Object.assign({}, round);
      var isFinal = isFinalRoundAt(copy, index, stages.length);
      if (!isFinal) {
        if (isAdvanceAllRound(copy, isFinal)) {
          copy.qualification_method = 'advance_all';
          copy.advancement_rule = 'advance_all';
          copy.qualifiers = count;
        } else {
          var configured = Number(copy.qualifiers) || 0;
          if (count > 0 && configured > count) {
            copy.qualifiers = count;
            if (!firstWarning) firstWarning = qualifierWarning(configured, count);
          }
          if (copy.qualifiers > 0) count = Math.min(count, Number(copy.qualifiers) || count);
        }
      }
      return copy;
    });
    if (firstWarning) roundAdjustmentWarning = firstWarning;
    renderRoundWarning();
  }

  function defaultStages(count) {
    count = Math.max(1, Math.min(20, Number(count) || 1));
    if (count === 1) {
      return [normalizeRound({
        name: 'Main Scoring Round',
        round_type: 'main',
        weight: 100,
        is_final: true,
        advancement_rule: 'no_advancement',
      }, 0, 1)];
    }
    var weight = Math.floor(100 / count);
    var rows = [];
    for (var i = 0; i < count; i++) {
      var isFinal = i === count - 1;
      rows.push(normalizeRound({
        name: i === 0 ? 'Preliminary Round' : (isFinal ? 'Final Round' : ('Top ' + Math.max(1, count - i) + ' Round')),
        round_type: isFinal ? 'final' : 'elimination',
        weight: isFinal ? (100 - weight * (count - 1)) : weight,
        qualification_method: isFinal ? null : 'top_ranking',
        qualifiers: isFinal ? null : Math.max(1, 10 - i * 2),
        carry_previous_scores: i > 0,
        require_faculty_confirmation: !isFinal,
        is_final: isFinal,
      }, i, count));
    }
    return rows;
  }

  function normalizeRound(row, index, total) {
    row = row || {};
    var count = total || stages.length || 1;
    var isLast = index === count - 1;
    // Preserve qualifier-based rounds while editing; simple scoring-only rounds
    // still end the event when they are the only/current last round.
    var roundType = row.round_type || (isLast ? (count > 1 ? 'final' : 'scoring') : 'elimination');
    if (roundType === 'scoring' && hasQualifierAdvancement(row)) roundType = 'elimination';
    var isFinal = roundType === 'final' || (!hasQualifierAdvancement(row) && isLast && roundType !== 'elimination');
    if (!isLast && roundType === 'final') roundType = 'elimination';
    var method = isFinal ? null : (row.qualification_method || row.advancement_rule || 'top_ranking');
    if (method === 'highest_total') method = 'top_ranking';
    var handling = row.score_handling || (row.carry_previous_scores ? 'carry' : 'start_zero');
    return {
      id: row.id || uid('s'),
      name: row.name || '',
      round_type: roundType,
      scoring_method: row.scoring_method || 'numerical',
      score_handling: handling,
      weight: Number(row.weight) || 0,
      weight_locked: handling === 'carry' && !!row.weight_locked,
      qualification_method: isFinal ? null : method,
      advancement_rule: isFinal ? null : (row.advancement_rule || method || 'top_ranking'),
      qualifiers: row.qualifiers != null ? Number(row.qualifiers) : 10,
      minimum_score: (!isFinal && method === 'minimum_score') ? (Number(row.minimum_score) || 0) : null,
      carry_previous_scores: index === 0 ? false : (row.score_handling === 'carry' || !!row.carry_previous_scores),
      require_faculty_confirmation: isFinal ? false : !!row.require_faculty_confirmation,
      require_all_judges: row.require_all_judges !== false,
      hide_standings: !!row.hide_standings,
      allow_reopen: !!row.allow_reopen,
      performance_order: row.performance_order || 'event',
      tie_handling: row.tie_handling || 'manual_decision',
      is_final: isFinal,
    };
  }

  // Final rounds do not advance anyone; qualifier-based rounds keep their controls
  // visible even while they are being edited as the current last configured round.
  function formRoundEndsEvent() {
    if ($('#round-type').value === 'final') return true;
    var editId = $('#round-edit-id').value;
    if (!editId) return false;
    var round = stages.find(function (row) { return String(row.id) === String(editId); });
    return !!(round && round.is_final && !hasQualifierAdvancement(round));
  }

  function syncRoundFormVisibility() {
    var isFinal = formRoundEndsEvent();
    var method = ($('#round-advancement') && $('#round-advancement').value) || 'top_ranking';
    var advanceAll = !isFinal && (method === 'advance_all' || $('#round-type').value === 'scoring');
    $('#round-qualifiers-wrap').hidden = isFinal || advanceAll;
    $('#round-advancement-wrap').hidden = isFinal;
    var showMin = !isFinal && method === 'minimum_score';
    $('#round-min-score-wrap').hidden = !showMin;
    var carries = $('#round-score-handling').value === 'carry';
    $('#round-weight-wrap').hidden = !carries;
    $('#round-weight').disabled = !carries;
    $('#round-weight-auto-hint').hidden = carries;
    $('#round-weight-manual-hint').hidden = !carries;
    syncRoundQualifierLimits();
  }

  // Rounds without a weight of their own -- those that start from zero, plus any
  // carry-forward round left blank -- split whatever weight the rest leave behind.
  // The flag matters because auto shares are written back into row.weight, so the
  // number alone cannot tell an auto share apart from one the user typed.
  function hasOwnRoundWeight(row) {
    return !!row.weight_locked && (Number(row.weight) || 0) > 0;
  }

  function applyAutoRoundWeights() {
    var auto = stages.filter(function (row) { return !hasOwnRoundWeight(row); });
    if (!auto.length) return;
    var manual = stages.reduce(function (sum, row) {
      return hasOwnRoundWeight(row) ? sum + (Number(row.weight) || 0) : sum;
    }, 0);
    var remaining = Math.max(0, round2(100 - manual));
    var share = round2(remaining / auto.length);
    auto.forEach(function (row, index) {
      row.weight = index === auto.length - 1
        ? round2(remaining - share * (auto.length - 1))
        : share;
    });
  }

  function resetRoundForm(preset) {
    $('#round-edit-id').value = '';
    $('#round-form-title').textContent = 'Add Round';
    $('#save-round-btn').textContent = '+ Add Round';
    $('#cancel-round-edit').hidden = true;
    $('#round-name').value = (preset && preset.name) || '';
    $('#round-type').value = (preset && preset.round_type) || (stages.length ? 'elimination' : 'scoring');
    $('#round-order').value = String(stages.length + 1);
    $('#round-scoring-method').value = 'numerical';
    $('#round-score-handling').value = stages.length ? 'start_zero' : 'start_zero';
    $('#round-weight').value = '';
    var available = roundParticipantCount(stages.length);
    $('#round-qualifiers').value = available ? String(Math.min(10, available)) : '';
    $('#round-advancement').value = 'top_ranking';
    $('#round-min-score').value = '';
    $('#round-performance-order').value = 'event';
    $('#round-tie-handling').value = 'manual_decision';
    $('#round-require-judges').checked = true;
    $('#round-require-faculty').checked = !!stages.length;
    $('#round-hide-standings').checked = false;
    $('#round-allow-reopen').checked = false;
    syncRoundFormVisibility();
    renderRoundWarning();
  }

  function fillRoundForm(round) {
    $('#round-edit-id').value = round.id;
    $('#round-form-title').textContent = 'Edit Round';
    $('#save-round-btn').textContent = 'Save Round';
    $('#cancel-round-edit').hidden = false;
    $('#round-name').value = round.name || '';
    $('#round-type').value = hasQualifierAdvancement(round)
      ? 'elimination'
      : (round.round_type || (round.is_final ? 'final' : 'elimination'));
    var index = stages.findIndex(function (row) { return row.id === round.id; });
    $('#round-order').value = String(index >= 0 ? index + 1 : stages.length + 1);
    $('#round-scoring-method').value = round.scoring_method || 'numerical';
    $('#round-score-handling').value = round.score_handling || (round.carry_previous_scores ? 'carry' : 'start_zero');
    $('#round-weight').value = round.weight_locked ? String(Number(round.weight) || 0) : '';
    $('#round-qualifiers').value = String(round.qualifiers || 1);
    $('#round-advancement').value = round.qualification_method || round.advancement_rule || 'top_ranking';
    $('#round-min-score').value = round.minimum_score != null ? round.minimum_score : '';
    $('#round-performance-order').value = round.performance_order || 'event';
    $('#round-tie-handling').value = round.tie_handling || 'manual_decision';
    $('#round-require-judges').checked = round.require_all_judges !== false;
    $('#round-require-faculty').checked = !!round.require_faculty_confirmation;
    $('#round-hide-standings').checked = !!round.hide_standings;
    $('#round-allow-reopen').checked = !!round.allow_reopen;
    syncRoundFormVisibility();
    renderRoundWarning();
  }

  function readRoundForm() {
    var editId = $('#round-edit-id').value;
    var roundType = $('#round-type').value;
    var isFinal = formRoundEndsEvent();
    var method = isFinal ? null : ($('#round-advancement').value || 'top_ranking');
    var carries = $('#round-score-handling').value === 'carry';
    var typedWeight = carries ? Number(($('#round-weight').value || '').trim()) : 0;
    var locked = carries && typedWeight > 0;
    var draft = normalizeRound({
      id: editId || uid('s'),
      name: ($('#round-name').value || '').trim(),
      round_type: roundType,
      scoring_method: $('#round-scoring-method').value,
      score_handling: $('#round-score-handling').value,
      weight: locked ? typedWeight : 0,
      weight_locked: locked,
      qualification_method: method,
      advancement_rule: method,
      qualifiers: isFinal ? null : (Number($('#round-qualifiers').value) || 0),
      minimum_score: (!isFinal && method === 'minimum_score') ? (Number($('#round-min-score').value) || 0) : null,
      carry_previous_scores: $('#round-score-handling').value === 'carry',
      require_faculty_confirmation: $('#round-require-faculty').checked,
      require_all_judges: $('#round-require-judges').checked,
      hide_standings: $('#round-hide-standings').checked,
      allow_reopen: $('#round-allow-reopen').checked,
      performance_order: $('#round-performance-order').value,
      tie_handling: $('#round-tie-handling').value,
      is_final: isFinal,
    }, 0, 2); // never treat the draft as the last round; renderRoundSetup re-normalizes by real position
    draft.round_type = roundType;
    draft.is_final = isFinal;
    if (!isFinal && isAdvanceAllRound(draft, isFinal)) {
      draft.qualification_method = 'advance_all';
      draft.advancement_rule = 'advance_all';
      draft.qualifiers = roundParticipantCount(roundFormIndex());
    }
    return draft;
  }

  function validateRoundDraft(draft, exceptId) {
    if (!draft.name) return 'Round Name is required.';
    if (/^round\s*\d+$/i.test(draft.name)) return 'Use a descriptive name instead of generic Round labels.';
    var clash = stages.some(function (row) {
      return String(row.id) !== String(exceptId || '') && (row.name || '').trim().toLowerCase() === draft.name.toLowerCase();
    });
    if (clash) return 'Each round name must be unique.';
    if (!draft.is_final && draft.round_type !== 'final') {
      var count = roundParticipantCount(roundFormIndex());
      if (count < 1) {
        return participationType() === 'individual' ? 'Select at least one candidate.' : 'Select at least one participant or team.';
      }
      if (isAdvanceAllRound(draft, false)) {
        draft.qualifiers = count;
      } else {
        if (!Number.isInteger(draft.qualifiers) || draft.qualifiers < 1) {
          return 'Number of Qualifiers must be a whole number of at least 1.';
        }
        if (draft.qualifiers > count) {
          return 'Number of Qualifiers cannot exceed ' + count + ' eligible participants.';
        }
      }
    }
    return '';
  }

  function applyRoundOrder() {
    var requested = Number($('#round-order').value) || (stages.length + 1);
    var editId = $('#round-edit-id').value;
    var index = stages.findIndex(function (row) { return String(row.id) === String(editId); });
    if (index < 0) return;
    var next = Math.max(0, Math.min(stages.length - 1, requested - 1));
    if (next === index) return;
    var copy = stages.slice();
    var item = copy.splice(index, 1)[0];
    copy.splice(next, 0, item);
    stages = copy;
  }

  function saveRoundFromForm() {
    var draft = readRoundForm();
    var error = validateRoundDraft(draft, $('#round-edit-id').value);
    if (error) {
      showError(error);
      return;
    }
    var editId = $('#round-edit-id').value;
    var requested = Math.max(1, Number($('#round-order').value) || (stages.length + 1));
    if (editId) {
      stages = stages.map(function (row) {
        return String(row.id) === String(editId) ? Object.assign({}, row, draft, { id: editId }) : row;
      });
      var index = stages.findIndex(function (row) { return String(row.id) === String(editId); });
      if (index >= 0) {
        var next = Math.max(0, Math.min(stages.length - 1, requested - 1));
        if (next !== index) {
          var copy = stages.slice();
          var item = copy.splice(index, 1)[0];
          copy.splice(next, 0, item);
          stages = copy;
        }
      }
    } else {
      var insertAt = Math.max(0, Math.min(stages.length, requested - 1));
      stages.splice(insertAt, 0, draft);
    }
    expandedRoundId = draft.id;
    showError('');
    resetRoundForm();
    renderRoundSetup();
  }

  function duplicateRound(id) {
    var source = stages.find(function (row) { return String(row.id) === String(id); });
    if (!source) return;
    var copy = normalizeRound(Object.assign({}, source, {
      id: uid('s'),
      name: (source.name || 'Round') + ' copy',
    }), stages.length, stages.length + 1);
    stages.push(copy);
    expandedRoundId = copy.id;
    renderRoundSetup();
  }

  function deleteRound(id) {
    stages = stages.filter(function (row) { return String(row.id) !== String(id); });
    if (expandedRoundId && String(expandedRoundId) === String(id)) expandedRoundId = null;
    if ($('#round-edit-id').value === String(id)) resetRoundForm();
    renderRoundSetup();
  }

  function renderRoundSummary() {
    var host = $('#round-setup-summary');
    if (!host) return;
    var name = (form.event_name && form.event_name.value.trim()) || 'Untitled event';
    var typeLabel = form.category && form.category.selectedIndex >= 0
      ? form.category.options[form.category.selectedIndex].text
      : 'Criteria-Based';
    if (typeLabel === 'Select category type') typeLabel = 'Criteria-Based';
    var status = ($('#publication-status') && $('#publication-status').value) || 'draft';
    var count = participantCount();
    host.innerHTML =
      '<strong>' + escapeHtml(name) + '</strong>' +
      '<span>' + escapeHtml(typeLabel) + '</span>' +
      '<span>' + (isIndividualParticipation() ? 'Individual' : 'Group') + '</span>' +
      '<span>' + count + ' ' + participantNoun() + '</span>' +
      '<em class="rs-status rs-status--' + escapeAttr(status) + '">' + (status === 'published' ? 'Published' : 'Draft') + '</em>';
  }

  function roundTypeLabel(type) {
    if (type === 'final') return 'Final';
    if (type === 'scoring') return 'Scoring';
    return 'Elimination';
  }

  function roundMetaLine(round, index) {
    var bits = [];
    bits.push(round.scoring_method === 'ranking' ? 'Ranking' : 'Numerical Scoring');
    bits.push(round.score_handling === 'carry' ? 'Carry previous scores' : 'Start from zero');
    if (round.require_faculty_confirmation) bits.push('Faculty confirmation required');
    var isFinal = isFinalRoundAt(round, index, stages.length);
    var available = roundParticipantCount(index);
    if (isFinal) bits.push('No advancement');
    else if (isAdvanceAllRound(round, isFinal)) bits.push('Advance all ' + available);
    else if (round.qualifiers) bits.push('Top ' + Math.min(Number(round.qualifiers) || 0, available) + ' advance');
    return bits.join(' · ');
  }

  function renderConfiguredRounds() {
    var host = $('#configured-rounds-list');
    if (!host) return;
    if (!stages.length) {
      host.innerHTML = '<p class="pageant-empty-state">No rounds yet. Use Add Round above, or create one Main Scoring round for simple events.</p>';
      return;
    }
    host.innerHTML = stages.map(function (round, index) {
      var expanded = String(expandedRoundId || stages[0].id) === String(round.id);
      var isFinal = isFinalRoundAt(round, index, stages.length);
      var available = roundParticipantCount(index);
      var qualBadge = isFinal
        ? 'No advancement'
        : (isAdvanceAllRound(round, isFinal)
          ? 'Advance All ' + available
          : ('Top ' + Math.min(Number(round.qualifiers) || 0, available) + ' Advance'));
      return (
        '<article class="rs-round' + (expanded ? ' is-open' : '') + '" data-round-id="' + escapeAttr(round.id) + '" draggable="true">' +
          '<div class="rs-round-row">' +
            '<button type="button" class="rs-drag" title="Drag to reorder" aria-label="Reorder">⠿</button>' +
            '<button type="button" class="rs-round-main" data-toggle-round="' + escapeAttr(round.id) + '">' +
              '<span class="rs-round-num">' + (index < 9 ? '0' : '') + (index + 1) + '</span>' +
              '<span class="rs-round-copy">' +
                '<strong>' + escapeHtml(round.name || 'Untitled round') + '</strong>' +
                '<span class="rs-badges">' +
                  '<em>' + escapeHtml(roundTypeLabel(round.round_type)) + '</em>' +
                  '<em>' + available + ' ' + participantNoun() + '</em>' +
                  '<em>' + escapeHtml(qualBadge) + '</em>' +
                '</span>' +
              '</span>' +
              '<span class="rs-configured">Configured</span>' +
            '</button>' +
            '<span class="rs-round-actions">' +
              '<button type="button" class="ss-link-btn" data-edit-round="' + escapeAttr(round.id) + '">Edit</button>' +
              '<button type="button" class="ss-link-btn" data-duplicate-round="' + escapeAttr(round.id) + '">Duplicate</button>' +
              '<button type="button" class="ss-link-btn danger" data-delete-round="' + escapeAttr(round.id) + '">Delete</button>' +
            '</span>' +
          '</div>' +
          (expanded ? '<p class="rs-round-details">' + escapeHtml(roundMetaLine(round, index)) + '</p>' : '') +
        '</article>'
      );
    }).join('');
  }

  function updateStagesMeter() {
    var meter = $('#stages-weight-meter');
    if (!meter) return;
    var total = stages.reduce(function (sum, row) { return sum + (Number(row.weight) || 0); }, 0);
    meter.textContent = 'Round total: ' + round2(total) + '%';
    meter.classList.toggle('is-ok', Math.abs(total - 100) < 0.01);
    meter.classList.toggle('is-warn', Math.abs(total - 100) >= 0.01);
  }

  function renderRoundSetup() {
    stages = stages.map(function (row, index) {
      return normalizeRound(row, index, stages.length);
    });
    revalidateRoundQualifiers();
    applyAutoRoundWeights();
    syncEventFormatFromRounds();
    renderRoundSummary();
    renderConfiguredRounds();
    updateStagesMeter();
    syncRoundQualifierLimits();
    renderRoundWarning();
    if (!$('#round-edit-id').value) $('#round-order').value = String(stages.length + 1);
  }

  function renderStagesTable() {
    renderRoundSetup();
  }

  function moveStage(id, delta) {
    var index = stages.findIndex(function (row) { return String(row.id) === String(id); });
    var next = index + delta;
    if (index < 0 || next < 0 || next >= stages.length) return;
    var copy = stages.slice();
    var tmp = copy[index];
    copy[index] = copy[next];
    copy[next] = tmp;
    stages = copy;
    renderRoundSetup();
  }

  function syncParticipantPanels() {
    var kind = participationType();
    $('#team-options').classList.toggle('criteria-hidden', kind === 'individual');
    $('#candidate-options').classList.toggle('criteria-hidden', kind !== 'individual');
    var legend = $('#participant-legend-label');
    if (legend) {
      legend.textContent = kind === 'individual' ? 'Participating Individuals' : 'Participating Groups';
    }
    $$('#team-options .participant-check').forEach(function (box) {
      if (kind === 'individual') box.checked = false;
    });
    $$('#candidate-options .participant-check').forEach(function (box) {
      if (kind !== 'individual') box.checked = false;
    });
    updateParticipantCount();
    refreshParticipantFilters();
  }

  function visibleParticipantBoxes() {
    var panel = isIndividualParticipation() ? '#candidate-options' : '#team-options';
    return $$(panel + ' .participant-check').filter(function (box) {
      var label = box.closest('label');
      return !label || !label.hidden;
    });
  }

  function setAllVisibleParticipants(checked) {
    visibleParticipantBoxes().forEach(function (box) {
      box.checked = !!checked;
    });
    updateParticipantCount();
    renderRoundSetup();
    showError('');
  }

  function refreshParticipantFilters() {
    var panel = isIndividualParticipation() ? '#candidate-options' : '#team-options';
    var deptFilter = $('#participant-department-filter');
    var courseFilter = $('#participant-course-filter');
    var departments = {};
    var courses = {};
    $$(panel + ' label[data-search]').forEach(function (label) {
      var dept = label.dataset.department || '';
      var course = label.dataset.course || '';
      if (dept) departments[dept] = true;
      if (course) courses[course] = true;
    });
    if (deptFilter) {
      var current = deptFilter.value;
      deptFilter.innerHTML = '<option value="">All departments</option>' +
        Object.keys(departments).sort().map(function (name) {
          return '<option value="' + escapeAttr(name) + '">' + escapeHtml(name) + '</option>';
        }).join('');
      deptFilter.value = departments[current] ? current : '';
    }
    if (courseFilter) {
      var selected = courseFilter.value;
      courseFilter.innerHTML = '<option value="">All courses/programs</option>' +
        Object.keys(courses).sort().map(function (name) {
          return '<option value="' + escapeAttr(name) + '">' + escapeHtml(name) + '</option>';
        }).join('');
      courseFilter.value = courses[selected] ? selected : '';
      courseFilter.disabled = !Object.keys(courses).length;
    }
    filterParticipants();
  }

  function filterParticipants() {
    var q = ($('#participant-search') || {}).value || '';
    q = q.trim().toLowerCase();
    var dept = ($('#participant-department-filter') || {}).value || '';
    var course = ($('#participant-course-filter') || {}).value || '';
    var panel = isIndividualParticipation() ? '#candidate-options' : '#team-options';
    $$(panel + ' label[data-search]').forEach(function (label) {
      var textMatch = !q || (label.dataset.search || '').indexOf(q) !== -1;
      var deptMatch = !dept || (label.dataset.department || '') === dept;
      var courseMatch = !course || (label.dataset.course || '') === course;
      label.hidden = !(textMatch && deptMatch && courseMatch);
    });
  }

  function updateParticipantCount() {
    var panel = isIndividualParticipation() ? '#candidate-options' : '#team-options';
    var count = $$(panel + ' .participant-check:checked').length;
    $('#selected-participant-count').textContent = count + ' participants selected';
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
      div.className = 'points-row';
      div.innerHTML =
        '<input data-p-label value="' + escapeAttr(row.label) + '" aria-label="Rank label" placeholder="Rank label">' +
        '<input type="number" min="0" data-p-points value="' + escapeAttr(row.points) + '" aria-label="Points">' +
        '<div class="points-row-actions">' +
          '<button type="button" class="points-btn points-btn-up" data-p-up ' + (index === 0 ? 'disabled' : '') + ' aria-label="Move rank up">Up</button>' +
          '<button type="button" class="points-btn points-btn-down" data-p-down ' + (index === points.length - 1 ? 'disabled' : '') + ' aria-label="Move rank down">Down</button>' +
          '<button type="button" class="points-btn points-btn-del" data-p-del aria-label="Delete rank">Delete</button>' +
        '</div>';
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
    var panel = isIndividualParticipation() ? '#candidate-options' : '#team-options';
    return $$(panel + ' .participant-check:checked').map(function (box) {
      return Number(box.value);
    });
  }

  function collectRounds() {
    revalidateRoundQualifiers();
    syncEventFormatFromRounds();
    return stages.map(function (stage, index) {
      var isFinal = isFinalRoundAt(stage, index, stages.length);
      var advanceAll = isAdvanceAllRound(stage, isFinal);
      var available = roundParticipantCount(index);
      var qualifiers = isFinal ? null : (advanceAll ? available : Math.min(Number(stage.qualifiers) || 0, available));
      return {
        stage_number: index + 1,
        id: stage.id,
        name: stage.name,
        weight: Number(stage.weight) || 0,
        weight_locked: !!stage.weight_locked,
        qualification_method: isFinal ? null : (advanceAll ? 'advance_all' : (stage.qualification_method || 'top_ranking')),
        qualifiers: qualifiers,
        minimum_score: (!isFinal && !advanceAll && stage.qualification_method === 'minimum_score')
          ? (Number(stage.minimum_score) || 0)
          : null,
        carry_previous_scores: index === 0 ? false : !!stage.carry_previous_scores,
        require_faculty_confirmation: isFinal ? false : !!stage.require_faculty_confirmation,
        require_all_judges: stage.require_all_judges !== false,
        hide_standings: !!stage.hide_standings,
        allow_reopen: !!stage.allow_reopen,
        scoring_method: stage.scoring_method || 'numerical',
        score_handling: stage.score_handling || 'start_zero',
        round_type: stage.round_type || (isFinal ? 'final' : 'elimination'),
        advancement_rule: isFinal ? null : (advanceAll ? 'advance_all' : (stage.advancement_rule || stage.qualification_method || 'top_ranking')),
        performance_order: stage.performance_order || 'event',
        tie_handling: stage.tie_handling || 'manual_decision',
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
    out.final_result_basis = $('#final-result-basis').value;
    out.placements_count = Number($('#placements-count').value);
    $$('[data-rp]').forEach(function (input) {
      out[input.dataset.rp] = input.checked;
    });
    if (eventFormat() === 'multiple_stage') {
      var tieEl = $('#stage-tiebreak-method');
      out.stage_tiebreak_method = (tieEl && tieEl.value) || 'manual_decision';
    }
    return out;
  }

  function collectJudgeSettings() {
    var out = {};
    out.allowed_submission_methods = [];
    if ($('#method-mobile').checked || $('#method-hybrid').checked) out.allowed_submission_methods.push('MOBILE');
    if ($('#method-ocr').checked || $('#method-hybrid').checked) out.allowed_submission_methods.push('OCR');
    out.default_submission_method = $('#default-submission-method').value;
    out.mobile_approval_required = mobileApprovalRequired;
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
    renderJudgeAssignmentDashboard();
  }

  function renderJudgeAssignmentDashboard() {
    var host = $('#judge-assignment-dashboard');
    if (!host) return;
    var judges = $$('.judge-check:checked');
    host.innerHTML = judges.map(function (box) {
      return '<p><strong>' + escapeHtml(box.dataset.name) + '</strong> - All rounds</p>';
    }).join('') || '<p>No judges selected.</p>';
  }

  function updateOfficialCategoryTotal() {
    var key = $('#scoring-category-round').value;
    var total = officialWeightTotalForRound(key);
    $('#official-category-total').textContent = 'Official categories: ' + total + '% of 100% configured.';
  }

  function categoryOverallWeightTotal() {
    return scoringWorkflow.categories.filter(function (row) {
      return (row.purpose || 'official') === 'official';
    }).reduce(function (sum, row) {
      return sum + (Number(row.overall_weight_percent) || 0);
    }, 0);
  }

  function roundKeyFor(row, index) {
    return String((row && (row.id || row.stage_number || row.name)) || (index + 1));
  }

  function categoryRoundKey(category) {
    if (category && category.assigned_round_id) return String(category.assigned_round_id);
    return stages.length ? roundKeyFor(stages[0], 0) : 'main';
  }

  function officialWeightTotalForRound(roundKey) {
    return roundWeight(scoringWorkflow.categories.reduce(function (sum, row) {
      if ((row.purpose || 'official') !== 'official') return sum;
      if (categoryRoundKey(row) !== String(roundKey)) return sum;
      return sum + (Number(row.overall_weight_percent) || 0);
    }, 0));
  }

  function roundWeight(value) {
    return Math.round((Number(value) || 0) * 100) / 100;
  }

  function validateCategoryOverallWeights() {
    var roundIds = (stages.length ? stages : [{ id: 'main', name: 'Main Scoring Round' }]).map(roundKeyFor);
    for (var i = 0; i < roundIds.length; i++) {
      var total = officialWeightTotalForRound(roundIds[i]);
      if (total < 100) return 'Official categories in this round must total 100%. Current total: ' + total + '%.';
      if (total > 100) return 'Official categories in this round exceed 100% by ' + roundWeight(total - 100) + '%.';
    }
    return '';
  }

  function categoryTargetWeight(category, categoryCount, eventWeightTotal) {
    return Number(category.overall_weight_percent) || 0;
  }

  function criteriaWeightTotal(categoryRow) {
    return roundWeight((categoryRow.criteria || []).reduce(function (sum, row) {
      return sum + (Number(row.weight_percent) || 0);
    }, 0));
  }

  function validateCriteriaWeightsForCategory(categoryRow) {
    if (!categoryRow) return '';
    var rows = categoryRow.criteria || [];
    if (!rows.length) {
      return 'Add criteria for "' + categoryRow.name + '" before continuing.';
    }
    // Ranking Mode keeps existing ranking logic; weight totals are for Scoring Mode.
    if (categoryRow.judge_mode === 'ranking') return '';

    for (var i = 0; i < rows.length; i++) {
      var raw = rows[i].weight_percent;
      if (raw === '' || raw == null || Number.isNaN(Number(raw))) {
        return 'Criterion "' + (rows[i].name || 'Untitled') + '" in "' + categoryRow.name + '" needs a valid Criterion Weight Within Category.';
      }
      if (Number(raw) < 0) {
        return 'Criterion weights cannot be negative in "' + categoryRow.name + '".';
      }
    }

    var categoryWeight = 100;
    var weightTotal = criteriaWeightTotal(categoryRow);

    if (Math.abs(weightTotal - categoryWeight) < 0.01) return '';
    if (weightTotal > categoryWeight) {
      return (
        'Criteria weights for "' + categoryRow.name + '" exceed the category weight by ' +
        roundWeight(weightTotal - categoryWeight) + '%.'
      );
    }
    return (
      'Criteria weights for "' + categoryRow.name + '" must total ' + categoryWeight +
      '% of category. Current total: ' + weightTotal + '%.'
    );
  }

  function absoluteCriterionEventWeight(criterion, criteriaCount, localSum, categoryWeight) {
    var local = Number(criterion.weight_percent) || 0;
    if (Math.abs(localSum) < 0.01) return 0;
    return roundWeight((local / localSum) * categoryWeight);
  }

  function syncCriteriaFromScoringWorkflow() {
    // Adapt category-local weights for the legacy flat mobile payload.
    var flat = [];
    var cats = scoringWorkflow.categories;
    var eventWeightTotal = categoryOverallWeightTotal();
    cats.filter(function (category) {
      return (category.purpose || 'official') === 'official';
    }).forEach(function (category) {
      var categoryWeight = categoryTargetWeight(category, cats.length, eventWeightTotal);
      var rows = category.criteria || [];
      var localSum = criteriaWeightTotal(category);
      rows.forEach(function (criterion) {
        flat.push({
          id: 'esc_' + criterion.id,
          name: criterion.name || '',
          description: category.name || '',
          weight: absoluteCriterionEventWeight(criterion, rows.length, localSum, categoryWeight),
          max_score: criterion.max_score != null ? Number(criterion.max_score) : 100,
        });
      });
    });
    if (flat.length) {
      var total = roundWeight(flat.reduce(function (sum, row) { return sum + Number(row.weight); }, 0));
      var drift = roundWeight(100 - total);
      if (Math.abs(drift) <= 0.15 && Math.abs(drift) > 0.001) {
        flat[flat.length - 1].weight = roundWeight(flat[flat.length - 1].weight + drift);
      }
      criteria = flat;
    }
  }

  function updateCriteriaWeightMeter(category, criteriaRows) {
    var meter = $('#scoring-criterion-total');
    if (!meter) return;
    var eventWeightTotal = categoryOverallWeightTotal();
    var categoryWeight = category ? 100 : 0;
    var total = roundWeight((criteriaRows || []).reduce(function (sum, row) {
      return sum + (Number(row.weight_percent) || 0);
    }, 0));
    meter.classList.remove('is-ok', 'is-bad');
    if (!category) {
      meter.textContent = 'Category criteria total: 0% / 0%';
      return;
    }
    if (Math.abs(total - categoryWeight) < 0.01) {
      meter.classList.add('is-ok');
      meter.textContent = '✓ Category criteria total: ' + total + '% / ' + categoryWeight + '%';
      return;
    }
    if (total > categoryWeight) {
      meter.classList.add('is-bad');
      meter.textContent = (
        'Category criteria total: ' + total + '% / ' + categoryWeight + '%\n' +
        roundWeight(total - categoryWeight) + '% over the allowed category weight'
      );
      return;
    }
    meter.classList.add('is-bad');
    meter.textContent = (
      'Category criteria total: ' + total + '% / ' + categoryWeight + '%\n' +
      roundWeight(categoryWeight - total) + '% remaining'
    );
  }

  function syncHiddenFields() {
    syncCriteriaFromScoringWorkflow();
    if (!criteria.length) syncCriteriaFromDom();
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
      if (!form.category.value) return 'Category Name is required. Select a category type.';
      if (!form.event_classification.value) return 'Event Classification is required.';
      if (!form.venue.value.trim()) return 'Venue is required.';
      if (!form.start_date.value || !form.end_date.value) return 'Start Date and End Date are required.';
      if (form.end_date.value < form.start_date.value) return 'End Date cannot precede Start Date.';
    }
    if (current === 2) {
      if (collectParticipants().length < 1) {
        return participationType() === 'individual' ? 'Select at least one candidate.' : 'Select at least one participant or team.';
      }
    }
    if (current === 3) {
      revalidateRoundQualifiers();
      if (!stages.length) return 'Add at least one round. For simple events, create one Main Scoring round.';
      var names = {};
      for (var i = 0; i < stages.length; i++) {
        var stage = stages[i];
        var nameKey = (stage.name || '').trim().toLowerCase();
        if (!nameKey) return 'Each round needs a name.';
        if (/^round\s*\d+$/i.test(stage.name.trim())) {
          return 'Use a descriptive round name instead of generic Round labels.';
        }
        if (names[nameKey]) return 'Each round name must be unique.';
        names[nameKey] = true;
        var isFinal = isFinalRoundAt(stage, i, stages.length);
        if (!isFinal) {
          var stageCount = roundParticipantCount(i);
          if (stageCount < 1) {
            return participationType() === 'individual' ? 'Select at least one candidate.' : 'Select at least one participant or team.';
          }
          if (isAdvanceAllRound(stage, isFinal)) continue;
          var quals = Number(stage.qualifiers) || 0;
          if (quals < 1) return 'Each elimination round needs at least one qualifier.';
        }
      }
      if (stages.length === 1) {
        stages[0].weight = 100;
        stages[0].is_final = true;
        stages[0].round_type = stages[0].round_type || 'scoring';
      } else {
        var total = stages.reduce(function (sum, row) { return sum + (Number(row.weight) || 0); }, 0);
        if (Math.abs(total - 100) >= 0.01) return 'Total round weight must equal 100%. Currently ' + round2(total) + '%.';
        for (var w = 0; w < stages.length; w++) {
          if ((Number(stages[w].weight) || 0) <= 0) return 'Every round weight must be greater than zero.';
        }
      }
      syncEventFormatFromRounds();
    }
    if (current === 4) {
      if (!scoringWorkflow.eventId) return 'Complete Step 1 so categories can be saved to this event.';
      if (!scoringWorkflow.categories.length) return 'Save at least one scoring category.';
      var categoryWeightError = validateCategoryOverallWeights();
      if (categoryWeightError) return categoryWeightError;
    }
    if (current === 4) {
      if (!scoringWorkflow.categories.length) return 'Save at least one scoring category first.';
      var emptyCategory = scoringWorkflow.categories.filter(function (category) {
        return !(category.criteria && category.criteria.length);
      })[0];
      if (emptyCategory) {
        return 'Add criteria for "' + emptyCategory.name + '" before continuing.';
      }
      var categoryWeightErrorStep4 = validateCategoryOverallWeights();
      if (categoryWeightErrorStep4) return categoryWeightErrorStep4;
      for (var c = 0; c < scoringWorkflow.categories.length; c++) {
        var criteriaWeightError = validateCriteriaWeightsForCategory(scoringWorkflow.categories[c]);
        if (criteriaWeightError) return criteriaWeightError;
      }
      syncCriteriaFromScoringWorkflow();
      var flatTotal = roundWeight(criteria.reduce(function (sum, row) { return sum + (Number(row.weight) || 0); }, 0));
      if (criteria.length && Math.abs(flatTotal - 100) >= 0.01 && Math.abs(categoryOverallWeightTotal()) >= 0.01) {
        return 'All criterion event weights must total 100%. Current total: ' + flatTotal + '%.';
      }
    }
    if (current === 5) {
      if (!$('#chief-judge').value) return 'Chief Judge is required.';
      if (!form.faculty_account.value) return 'Tabulator in Charge is required.';
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
    var panel = isIndividualParticipation() ? '#candidate-options' : '#team-options';
    var names = $$(panel + ' .participant-check:checked').map(function (box) { return box.dataset.name; });
    var judgeNames = $$('.judge-check:checked').map(function (box) { return box.dataset.name; });
    var chief = $('#chief-judge');
    var faculty = form.faculty_account;
    var categories = scoringWorkflow.categories.map(function (category) {
      var criteriaSummary = (category.criteria || []).map(function (criterion) {
        return criterion.name + ' (' + criterion.weight_percent + '% of event)';
      }).join(', ') || 'No criteria saved';
      var criteriaTotal = criteriaWeightTotal(category);
      return category.name + ' · ' + (category.judge_mode === 'ranking' ? 'Ranking Mode' : 'Scoring Mode') +
        ' · Category ' + category.overall_weight_percent + '% · Criteria ' + criteriaTotal + '%' +
        ' · ' + criteriaSummary;
    }).join(' | ') || '—';
    var rows = [
      ['Event Name', form.event_name.value],
      ['Category Name', form.category.options[form.category.selectedIndex] ? form.category.options[form.category.selectedIndex].text : '—'],
      ['Event Classification', form.event_classification.options[form.event_classification.selectedIndex].text],
      ['Participation Type', isIndividualParticipation() ? 'Individual' : 'Group'],
      ['Venue', form.venue.value],
      ['Date & Time', form.start_date.value + (($('#event-time') || {}).value ? ' · ' + $('#event-time').value : '')],
      ['Participants', names.join(', ') || '—'],
      ['Rounds', stages.map(function (row, index) {
        return (index + 1) + '. ' + (row.name || 'Untitled') + ' (' + (row.weight || 0) + '%)';
      }).join(' · ') || '—'],
      ['Scoring Categories & Criteria', categories],
      ['Chief Judge', chief.options[chief.selectedIndex] ? chief.options[chief.selectedIndex].text : '—'],
      ['Assigned Judges', judgeNames.join(', ') || '—'],
      ['Tabulator in Charge', faculty.options[faculty.selectedIndex] ? faculty.options[faculty.selectedIndex].text : '—'],
    ];
    $('#review-summary').innerHTML = rows.map(function (row) {
      return '<div><span>' + escapeHtml(row[0]) + '</span><strong>' + escapeHtml(row[1]) + '</strong></div>';
    }).join('');
  }

  function resetScoringWorkflow() {
    scoringWorkflow = {
      eventId: null, eventName: '', categories: [], selectedCategoryId: null,
      editingCategoryId: null, editingCriterionId: null, criteria: [],
    };
    var categoryList = $('#scoring-category-list');
    if (categoryList) categoryList.innerHTML = '<p class="pageant-empty-state">No categories saved yet.</p>';
    var criterionList = $('#scoring-criterion-list');
    if (criterionList) criterionList.innerHTML = '<p class="pageant-empty-state">Save and select a category first.</p>';
    var categoryPicker = $('#scoring-criterion-category');
    if (categoryPicker) categoryPicker.innerHTML = '<option value="">Select category</option>';
  }

  function hydrateScoringWorkflow(event) {
    scoringWorkflow.eventId = event.id;
    scoringWorkflow.eventName = event.name || '';
    scoringWorkflow.editingCategoryId = null;
    scoringWorkflow.editingCriterionId = null;
    scoringWorkflow.categories = (event.scoring_categories || []).map(function (category) {
      return {
        id: category.id,
        name: category.name,
        assigned_round_id: category.assigned_round_id || '',
        purpose: category.purpose || 'official',
        category_purpose: category.category_purpose || '',
        judge_mode: category.judge_mode,
        display_order: category.display_order,
        overall_weight_percent: category.overall_weight_percent,
        criteria: (category.criteria || []).slice(),
      };
    });
    scoringWorkflow.selectedCategoryId = scoringWorkflow.categories.length
      ? scoringWorkflow.categories[0].id
      : null;
    scoringWorkflow.criteria = scoringWorkflow.categories.length
      ? (scoringWorkflow.categories[0].criteria || []).slice()
      : [];
    syncCriteriaFromScoringWorkflow();
    renderScoringCategories();
  }

  function currentEventId() {
    if (scoringWorkflow.eventId) return scoringWorkflow.eventId;
    var match = String(form.action || '').match(/\/criteria\/(\d+)\/edit\/?/);
    return match ? match[1] : '';
  }

  function resetWizard() {
    mobileApprovalRequired = true;
    form.reset();
    form.action = '/admin/events/criteria/';
    $('#publication-status').value = 'draft';
    $('#wizard-title').textContent = 'Create criteria event';
    criteria = defaultCriteria();
    deductions = [];
    points = defaultPoints('major');
    stages = [];
    resetScoringWorkflow();
    if ($('#stage-count')) $('#stage-count').value = '0';
    $('#stage-tiebreak-method').value = 'manual_decision';
    renderCriteria();
    renderDeductions();
    renderPoints();
    resetRoundForm();
    renderRoundSetup();
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
          // Carrying keeps the saved split; the preliminary round then takes the rest.
          score_handling: 'carry',
          weight_locked: true,
          require_faculty_confirmation: false,
          is_final: true,
        },
      ];
    }
    if (Array.isArray(config) && config.length) {
      return config.map(function (row, index) {
        // Events saved before weights could be auto-shared have no flag, so keep
        // their stored split rather than redistributing it on load.
        var locked = row.weight_locked != null
          ? !!row.weight_locked
          : (config.length > 1 && (Number(row.weight) || 0) > 0);
        return normalizeRound(Object.assign({ id: uid('s') }, row, { weight_locked: locked }), index, config.length);
      });
    }
    return [];
  }

  function fillEditor(event) {
    form.action = '/admin/events/criteria/' + event.id + '/edit/';
    $('#wizard-title').textContent = 'Edit criteria event';
    form.event_name.value = event.name || '';
    form.category.value = event.category || '';
    form.event_classification.value = event.classification || '';
    form.participation_type.value = event.participation_type === 'team' ? 'group' : (event.participation_type || 'individual');
    form.division.value = event.division === '—' ? '' : (event.division || '');
    form.venue.value = event.venue || '';
    form.start_date.value = event.start_date || '';
    form.end_date.value = event.end_date || event.start_date || '';
    if ($('#event-time')) $('#event-time').value = event.start_time || '';
    $('#status-preview').value = event.publication_status || 'draft';
    $('#publication-status').value = event.publication_status || 'draft';
    hydrateScoringWorkflow(event);
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
    $('#event-format-select').value = normalizeLoadedFormat(event.event_format);
    if ($('#stage-count')) $('#stage-count').value = String(stages.length);
    var stageTie = ((event.result_processing_config || {}).stage_tiebreak_method) || 'manual_decision';
    $('#stage-tiebreak-method').value = stageTie;
    resetRoundForm();
    syncFormatPanels();
    syncCriteriaFromScoringWorkflow();
    if (!criteria.length) {
      criteria = (event.judging_criteria_config || []).map(function (row) {
        return {
          id: row.id || uid('c'),
          name: row.name || '',
          description: row.description || '',
          weight: row.weight || 0,
          max_score: row.max_score || 0,
        };
      });
    }
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
    $('#final-result-basis').value = rp.final_result_basis || 'main_round';
    $('#placements-count').value = rp.placements_count || 3;
    $$('[data-rp]').forEach(function (input) {
      input.checked = !!rp[input.dataset.rp];
    });
    var js = event.judge_settings || {};
    var allowed = js.allowed_submission_methods || ['MOBILE'];
    $('#method-mobile').checked = allowed.indexOf('MOBILE') !== -1;
    $('#method-ocr').checked = allowed.indexOf('OCR') !== -1;
    $('#method-hybrid').checked = allowed.length > 1;
    $('#default-submission-method').value = js.default_submission_method || 'MOBILE';
    mobileApprovalRequired = js.mobile_approval_required !== false;
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
    document.documentElement.classList.add('event-wizard-open');
  }

  function closeWizard() {
    wizard.close();
  }

  function findEvent(id) {
    return events.find(function (row) { return String(row.id) === String(id); });
  }

  function csrfToken() {
    var match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    return match ? decodeURIComponent(match[1]) : '';
  }

  function workflowRequest(action, values) {
    var body = new URLSearchParams(values || {});
    body.set('workflow_action', action);
    return fetch('/admin/events/criteria/', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded', 'X-CSRFToken': csrfToken(), 'X-Requested-With': 'XMLHttpRequest' },
      body: body.toString(),
    }).then(function (response) { return response.json().then(function (data) {
      if (!response.ok || !data.success) throw new Error(data.message || 'Unable to save workflow data.');
      return data;
    }); });
  }

  function saveEventDraft() {
    if (!form.end_date.value && form.start_date.value) form.end_date.value = form.start_date.value;
    return workflowRequest('save_event_draft', {
      event_id: currentEventId(),
      event_name: form.event_name.value.trim(),
      event_classification: form.event_classification.value,
      participation_type: form.participation_type.value,
      venue: form.venue.value.trim(),
      start_date: form.start_date.value,
      event_time: ($('#event-time') || {}).value || '',
      event_category: form.category && form.category.value ? form.category.value : '',
    }).then(function (data) {
      scoringWorkflow.eventId = data.event.id;
      scoringWorkflow.eventName = data.event.name;
      form.action = '/admin/events/criteria/' + data.event.id + '/edit/';
      return data;
    });
  }

  function renderScoringCategories() {
    var roundPicker = $('#scoring-category-round');
    var selectedRound = roundPicker.value;
    roundPicker.innerHTML = stages.map(function (round, index) {
      return '<option value="' + escapeAttr(roundKeyFor(round, index)) + '">' + escapeHtml(round.name) + '</option>';
    }).join('');
    if (stages.some(function (round, index) { return roundKeyFor(round, index) === selectedRound; })) roundPicker.value = selectedRound;
    updateOfficialCategoryTotal();
    var eventSelect = $('#scoring-category-event');
    if (eventSelect) eventSelect.innerHTML = scoringWorkflow.eventId
      ? '<option value="' + scoringWorkflow.eventId + '">' + escapeHtml(scoringWorkflow.eventName) + '</option>'
      : '<option value="">Complete Step 1 first</option>';
    var host = $('#scoring-category-list');
    if (host) host.innerHTML = scoringWorkflow.categories.map(function (category) {
      var weightLabel = Number(category.overall_weight_percent)
        ? (category.overall_weight_percent + '%')
        : '0% (simple average)';
      return '<article class="pageant-review-card"><h4>' + escapeHtml(category.name) + '</h4><ul><li>' +
        escapeHtml(category.judge_mode === 'ranking' ? 'Ranking Mode' : 'Scoring Mode') +
        '</li><li>Order ' + category.display_order + ' · ' + weightLabel + '</li></ul>' +
        '<div class="criteria-editor-actions"><button type="button" class="match-secondary" data-category-edit="' + category.id + '">Edit</button>' +
        '<button type="button" class="pageant-danger-button" data-category-delete="' + category.id + '">Delete</button></div></article>';
    }).join('') || '<p class="pageant-empty-state">No categories saved yet.</p>';
  }

  function loadCategories() {
    if (!scoringWorkflow.eventId) return Promise.resolve();
    return workflowRequest('list_categories', { event_id: scoringWorkflow.eventId }).then(function (data) {
      var previous = scoringWorkflow.categories.slice();
      scoringWorkflow.categories = (data.categories || []).map(function (category) {
        var prior = previous.filter(function (row) { return String(row.id) === String(category.id); })[0];
        return {
          id: category.id,
          name: category.name,
          assigned_round_id: category.assigned_round_id || '',
          purpose: category.purpose || 'official',
          category_purpose: category.category_purpose || '',
          judge_mode: category.judge_mode,
          display_order: category.display_order,
          overall_weight_percent: category.overall_weight_percent,
          criteria: category.criteria || (prior && prior.criteria) || [],
        };
      });
      if (
        !scoringWorkflow.selectedCategoryId
        || !scoringWorkflow.categories.some(function (row) {
          return String(row.id) === String(scoringWorkflow.selectedCategoryId);
        })
      ) {
        scoringWorkflow.selectedCategoryId = scoringWorkflow.categories.length
          ? scoringWorkflow.categories[0].id
          : null;
      }
      syncCriteriaFromScoringWorkflow();
    });
  }

  function renderScoringCriteria() {
    var picker = $('#scoring-criterion-category');
    if (!picker) return;
    picker.innerHTML = '<option value="">Select category</option>' + scoringWorkflow.categories.map(function (category) {
      return '<option value="' + category.id + '">' + escapeHtml(category.name) +
        ' (' + (Number(category.overall_weight_percent) || 0) + '%)</option>';
    }).join('');
    picker.value = scoringWorkflow.selectedCategoryId || '';
    loadScoringCriteria();
  }

  function loadScoringCriteria() {
    var categoryId = ($('#scoring-criterion-category') || {}).value;
    var host = $('#scoring-criterion-list');
    if (!categoryId || !scoringWorkflow.eventId) {
      if (host) host.innerHTML = '<p class="pageant-empty-state">Save and select a category first.</p>';
      return;
    }
    scoringWorkflow.selectedCategoryId = categoryId;
    workflowRequest('list_criteria', { event_id: scoringWorkflow.eventId, category_id: categoryId }).then(function (data) {
      var ranking = data.category.judge_mode === 'ranking';
      $('#scoring-criterion-max-score-wrap').hidden = ranking;
      $('#scoring-criterion-max-score').required = !ranking;
      scoringWorkflow.criteria = data.criteria;
      var matchedCategory = null;
      scoringWorkflow.categories.forEach(function (row) {
        if (String(row.id) === String(data.category.id)) {
          row.criteria = data.criteria;
          row.judge_mode = data.category.judge_mode;
          if (data.category.overall_weight_percent != null) {
            row.overall_weight_percent = data.category.overall_weight_percent;
          }
          matchedCategory = row;
        }
      });
      if (!matchedCategory) {
        matchedCategory = {
          id: data.category.id,
          name: data.category.name,
          judge_mode: data.category.judge_mode,
          overall_weight_percent: data.category.overall_weight_percent || 0,
          criteria: data.criteria,
        };
      }
      updateCriteriaWeightMeter(matchedCategory, data.criteria);
      host.innerHTML = data.criteria.map(function (criterion) {
        var weightLabel = Number(criterion.weight_percent)
          ? (criterion.weight_percent + '% of event')
          : '0% (simple average)';
        return '<article class="pageant-review-card"><h4>' + escapeHtml(criterion.name) + '</h4><ul><li>Event Weight ' +
          weightLabel + ' · Order ' + criterion.display_order + '</li><li>' +
          (ranking ? 'Ranking Mode' : 'Max Score ' + criterion.max_score) + '</li></ul>' +
          '<div class="criteria-editor-actions"><button type="button" class="match-secondary" data-criterion-edit="' + criterion.id + '">Edit</button>' +
          '<button type="button" class="pageant-danger-button" data-criterion-delete="' + criterion.id + '">Delete</button></div></article>';
      }).join('') || '<p class="pageant-empty-state">No criteria saved for this category.</p>';
    }).catch(function (error) { showError(error.message); });
  }

  function renderView(event) {
    var savedCategories = (event.scoring_categories || []).map(function (category) {
      var criteria = (category.criteria || []).map(function (criterion) {
        return criterion.name + ' (' + criterion.weight_percent + '% of event)';
      }).join(', ');
      var criteriaTotal = roundWeight((category.criteria || []).reduce(function (sum, row) {
        return sum + (Number(row.weight_percent) || 0);
      }, 0));
      return category.name + ' · ' + (category.judge_mode === 'ranking' ? 'Ranking Mode' : 'Scoring Mode') +
        ' · Category ' + category.overall_weight_percent + '% · Criteria ' + criteriaTotal + '%' +
        (criteria ? ' · ' + criteria : '');
    }).join(' | ');
    var rows = [
      ['Event Name', event.name],
      ['Category', event.category_label || event.category],
      ['Event Classification', event.classification_label],
      ['Participation Type', event.participation_label],
      ['Venue', event.venue],
      ['Date & Time', event.start_date + (event.start_time ? ' · ' + event.start_time : '')],
      ['Participants', (event.participant_names || []).join(', ') || '—'],
      ['Scoring Categories & Criteria', savedCategories || '—'],
      ['Assigned Judges', (event.judge_names || []).join(', ') || '—'],
      ['Chief Judge', event.chief_judge_name],
      ['Tabulator in Charge', event.faculty_name],
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
  wizard.addEventListener('close', function () {
    document.documentElement.classList.remove('event-wizard-open');
  });
  $('#wizard-back').addEventListener('click', function () {
    if (activeStep > 1) setStep(activeStep - 1);
  });
  $('#wizard-next').addEventListener('click', function () {
    var error = mode === 'pageant' && window.PageantWizard
      ? window.PageantWizard.validate(activeStep)
      : validateStep(activeStep);
    if (error) {
      showError(error);
      return;
    }
    if (mode === 'standard' && activeStep === 1) {
      var button = this;
      button.disabled = true;
      saveEventDraft().then(function () {
        return loadCategories();
      }).then(function () {
        setStep(2);
      }).catch(function (draftError) {
        showError(draftError.message);
      }).finally(function () {
        button.disabled = false;
      });
      return;
    }
    if (activeStep < maxWizardStep()) setStep(activeStep + 1);
  });
  $$('.final-action').forEach(function (btn) {
    btn.addEventListener('click', function () {
      $('#publication-status').value = btn.dataset.status;
    });
  });
  form.addEventListener('submit', function (e) {
    if (mode === 'pageant' && window.PageantWizard) {
      window.PageantWizard.sync();
      if ($('#publication-status').value === 'published') {
        var issues = window.PageantWizard.publishIssues();
        if (issues.length) {
          e.preventDefault();
          setStep(5);
          showError(issues[0].text);
        }
      }
      return;
    }
    var publishing = $('#publication-status').value === 'published';
    var error = publishing ? (validateStep(1) || validateStep(2) || validateStep(3) || validateStep(4) || validateStep(5) || validateStep(6)) : validateStep(1);
    if (error) {
      e.preventDefault();
      showError(error);
      return;
    }
    syncHiddenFields();
  });

  $('#participation-type').addEventListener('change', function () {
    syncParticipantPanels();
    renderRoundSetup();
  });
  $('#event-format-select')?.addEventListener('change', syncFormatPanels);
  $('#round-type')?.addEventListener('change', function () {
    // A final round following earlier rounds normally continues from their scores,
    // but the picker stays editable afterwards.
    if ($('#round-type').value === 'final' && stages.length) {
      $('#round-score-handling').value = 'carry';
    }
    syncRoundFormVisibility();
  });
  $('#round-advancement')?.addEventListener('change', syncRoundFormVisibility);
  $('#round-score-handling')?.addEventListener('change', syncRoundFormVisibility);
  $('#save-round-btn')?.addEventListener('click', saveRoundFromForm);
  $('#cancel-round-edit')?.addEventListener('click', function () {
    resetRoundForm();
    showError('');
  });
  $('#add-another-round')?.addEventListener('click', function () {
    resetRoundForm();
    $('#round-name').focus();
    document.getElementById('round-form-card')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  });
  $('#configured-rounds-list')?.addEventListener('click', function (e) {
    var toggle = e.target.closest('[data-toggle-round]');
    var edit = e.target.closest('[data-edit-round]');
    var dup = e.target.closest('[data-duplicate-round]');
    var del = e.target.closest('[data-delete-round]');
    if (toggle) {
      expandedRoundId = toggle.getAttribute('data-toggle-round');
      renderConfiguredRounds();
      return;
    }
    if (edit) {
      var round = stages.find(function (row) { return String(row.id) === edit.getAttribute('data-edit-round'); });
      if (round) fillRoundForm(round);
      document.getElementById('round-form-card')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
      return;
    }
    if (dup) {
      duplicateRound(dup.getAttribute('data-duplicate-round'));
      return;
    }
    if (del) {
      if (!window.confirm('Remove this round from the scoresheet setup?')) return;
      deleteRound(del.getAttribute('data-delete-round'));
    }
  });
  $('#configured-rounds-list')?.addEventListener('dragstart', function (e) {
    var card = e.target.closest('[data-round-id]');
    if (!card) return;
    roundDragId = card.getAttribute('data-round-id');
    card.classList.add('is-dragging');
  });
  $('#configured-rounds-list')?.addEventListener('dragend', function (e) {
    e.target.closest('[data-round-id]')?.classList.remove('is-dragging');
    roundDragId = null;
  });
  $('#configured-rounds-list')?.addEventListener('dragover', function (e) {
    e.preventDefault();
    var card = e.target.closest('[data-round-id]');
    if (card) card.classList.add('is-drop');
  });
  $('#configured-rounds-list')?.addEventListener('dragleave', function (e) {
    e.target.closest('[data-round-id]')?.classList.remove('is-drop');
  });
  $('#configured-rounds-list')?.addEventListener('drop', function (e) {
    e.preventDefault();
    var card = e.target.closest('[data-round-id]');
    card?.classList.remove('is-drop');
    if (!roundDragId || !card || roundDragId === card.getAttribute('data-round-id')) return;
    var from = stages.findIndex(function (row) { return String(row.id) === String(roundDragId); });
    var to = stages.findIndex(function (row) { return String(row.id) === card.getAttribute('data-round-id'); });
    if (from < 0 || to < 0) return;
    var copy = stages.slice();
    var moved = copy.splice(from, 1)[0];
    copy.splice(to, 0, moved);
    stages = copy;
    renderRoundSetup();
  });
  $('#participant-search').addEventListener('input', filterParticipants);
  $('#participant-department-filter')?.addEventListener('change', filterParticipants);
  $('#participant-course-filter')?.addEventListener('change', filterParticipants);
  $('#select-all-participants').addEventListener('click', function () {
    setAllVisibleParticipants(true);
  });
  $('#clear-participants').addEventListener('click', function () {
    setAllVisibleParticipants(false);
  });
  document.addEventListener('change', function (e) {
    if (e.target.classList.contains('participant-check')) {
      updateParticipantCount();
      renderRoundSetup();
    }
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
  form.start_date.addEventListener('change', function () {
    form.end_date.value = form.start_date.value;
  });
  $('#event-time').addEventListener('change', function () {
    var pageantTime = $('#pageant-start-time');
    if (pageantTime) pageantTime.value = $('#event-time').value;
  });
  $('#save-scoring-category').addEventListener('click', function () {
    if (!scoringWorkflow.eventId) {
      showError('Complete Step 1 first.');
      return;
    }
    var overallWeight = $('#scoring-category-weight').value;
    if (overallWeight === '' || overallWeight == null) overallWeight = '0';
    workflowRequest(scoringWorkflow.editingCategoryId ? 'update_category' : 'create_category', {
      event_id: scoringWorkflow.eventId,
      category_id: scoringWorkflow.editingCategoryId || '',
      category_name: $('#scoring-category-name').value.trim(),
      assigned_round_id: $('#scoring-category-round').value,
      purpose: $('#scoring-category-purpose').value,
      judge_mode: $('#scoring-category-mode').value,
      display_order: $('#scoring-category-order').value,
      overall_weight_percent: overallWeight,
    }).then(function () {
      return loadCategories();
    }).then(function () {
      $('#scoring-category-name').value = '';
      $('#scoring-category-order').value = '';
      $('#scoring-category-weight').value = '';
      scoringWorkflow.editingCategoryId = null;
      $('#save-scoring-category').textContent = 'Save Category';
      renderScoringCategories();
      renderScoringCriteria();
    }).catch(function (workflowError) { showError(workflowError.message); });
  });
  $('#scoring-criterion-category').addEventListener('change', loadScoringCriteria);
  $('#scoring-category-round').addEventListener('change', updateOfficialCategoryTotal);
  $('#scoring-category-purpose').addEventListener('change', function () {
    var special = this.value === 'special_award';
    $('#scoring-category-weight').disabled = special;
    if (special) $('#scoring-category-weight').value = '0';
  });
  $('#save-scoring-criterion').addEventListener('click', function () {
    var categoryId = $('#scoring-criterion-category').value;
    var weightPercent = $('#scoring-criterion-weight').value;
    if (weightPercent === '' || weightPercent == null) {
      showError('Criterion Weight Within Category is required.');
      return;
    }
    if (Number(weightPercent) < 0) {
      showError('Criterion weights cannot be negative.');
      return;
    }
    workflowRequest(scoringWorkflow.editingCriterionId ? 'update_criterion' : 'create_criterion', {
      event_id: scoringWorkflow.eventId || '',
      category_id: categoryId,
      criterion_id: scoringWorkflow.editingCriterionId || '',
      criterion_name: $('#scoring-criterion-name').value.trim(),
      weight_percent: weightPercent,
      min_score: $('#scoring-criterion-min-score').value,
      tie_breaker_priority: $('#scoring-criterion-tie-priority').value,
      max_score: $('#scoring-criterion-max-score').value,
      display_order: $('#scoring-criterion-order').value,
    }).then(function () {
      $('#scoring-criterion-name').value = '';
      $('#scoring-criterion-weight').value = '';
      $('#scoring-criterion-max-score').value = '';
      $('#scoring-criterion-order').value = '';
      scoringWorkflow.editingCriterionId = null;
      $('#save-scoring-criterion').textContent = 'Save Criterion';
      loadScoringCriteria();
    }).catch(function (workflowError) { showError(workflowError.message); });
  });
  document.addEventListener('click', function (event) {
    var categoryEdit = event.target.closest('[data-category-edit]');
    var categoryDelete = event.target.closest('[data-category-delete]');
    var criterionEdit = event.target.closest('[data-criterion-edit]');
    var criterionDelete = event.target.closest('[data-criterion-delete]');
    if (categoryEdit) {
      var category = scoringWorkflow.categories.filter(function (row) { return String(row.id) === categoryEdit.dataset.categoryEdit; })[0];
      if (!category) return;
      scoringWorkflow.editingCategoryId = category.id;
      $('#scoring-category-name').value = category.name;
      $('#scoring-category-round').value = category.assigned_round_id || (stages[0] ? roundKeyFor(stages[0], 0) : '');
      $('#scoring-category-purpose').value = category.purpose || 'official';
      $('#scoring-category-mode').value = category.judge_mode;
      $('#scoring-category-order').value = category.display_order;
      $('#scoring-category-weight').value = category.overall_weight_percent;
      $('#save-scoring-category').textContent = 'Update Category';
    } else if (categoryDelete) {
      workflowRequest('delete_category', { event_id: scoringWorkflow.eventId, category_id: categoryDelete.dataset.categoryDelete })
        .then(loadCategories).then(function () { renderScoringCategories(); renderScoringCriteria(); })
        .catch(function (workflowError) { showError(workflowError.message); });
    } else if (criterionEdit) {
      var criterion = scoringWorkflow.criteria.filter(function (row) { return String(row.id) === criterionEdit.dataset.criterionEdit; })[0];
      if (!criterion) return;
      scoringWorkflow.editingCriterionId = criterion.id;
      $('#scoring-criterion-name').value = criterion.name;
      $('#scoring-criterion-min-score').value = criterion.min_score == null ? 0 : criterion.min_score;
      $('#scoring-criterion-tie-priority').value = criterion.tie_breaker_priority || '';
      $('#scoring-criterion-weight').value = criterion.weight_percent;
      $('#scoring-criterion-max-score').value = criterion.max_score || '';
      $('#scoring-criterion-order').value = criterion.display_order;
      $('#save-scoring-criterion').textContent = 'Update Criterion';
    } else if (criterionDelete) {
      workflowRequest('delete_criterion', {
        event_id: scoringWorkflow.eventId,
        category_id: scoringWorkflow.selectedCategoryId,
        criterion_id: criterionDelete.dataset.criterionDelete,
      }).then(loadScoringCriteria).catch(function (workflowError) { showError(workflowError.message); });
    }
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
      if (btn.dataset.finalized === '1' && !window.CRITERIA_CAN_DELETE_FINALIZED && !window.CRITERIA_IS_SUPERUSER) {
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
  window.CriteriaWizard = {
    setMode: setMode,
    setStep: setStep,
    getMode: function () { return mode; },
    getActiveStep: function () { return activeStep; },
  };
}());
