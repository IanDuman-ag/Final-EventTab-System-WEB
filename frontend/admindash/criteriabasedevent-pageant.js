/**
 * Pageant wizard overlay for Criteria-Based Events.
 * Activates when Category = Special Event and Special Event Type = Pageant.
 * Preserves the standard wizard for all other criteria events.
 */
(function () {
  'use strict';

  var canonicalSelector = function (sel) {
    return String(sel)
      .replace(/#pageant-event-name/g, '[name="event_name"]')
      .replace(/#pageant-classification/g, '#event-classification')
      .replace(/#pageant-venue/g, '[name="venue"]')
      .replace(/#pageant-start-date/g, '[name="start_date"]')
      .replace(/#pageant-end-date/g, '[name="end_date"]')
      .replace(/#pageant-faculty/g, '[name="faculty_account"]')
      .replace(/#pageant-chief-judge/g, '#chief-judge')
      .replace(/#pageant-existing-candidates/g, '#candidate-options')
      .replace(/#pageant-judge-options/g, '#judge-options');
  };
  var $ = function (sel, root) { return (root || document).querySelector(canonicalSelector(sel)); };
  var $$ = function (sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(canonicalSelector(sel))); };

  var form = $('#wizard-form');
  var wizard = $('#event-wizard');
  if (!form || !wizard) return;

  var pageantMode = false;
  var pageantSegments = [];
  var pendingCandidates = [];
  var specialAwards = [];
  var competitionCategories = [];
  var pairEntries = [];
  var advancementEnabled = false;
  var pendingModeChange = null;
  var canonicalHomes = {};

  var FORMAT_META = {
    male_female: {
      title: 'Male and Female Pageant',
      blurb: 'Separate candidates, rankings, and winners for male and female categories.',
      categories: ['Male Category', 'Female Category'],
    },
    individual: {
      title: 'Individual Pageant',
      blurb: 'All candidates compete individually in one category.',
      categories: ['Open Category'],
    },
    pairs: {
      title: 'Pair or Couple Pageant',
      blurb: 'Two linked candidates compete as one entry.',
      categories: ['Pair Category'],
    },
    custom: {
      title: 'Custom Format',
      blurb: 'Create your own competition categories.',
      categories: ['Open Category'],
    },
  };

  var DEFAULT_SEGMENTS = [
    { name: 'Production Number', weight: 15, enabled: true, counts_toward_main_ranking: true, round_type: 'preliminary', criteria: [
      { name: 'Choreography', weight: 40, max_score: 100, description: '' },
      { name: 'Stage Presence', weight: 35, max_score: 100, description: '' },
      { name: 'Energy', weight: 25, max_score: 100, description: '' },
    ]},
    { name: 'Talent', weight: 25, enabled: true, counts_toward_main_ranking: true, round_type: 'preliminary', criteria: [
      { name: 'Mastery', weight: 30, max_score: 100, description: '' },
      { name: 'Creativity', weight: 25, max_score: 100, description: '' },
      { name: 'Stage Presence', weight: 25, max_score: 100, description: '' },
      { name: 'Audience Impact', weight: 20, max_score: 100, description: '' },
    ]},
    { name: 'Formal Wear', weight: 20, enabled: true, counts_toward_main_ranking: true, round_type: 'semifinal', criteria: [
      { name: 'Elegance', weight: 40, max_score: 100, description: '' },
      { name: 'Carriage', weight: 30, max_score: 100, description: '' },
      { name: 'Overall Look', weight: 30, max_score: 100, description: '' },
    ]},
    { name: 'Interview', weight: 20, enabled: true, counts_toward_main_ranking: true, round_type: 'semifinal', criteria: [
      { name: 'Content', weight: 40, max_score: 100, description: '' },
      { name: 'Delivery', weight: 35, max_score: 100, description: '' },
      { name: 'Insight', weight: 25, max_score: 100, description: '' },
    ]},
    { name: 'Question and Answer', weight: 20, enabled: true, counts_toward_main_ranking: true, round_type: 'final', criteria: [
      { name: 'Content and Relevance', weight: 40, max_score: 100, description: '' },
      { name: 'Delivery', weight: 30, max_score: 100, description: '' },
      { name: 'Confidence', weight: 20, max_score: 100, description: '' },
      { name: 'Time Management', weight: 10, max_score: 100, description: '' },
    ]},
  ];

  var AWARD_PRESETS = [
    'Best in Talent', 'Best in Formal Wear', 'Best in Sports Wear',
    'Best in Interview', 'Best in Question and Answer',
    'People\'s Choice Award', 'Photogenic Award', 'Congeniality Award',
  ];

  function showError(msg) {
    var el = $('#wizard-error');
    if (el) el.textContent = msg || '';
  }

  function categoryValue() {
    var sel = form.querySelector('[name="category"]');
    return sel ? sel.value : '';
  }

  function specialTypeValue() {
    var sel = $('#special-event-type');
    return sel ? sel.value : '';
  }

  function pageantFormat() {
    var checked = $('input[name="pageant_format"]:checked');
    return checked ? checked.value : 'individual';
  }

  function isPageantSelected() {
    return categoryValue() === 'Special Event' && specialTypeValue() === 'pageant';
  }

  function uid(prefix) {
    return (prefix || 'id') + '-' + Math.random().toString(36).slice(2, 9);
  }

  function cloneSegments(src) {
    return JSON.parse(JSON.stringify(src || []));
  }

  function syncSpecialTypeVisibility() {
    var wrap = $('#special-event-type-wrap');
    var isSpecial = categoryValue() == 'Special Event';
    if (wrap) wrap.hidden = !isSpecial;
    if (!isSpecial) {
      var sel = $('#special-event-type');
      if (sel) sel.value = '';
    }
    applyPageantMode(isPageantSelected());
  }

  function requestModeChange(message, accept, options) {
    var dialog = $('#pageant-confirm-dialog');
    if (!dialog) return;
    options = options || {};
    pendingModeChange = accept;
    $('#pageant-confirm-title').textContent = options.title || 'Leave Pageant setup?';
    $('#pageant-confirm-message').textContent = message;
    $('#pageant-confirm-cancel').textContent = options.cancelLabel || 'Keep Pageant';
    $('#pageant-confirm-accept').textContent = options.acceptLabel || 'Switch setup';
    dialog.showModal();
  }

  function moveCanonical(id, destination, first) {
    var node = $('#' + id);
    if (!node || !destination) return;
    if (!canonicalHomes[id]) {
      var marker = document.createComment(id + '-home');
      node.parentNode.insertBefore(marker, node);
      canonicalHomes[id] = marker;
    }
    if (first) destination.insertBefore(node, destination.firstChild);
    else destination.appendChild(node);
  }

  function restoreCanonical() {
    Object.keys(canonicalHomes).forEach(function (id) {
      var node = $('#' + id);
      var marker = canonicalHomes[id];
      if (node && marker && marker.parentNode) marker.parentNode.insertBefore(node, marker.nextSibling);
    });
  }

  function adoptCanonicalControls() {
    var detailsGrid = $('[data-pageant-step="1"] .wizard-grid');
    var standardGrid = $('[data-step="1"] .wizard-grid');
    ['pageant-event-name', 'pageant-classification', 'pageant-venue', 'pageant-start-date', 'pageant-end-date',
      'pageant-faculty', 'pageant-chief-judge', 'pageant-existing-candidates', 'pageant-judge-options']
      .forEach(function (id) {
        var node = document.getElementById(id);
        if (node) node.closest('label,fieldset,details,div').remove();
      });
    if (detailsGrid && standardGrid) {
      ['faculty_account', 'start_date', 'venue', 'event_classification', 'event_name'].forEach(function (name) {
        var control = form.querySelector('[name="' + name + '"]');
        if (control) moveCanonical(control.closest('label').id || (control.closest('label').id = 'canonical-' + name), detailsGrid, true);
      });
    }
    var judges = $('[data-pageant-tab-panel="judges"]');
    ['chief-judge', 'judge-options'].forEach(function (id) { moveCanonical(id, judges); });
    moveCanonical('candidate-options', $('#pageant-existing-candidates-section'));
  }

  function applyPageantMode(on) {
    var was = pageantMode;
    pageantMode = !!on;
    if (window.CriteriaWizard) window.CriteriaWizard.setMode(pageantMode ? 'pageant' : 'standard');

    var headMeta = $('.pageant-head-meta');
    if (headMeta) headMeta.hidden = !pageantMode;

    var draftBtn = $('#save-draft-action');
    var publishBtn = $('#publish-action');
    if (draftBtn) draftBtn.textContent = pageantMode ? 'Save as Draft' : 'Save as Draft';
    if (publishBtn) publishBtn.textContent = pageantMode ? 'Publish Pageant' : 'Publish';

    var title = $('#wizard-title');
    if (title) {
      var editing = title.textContent.indexOf('Edit') === 0;
      title.textContent = pageantMode ? (editing ? 'Edit Pageant' : 'Create Pageant') : (editing ? 'Edit criteria event' : 'Create criteria event');
    }
    var subtitle = $('#wizard-subtitle');
    if (subtitle) subtitle.textContent = pageantMode
      ? 'Set up the candidates, scoring segments, judges, and awards for this Pageant.'
      : 'Configure participants, judging criteria, score settings, and championship points.';

    var hiddenType = $('#special-event-type-input');
    if (hiddenType) hiddenType.value = pageantMode ? 'pageant' : (specialTypeValue() || '');

    // Force individual + multiple stage for pageants (backend expectation)
    if (pageantMode) {
      adoptCanonicalControls();
      var part = $('#participation-type');
      if (part) part.value = 'individual';
      var fmt = $('input[name="event_format"][value="multiple_stage"]');
      if (fmt) fmt.checked = true;
      if (!pageantSegments.length) {
        pageantSegments = cloneSegments(DEFAULT_SEGMENTS).map(function (seg, i) {
          seg.id = 's' + (i + 1);
          (seg.criteria || []).forEach(function (c, ci) { c.id = seg.id + 'c' + (ci + 1); });
          return seg;
        });
      }
      if (!was && window.CriteriaWizard) window.CriteriaWizard.setStep(1);
      renderCategories();
      renderCandidates();
      renderSegments();
      renderAwards();
      syncAdvancementPanel();
    } else if (was) restoreCanonical();
  }

  function renderPageant(activeStep) {
    if (activeStep === 5) buildPageantReview();
    if (activeStep === 3) updateWeightMeter();
    var body = $('.wizard-body');
    if (body) body.scrollTop = 0;
    var heading = $('.wizard-panel[data-pageant-step="' + activeStep + '"] h3');
    if (heading) {
      heading.setAttribute('tabindex', '-1');
      heading.focus({ preventScroll: true });
    }
  }

  function selectedFormatCard() {
    return pageantFormat();
  }

  function recommendCategories(format) {
    var meta = FORMAT_META[format];
    competitionCategories = meta ? meta.categories.slice() : ['Open Category'];
    renderCategories();
  }

  function renderCategories() {
    var list = $('#pageant-categories-list');
    if (!list) return;
    list.innerHTML = competitionCategories.map(function (name, idx) {
      return '<li><input type="text" class="pageant-category-input" data-idx="' + idx + '" value="' +
        escapeAttr(name) + '" aria-label="Competition category ' + (idx + 1) + '">' +
        '<button type="button" class="pageant-danger-button pageant-cat-remove" data-idx="' + idx + '" aria-label="Remove category">Remove</button></li>';
    }).join('') || '<li class="pageant-empty-inline">No categories yet.</li>';
  }

  function escapeAttr(s) {
    return String(s || '').replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;');
  }
  function escapeHtml(s) {
    return String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  function renderCandidates() {
    var host = $('#pageant-candidates-list');
    var counts = $('#pageant-candidate-counts');
    if (!host) return;
    var fmt = pageantFormat();
    if (!pendingCandidates.length) {
      host.innerHTML = '<div class="pageant-empty-state">' +
        '<strong>No candidates have been added yet.</strong>' +
        '<p>Select candidates from the registry below.</p></div>';
    } else {
      host.innerHTML = pendingCandidates.map(function (c, idx) {
        return '<article class="pageant-candidate-card" data-idx="' + idx + '">' +
          '<div class="pageant-candidate-main">' +
          '<strong>#' + escapeHtml(c.number || '—') + ' ' + escapeHtml(c.name || 'Unnamed') + '</strong>' +
          '<span>' + escapeHtml(c.category || '—') + ' · ' + escapeHtml(c.team_label || c.team || 'No team') + '</span>' +
          '<span class="pageant-pill">' + escapeHtml(c.status || 'Active') + '</span></div>' +
          '<div class="pageant-candidate-actions">' +
          '<button type="button" class="match-secondary" data-cand-edit="' + idx + '">Edit</button>' +
          '<button type="button" class="pageant-danger-button" data-cand-remove="' + idx + '" aria-label="Remove ' +
          escapeAttr(c.name || 'candidate') + ' from Pageant roster">⌫ Remove</button>' +
          '</div></article>';
      }).join('');
    }
    if (counts) {
      var total = pendingCandidates.length;
      var male = pendingCandidates.filter(function (c) { return /male/i.test(c.category || '') && !/female/i.test(c.category || ''); }).length;
      var female = pendingCandidates.filter(function (c) { return /female/i.test(c.category || ''); }).length;
      var pairs = pairEntries.length;
      counts.innerHTML =
        '<span><strong>' + total + '</strong> Total</span>' +
        (fmt === 'male_female' ? '<span><strong>' + male + '</strong> Male</span><span><strong>' + female + '</strong> Female</span>' : '') +
        (fmt === 'pairs' ? '<span><strong>' + pairs + '</strong> Pair Entries</span>' : '');
    }
    syncPairFormVisibility();
  }

  function syncPairFormVisibility() {
    var pair = $('#pageant-pair-form');
    var solo = $('#pageant-solo-form');
    var isPair = pageantFormat() === 'pairs';
    if (pair) pair.hidden = !isPair;
    if (solo) solo.hidden = isPair;
  }

  function renderSegments() {
    var host = $('#pageant-segments-list');
    if (!host) return;
    if (!pageantSegments.length) {
      host.innerHTML = '<div class="pageant-empty-state"><strong>No segments yet.</strong>' +
        '<p>Use the recommended template or add a segment from scratch.</p></div>';
      updateWeightMeter();
      return;
    }
    host.innerHTML = pageantSegments.map(function (seg, idx) {
      var critCount = (seg.criteria || []).length;
      var critTotal = (seg.criteria || []).reduce(function (s, c) { return s + Number(c.weight || 0); }, 0);
      var critStatus = Math.abs(critTotal - 100) < 0.01 ? 'Complete' : (critTotal < 100 ? 'Incomplete' : 'Over');
      return '<article class="pageant-segment-card' + (seg.enabled === false ? ' is-disabled' : '') + '" data-seg="' + idx + '">' +
        '<header><strong>' + escapeHtml(seg.name) + '</strong>' +
        '<span class="pageant-pill">' + escapeHtml(seg.round_type || 'preliminary') + '</span></header>' +
        '<p>Weight <strong>' + Number(seg.weight || 0) + '%</strong> · ' + critCount + ' criteria · Criteria ' +
        Number(critTotal.toFixed(1)) + '% — ' + critStatus + '</p>' +
        '<div class="pageant-segment-actions">' +
        '<button type="button" class="match-secondary" data-seg-edit="' + idx + '">Edit</button>' +
        '<button type="button" class="match-secondary" data-seg-dup="' + idx + '">Duplicate</button>' +
        '<button type="button" class="match-secondary" data-seg-up="' + idx + '" aria-label="Move up">↑</button>' +
        '<button type="button" class="match-secondary" data-seg-down="' + idx + '" aria-label="Move down">↓</button>' +
        '<button type="button" class="pageant-danger-button" data-seg-remove="' + idx + '">Remove</button>' +
        '</div></article>';
    }).join('');
    updateWeightMeter();
  }

  function updateWeightMeter() {
    var meter = $('#pageant-weight-meter');
    if (!meter) return;
    var total = pageantSegments.filter(function (s) {
      return s.enabled !== false && s.counts_toward_main_ranking !== false;
    }).reduce(function (sum, s) { return sum + Number(s.weight || 0); }, 0);
    total = Math.round(total * 100) / 100;
    var cls = 'is-amber';
    var text = 'Below Required Total';
    if (Math.abs(total - 100) < 0.01) { cls = 'is-green'; text = 'Complete'; }
    else if (total > 100) { cls = 'is-red'; text = 'Above Required Total'; }
    meter.className = 'pageant-weight-meter ' + cls;
    meter.innerHTML = '<strong>Main Pageant Score: ' + total + '% of 100% configured</strong> — ' + text;
  }

  function renderAwards() {
    var host = $('#pageant-awards-list');
    if (!host) return;
    if (!specialAwards.length) {
      host.innerHTML = '<p class="pageant-empty-inline">No special awards yet. Add a preset or create a custom award.</p>';
      return;
    }
    host.innerHTML = specialAwards.map(function (a, idx) {
      return '<div class="pageant-award-row"><strong>' + escapeHtml(a.title || a.name) + '</strong>' +
        '<span>' + escapeHtml(a.category || 'All categories') + ' · ' + escapeHtml(a.method || 'Highest segment score') + '</span>' +
        '<button type="button" class="pageant-danger-button" data-award-remove="' + idx + '">Remove</button></div>';
    }).join('');
  }

  function syncAdvancementPanel() {
    var panel = $('#pageant-advancement-fields');
    if (panel) panel.hidden = !advancementEnabled;
  }

  function collectPageantConfig() {
    var fmt = pageantFormat() || 'individual';
    return {
      pageant_format: fmt,
      competition_categories: competitionCategories.slice(),
      description: '',
      rules: '',
      segment_template: pageantSegments.length ? 'custom' : 'standard',
      advancement_enabled: advancementEnabled,
      advancement: advancementEnabled ? {
        top_n: Number(($('#pageant-adv-topn') && $('#pageant-adv-topn').value) || 0),
        source_segment: ($('#pageant-adv-source') && $('#pageant-adv-source').value) || '',
        notes: ($('#pageant-adv-notes') && $('#pageant-adv-notes').value) || '',
      } : {},
      special_awards: specialAwards.slice(),
      pair_entries: pairEntries.slice(),
      pending_candidates: pendingCandidates.map(function (c) {
        return {
          id: c.id || null,
          number: c.number,
          name: c.name,
          category: c.category,
          team: c.team,
          team_label: c.team_label,
          status: c.status || 'Active',
          course: c.course || '',
          year_level: c.year_level || '',
          age: c.age || '',
          bio: c.bio || '',
          partner_name: c.partner_name || '',
        };
      }),
      candidate_extras: {},
      start_time: ($('#pageant-start-time') && $('#pageant-start-time').value) || '',
      end_time: ($('#pageant-end-time') && $('#pageant-end-time').value) || '',
      team_ids: [],
    };
  }

  function syncPageantHidden() {
    if (!pageantMode) {
      var cfg = $('#pageant-config-input');
      if (cfg) cfg.value = '{}';
      var type = $('#special-event-type-input');
      if (type && categoryValue() !== 'Special Event') type.value = '';
      return;
    }
    var type = $('#special-event-type-input');
    if (type) type.value = 'pageant';
    var cfg = $('#pageant-config-input');
    if (cfg) cfg.value = JSON.stringify(collectPageantConfig());

    // Mirror key fields into the shared form controls used by standard save
    var name = $('#pageant-event-name');
    var stdName = form.querySelector('[name="event_name"]');
    if (name && stdName) stdName.value = name.value;
    var venue = $('#pageant-venue');
    var stdVenue = form.querySelector('[name="venue"]');
    if (venue && stdVenue) stdVenue.value = venue.value;
    var start = $('#pageant-start-date');
    var stdStart = form.querySelector('[name="start_date"]');
    if (start && stdStart) stdStart.value = start.value;
    var stdEnd = form.querySelector('[name="end_date"]');
    if (stdStart && stdEnd) stdEnd.value = stdStart.value;
    var classification = form.querySelector('[name="event_classification"]');
    var pClass = $('#pageant-classification');
    if (classification && pClass) classification.value = pClass.value;
    var faculty = form.querySelector('[name="faculty_account"]');
    var pFac = $('#pageant-faculty');
    if (faculty && pFac) faculty.value = pFac.value;
    var chief = $('#chief-judge');
    var pChief = $('#pageant-chief-judge');
    if (chief && pChief) chief.value = pChief.value;

    var part = $('#participation-type');
    if (part) part.value = 'individual';
    var fmt = $('input[name="event_format"][value="multiple_stage"]');
    if (fmt) fmt.checked = true;

    var rounds = $('#rounds-config');
    if (rounds) rounds.value = JSON.stringify(pageantSegments);

    // Flatten criteria for any consumer reading the hidden field before submit
    var flat = [];
    pageantSegments.filter(function (s) { return s.enabled !== false; }).forEach(function (seg) {
      (seg.criteria || []).forEach(function (c) {
        flat.push({
          id: c.id || uid('c'),
          name: (seg.name + ': ' + (c.name || 'Criterion')),
          description: c.description || '',
          weight: c.weight,
          max_score: c.max_score,
        });
      });
    });
    var crit = $('#criteria-config');
    if (crit) crit.value = JSON.stringify(flat);

    // Selected existing candidates + pending will be merged server-side
    var ids = [];
    $$('#pageant-existing-candidates .participant-check:checked').forEach(function (box) {
      ids.push(Number(box.value));
    });
    pendingCandidates.forEach(function (c) {
      if (c.id) ids.push(Number(c.id));
    });
    var pid = $('#participant-ids');
    if (pid) pid.value = JSON.stringify(ids);

    // Judges
    var judgeIds = [];
    $$('#pageant-judge-options .judge-check:checked').forEach(function (box) {
      judgeIds.push(Number(box.value));
    });
    // Also sync standard judge checkboxes for consistency
    $$('#judge-options .judge-check').forEach(function (box) {
      box.checked = judgeIds.indexOf(Number(box.value)) >= 0;
    });
    var jid = $('#judge-ids');
    if (jid) jid.value = JSON.stringify(judgeIds);

    var scoreMethod = $('input[name="criteria_score_method"][value="weighted_percentage"]');
    if (scoreMethod) scoreMethod.checked = true;
  }

  function validatePageantStep(n) {
    syncPageantHidden();
    if (n === 1) {
      var name = ($('#pageant-event-name') && $('#pageant-event-name').value || '').trim();
      if (!name) return 'Event Name is required.';
      if (!competitionCategories.length) return 'Add at least one competition category.';
    }
    if (n === 2) {
      var existing = $$('#pageant-existing-candidates .participant-check:checked').length;
      if (!pendingCandidates.length && !existing) {
        // allow continue; publish will block
      }
      var nums = {};
      for (var i = 0; i < pendingCandidates.length; i++) {
        var key = (pendingCandidates[i].category || '') + '::' + (pendingCandidates[i].number || '');
        if (nums[key]) return 'Candidate numbers must be unique within each category.';
        nums[key] = true;
        if (!pendingCandidates[i].name) return 'Every candidate needs a full name.';
        if (!pendingCandidates[i].category) return 'Every candidate must have a competition category.';
      }
      if (pageantFormat() === 'pairs') {
        for (var p = 0; p < pairEntries.length; p++) {
          if (!pairEntries[p].partner_a_name || !pairEntries[p].partner_b_name) {
            return 'Pair entries must contain both candidates.';
          }
        }
      }
    }
    if (n === 3) {
      if (!pageantSegments.length) return 'Add at least one segment.';
      var total = pageantSegments.filter(function (s) {
        return s.enabled !== false && s.counts_toward_main_ranking !== false;
      }).reduce(function (sum, s) { return sum + Number(s.weight || 0); }, 0);
      if (Math.abs(total - 100) > 0.01) {
        return 'Active main-ranking segment weights must total exactly 100% (currently ' + total + '%).';
      }
      for (var s = 0; s < pageantSegments.length; s++) {
        var seg = pageantSegments[s];
        if (seg.enabled === false) continue;
        if (!(seg.criteria || []).length) return '"' + seg.name + '" needs at least one criterion.';
        var ct = (seg.criteria || []).reduce(function (a, c) { return a + Number(c.weight || 0); }, 0);
        if (Math.abs(ct - 100) > 0.01) {
          return '"' + seg.name + '" criteria must total 100% (currently ' + ct + '%).';
        }
      }
    }
    if (n === 4) {
      var judges = $$('#pageant-judge-options .judge-check:checked').length;
      if (!judges) {
        // warn but allow draft navigation
      }
    }
    return '';
  }

  function publishIssues() {
    var issues = [];
    syncPageantHidden();
    if (!(($('#pageant-event-name') && $('#pageant-event-name').value) || '').trim()) {
      issues.push({ text: 'Add an event name', step: 1 });
    }
    if (!(($('#pageant-venue') && $('#pageant-venue').value) || '').trim()) {
      issues.push({ text: 'Add a venue', step: 1 });
    }
    if (!(($('#pageant-faculty') && $('#pageant-faculty').value) || '').trim()) {
      issues.push({ text: 'Assign Faculty in Charge', step: 1 });
    }
    if (!pageantFormat()) issues.push({ text: 'Select a pageant format', step: 1 });
    var candCount = pendingCandidates.length + $$('#pageant-existing-candidates .participant-check:checked').length;
    if (candCount < 1) issues.push({ text: 'Add at least one candidate', step: 2 });
    var step3 = validatePageantStep(3);
    if (step3) issues.push({ text: step3, step: 3 });
    if (!$$('#pageant-judge-options .judge-check:checked').length) {
      issues.push({ text: 'Assign at least one judge', step: 4 });
    }
    if (!(($('#pageant-chief-judge') && $('#pageant-chief-judge').value) || '').trim()) {
      issues.push({ text: 'Select a Judge Chairperson / Chief Judge', step: 4 });
    }
    return issues;
  }

  function buildPageantReview() {
    var host = $('#pageant-review');
    if (!host) return;
    var issues = publishIssues();
    var checklist = [
      { label: 'Pageant Details', ok: !issues.some(function (i) { return i.step === 1; }) },
      { label: 'Candidates', ok: pendingCandidates.length + $$('#pageant-existing-candidates .participant-check:checked').length > 0, optional: false },
      { label: 'Segments', ok: pageantSegments.length > 0 },
      { label: 'Scoring Criteria', ok: !validatePageantStep(3) },
      { label: 'Judges', ok: $$('#pageant-judge-options .judge-check:checked').length > 0 },
      { label: 'Advancement', ok: true, optional: !advancementEnabled },
      { label: 'Special Awards', ok: true, optional: true },
      { label: 'Schedule', ok: !!(($('#pageant-start-date') && $('#pageant-start-date').value)) },
    ];
    var ready = !issues.length;
    host.innerHTML =
      '<div class="pageant-publish-banner ' + (ready ? 'is-ready' : 'is-blocked') + '">' +
      (ready
        ? '<strong>Your Pageant is ready to publish.</strong>'
        : '<strong>Complete the required items below before publishing.</strong>') +
      '</div>' +
      '<ul class="pageant-checklist">' + checklist.map(function (item) {
        var state = item.ok ? 'Complete' : (item.optional ? 'Optional' : 'Needs Attention');
        return '<li class="is-' + state.toLowerCase().replace(' ', '-') + '"><span>' + item.label +
          '</span><em>' + state + '</em></li>';
      }).join('') + '</ul>' +
      (issues.length
        ? '<ul class="pageant-issue-list">' + issues.map(function (issue) {
          return '<li><span>' + escapeHtml(issue.text) + '</span>' +
            '<button type="button" class="match-secondary" data-goto-step="' + issue.step + '">Go fix</button></li>';
        }).join('') + '</ul>'
        : '') +
      '<div class="review-summary pageant-review-cards">' +
      card('Event Details', [
        ($('#pageant-event-name') && $('#pageant-event-name').value) || '—',
        FORMAT_META[pageantFormat()] ? FORMAT_META[pageantFormat()].title : '—',
        (($('#pageant-venue') && $('#pageant-venue').value) || 'No venue') +
        ' · ' + (($('#pageant-start-date') && $('#pageant-start-date').value) || 'No date'),
      ], 1) +
      card('Candidate Summary', [
        (pendingCandidates.length + $$('#pageant-existing-candidates .participant-check:checked').length) + ' candidates',
        competitionCategories.join(', ') || 'No categories',
      ], 2) +
      card('Segments and Scoring', [
        pageantSegments.length + ' segments',
        ($('#pageant-weight-meter') && $('#pageant-weight-meter').textContent) || '',
      ], 3) +
      card('Judges', [
        $$('#pageant-judge-options .judge-check:checked').length + ' judges assigned',
        advancementEnabled ? 'Advancement enabled' : 'No elimination rounds',
        specialAwards.length ? specialAwards.length + ' special awards' : 'No special awards',
      ], 4) +
      '</div>';
  }

  function card(title, lines, step) {
    return '<article class="pageant-review-card"><header><h4>' + escapeHtml(title) +
      '</h4><button type="button" class="match-secondary" data-goto-step="' + step + '">Edit</button></header><ul>' +
      lines.map(function (l) { return '<li>' + escapeHtml(l) + '</li>'; }).join('') +
      '</ul></article>';
  }

  function openCandidateForm(editIdx) {
    var dialog = $('#pageant-candidate-dialog');
    if (!dialog) return;
    var isPair = pageantFormat() === 'pairs';
    syncPairFormVisibility();
    var catSel = $('#pageant-cand-category');
    if (catSel) {
      catSel.innerHTML = competitionCategories.map(function (c) {
        return '<option value="' + escapeAttr(c) + '">' + escapeHtml(c) + '</option>';
      }).join('');
    }
    $('#pageant-cand-edit-idx').value = editIdx == null ? '' : String(editIdx);
    if (editIdx != null && pendingCandidates[editIdx]) {
      var c = pendingCandidates[editIdx];
      $('#pageant-cand-number').value = c.number || '';
      $('#pageant-cand-name').value = c.name || '';
      $('#pageant-cand-team').value = c.team || '';
      if (catSel) catSel.value = c.category || '';
      $('#pageant-cand-course').value = c.course || '';
      $('#pageant-cand-year').value = c.year_level || '';
      $('#pageant-cand-age').value = c.age || '';
      $('#pageant-cand-bio').value = c.bio || '';
      $('#pageant-cand-partner').value = c.partner_name || '';
    } else {
      ['pageant-cand-number', 'pageant-cand-name', 'pageant-cand-team', 'pageant-cand-course',
        'pageant-cand-year', 'pageant-cand-age', 'pageant-cand-bio', 'pageant-cand-partner']
        .forEach(function (id) { var el = $('#' + id); if (el) el.value = ''; });
    }
    dialog.showModal();
  }

  function saveCandidateFromForm() {
    var idxRaw = $('#pageant-cand-edit-idx').value;
    var isPair = pageantFormat() === 'pairs';
    var row = {
      number: ($('#pageant-cand-number').value || '').trim(),
      name: ($('#pageant-cand-name').value || '').trim(),
      category: ($('#pageant-cand-category').value || '').trim(),
      team: ($('#pageant-cand-team').value || '').trim(),
      team_label: ($('#pageant-cand-team').value || '').trim(),
      status: 'Active',
      course: ($('#pageant-cand-course').value || '').trim(),
      year_level: ($('#pageant-cand-year').value || '').trim(),
      age: ($('#pageant-cand-age').value || '').trim(),
      bio: ($('#pageant-cand-bio').value || '').trim(),
      partner_name: ($('#pageant-cand-partner').value || '').trim(),
    };
    if (!row.number || !row.name) {
      showError('Candidate number and full name are required.');
      return;
    }
    if (!row.category) {
      showError('Competition category is required.');
      return;
    }
    if (!row.team) {
      showError('Represented team or department is required.');
      return;
    }
    if (isPair && !row.partner_name) {
      showError('Pair entries must include the second candidate.');
      return;
    }
    if (idxRaw === '') pendingCandidates.push(row);
    else pendingCandidates[Number(idxRaw)] = Object.assign({}, pendingCandidates[Number(idxRaw)], row);
    if (isPair) {
      pairEntries = pendingCandidates.map(function (c, i) {
        return {
          id: 'pair-' + i,
          label: '#' + c.number,
          partner_a_name: c.name,
          partner_b_name: c.partner_name,
        };
      });
    }
    $('#pageant-candidate-dialog').close();
    renderCandidates();
    showError('');
  }

  function openSegmentEditor(idx) {
    var dialog = $('#pageant-segment-dialog');
    if (!dialog) return;
    var seg = idx == null ? {
      name: '', weight: 10, max_score: 100, enabled: true,
      counts_toward_main_ranking: true, round_type: 'preliminary',
      criteria: [{ name: 'Overall', weight: 100, max_score: 100, description: '' }],
    } : cloneSegments([pageantSegments[idx]])[0];
    $('#pageant-seg-edit-idx').value = idx == null ? '' : String(idx);
    $('#pageant-seg-name').value = seg.name || '';
    $('#pageant-seg-weight').value = seg.weight || 0;
    $('#pageant-seg-round').value = seg.round_type || 'preliminary';
    $('#pageant-seg-main').checked = seg.counts_toward_main_ranking !== false;
    $('#pageant-seg-enabled').checked = seg.enabled !== false;
    renderCriteriaEditor(seg.criteria || []);
    dialog.showModal();
  }

  function renderCriteriaEditor(rows) {
    var body = $('#pageant-criteria-editor-body');
    if (!body) return;
    body.innerHTML = (rows || []).map(function (c, i) {
      return '<tr data-crit="' + i + '">' +
        '<td><input value="' + escapeAttr(c.name || '') + '" data-f="name"></td>' +
        '<td><input type="number" min="0" step="0.1" value="' + Number(c.weight || 0) + '" data-f="weight"></td>' +
        '<td><input type="number" min="0.1" step="0.1" value="' + Number(c.max_score || 100) + '" data-f="max_score"></td>' +
        '<td><input value="' + escapeAttr(c.description || '') + '" data-f="description"></td>' +
        '<td><button type="button" class="pageant-danger-button" data-crit-remove="' + i + '">Remove</button></td></tr>';
    }).join('');
    var total = (rows || []).reduce(function (s, c) { return s + Number(c.weight || 0); }, 0);
    var meter = $('#pageant-seg-criteria-meter');
    if (meter) {
      meter.textContent = 'Criteria Weight: ' + total + '% — ' +
        (Math.abs(total - 100) < 0.01 ? 'Complete' : (total < 100 ? 'Incomplete' : 'Over 100%'));
    }
  }

  function readCriteriaEditor() {
    return $$('#pageant-criteria-editor-body tr').map(function (tr) {
      return {
        id: uid('c'),
        name: (tr.querySelector('[data-f="name"]').value || '').trim(),
        weight: Number(tr.querySelector('[data-f="weight"]').value || 0),
        max_score: Number(tr.querySelector('[data-f="max_score"]').value || 0),
        description: (tr.querySelector('[data-f="description"]').value || '').trim(),
      };
    });
  }

  function saveSegmentFromForm() {
    var idxRaw = $('#pageant-seg-edit-idx').value;
    var criteria = readCriteriaEditor();
    var seg = {
      id: uid('s'),
      name: ($('#pageant-seg-name').value || '').trim(),
      weight: Number($('#pageant-seg-weight').value || 0),
      round_type: $('#pageant-seg-round').value || 'preliminary',
      counts_toward_main_ranking: $('#pageant-seg-main').checked,
      enabled: $('#pageant-seg-enabled').checked,
      criteria: criteria,
    };
    if (!seg.name) { showError('Segment name is required.'); return; }
    if (seg.weight <= 0) { showError('Segment weight must be greater than zero.'); return; }
    if (!criteria.length) { showError('Add at least one criterion.'); return; }
    if (idxRaw === '') pageantSegments.push(seg);
    else {
      seg.id = pageantSegments[Number(idxRaw)].id || seg.id;
      pageantSegments[Number(idxRaw)] = seg;
    }
    $('#pageant-segment-dialog').close();
    renderSegments();
    showError('');
  }

  function hydrateFromEvent(ev) {
    if (!ev || !ev.is_pageant) return;
    $('#special-event-type').value = 'pageant';
    applyPageantMode(true);
    var cfg = ev.pageant_config || {};
    var fmt = cfg.pageant_format || ev.pageant_format || 'individual';
    if (fmt === 'open') fmt = 'individual';
    var radio = $('input[name="pageant_format"][value="' + fmt + '"]');
    if (radio) radio.checked = true;
    competitionCategories = (cfg.competition_categories || ev.competition_categories || []).slice();
    if (!competitionCategories.length) recommendCategories(fmt);
    else renderCategories();
    if ($('#pageant-event-name')) $('#pageant-event-name').value = ev.name || '';
    if ($('#pageant-venue')) $('#pageant-venue').value = ev.venue || '';
    if ($('#pageant-start-date')) $('#pageant-start-date').value = ev.start_date || '';
    if ($('#pageant-end-date')) $('#pageant-end-date').value = ev.end_date || '';
    if ($('#pageant-start-time')) $('#pageant-start-time').value = ev.start_time || cfg.start_time || '';
    if ($('#pageant-end-time')) $('#pageant-end-time').value = ev.end_time || cfg.end_time || '';
    if ($('#pageant-classification')) $('#pageant-classification').value = ev.classification || 'major';
    if ($('#pageant-description')) $('#pageant-description').value = ev.description || cfg.description || '';
    if ($('#pageant-rules')) $('#pageant-rules').value = ev.rules_guidelines || cfg.rules || '';
    if ($('#pageant-faculty') && ev.faculty_account_id) $('#pageant-faculty').value = String(ev.faculty_account_id);
    if ($('#pageant-chief-judge') && ev.chief_judge_id) $('#pageant-chief-judge').value = String(ev.chief_judge_id);
    pendingCandidates = (cfg.pending_candidates || []).slice();
    // Also mark existing participant checkboxes
    (ev.participant_ids || []).forEach(function (id) {
      var box = $('#pageant-existing-candidates .participant-check[value="' + id + '"]');
      if (box) box.checked = true;
      // If not in pending, synthesize a display row from name list is optional
    });
    pairEntries = (cfg.pair_entries || []).slice();
    specialAwards = (cfg.special_awards || []).slice();
    advancementEnabled = !!cfg.advancement_enabled;
    $$('input[name="pageant_advancement"]').forEach(function (r) {
      r.checked = (r.value === 'yes') === advancementEnabled;
    });
    if (cfg.advancement) {
      if ($('#pageant-adv-topn')) $('#pageant-adv-topn').value = cfg.advancement.top_n || '';
      if ($('#pageant-adv-source')) $('#pageant-adv-source').value = cfg.advancement.source_segment || '';
      if ($('#pageant-adv-notes')) $('#pageant-adv-notes').value = cfg.advancement.notes || '';
    }
    pageantSegments = cloneSegments(ev.rounds_config || []);
    if (!pageantSegments.length) pageantSegments = cloneSegments(DEFAULT_SEGMENTS);
    (ev.judge_ids || []).forEach(function (id) {
      var box = $('#pageant-judge-options .judge-check[value="' + id + '"]');
      if (box) box.checked = true;
    });
    renderCandidates();
    renderSegments();
    renderAwards();
    syncAdvancementPanel();
    if (window.CriteriaWizard) window.CriteriaWizard.setStep(1);
  }

  // ── Event wiring ──────────────────────────────────────────────────────────
  form.querySelector('[name="category"]')?.addEventListener('change', function () {
    if (pageantMode && categoryValue() !== 'Special Event') {
      var nextValue = this.value;
      this.value = 'Special Event';
      requestModeChange('Switching categories exits the Pageant setup. Your Pageant data will stay in this draft until you save or publish.', function () {
        form.querySelector('[name="category"]').value = nextValue;
        syncSpecialTypeVisibility();
      });
      return;
    }
    syncSpecialTypeVisibility();
  });
  $('#special-event-type')?.addEventListener('change', function () {
    if (pageantMode && this.value !== 'pageant') {
      var nextValue = this.value;
      this.value = 'pageant';
      requestModeChange('Switching event types exits the Pageant setup. Your Pageant data will stay in this draft until you save or publish.', function () {
        $('#special-event-type').value = nextValue;
        applyPageantMode(isPageantSelected());
      });
      return;
    }
    applyPageantMode(isPageantSelected());
  });

  form.addEventListener('change', function (e) {
    if (e.target && e.target.name === 'pageant_format') {
      recommendCategories(e.target.value);
      syncPairFormVisibility();
      renderCandidates();
    }
    if (e.target && e.target.name === 'pageant_advancement') {
      advancementEnabled = e.target.value === 'yes';
      syncAdvancementPanel();
    }
    if (e.target && e.target.name === 'pageant_awards_choice') {
      var awardsPanel = $('#pageant-awards-panel');
      if (awardsPanel) awardsPanel.hidden = e.target.value !== 'yes';
    }
  });

  $('#pageant-categories-list')?.addEventListener('input', function (e) {
    if (e.target.classList.contains('pageant-category-input')) {
      var i = Number(e.target.dataset.idx);
      competitionCategories[i] = e.target.value;
    }
  });
  $('#pageant-categories-list')?.addEventListener('click', function (e) {
    var btn = e.target.closest('.pageant-cat-remove');
    if (!btn) return;
    competitionCategories.splice(Number(btn.dataset.idx), 1);
    renderCategories();
  });
  $('#pageant-add-category')?.addEventListener('click', function () {
    competitionCategories.push('New Category');
    renderCategories();
  });

  document.addEventListener('click', function (e) {
    if (!pageantMode) return;
    if (e.target.id === 'pageant-add-first' || e.target.id === 'pageant-add-candidate') {
      openCandidateForm(null);
      return;
    }
    var edit = e.target.closest('[data-cand-edit]');
    if (edit) { openCandidateForm(Number(edit.dataset.candEdit)); return; }
    var rem = e.target.closest('[data-cand-remove]');
    if (rem) {
      var index = Number(rem.dataset.candRemove);
      requestModeChange('Remove this candidate from the Pageant roster?', function () {
        pendingCandidates.splice(index, 1);
        renderCandidates();
      }, {
        title: 'Remove Pageant candidate?',
        cancelLabel: 'Keep candidate',
        acceptLabel: 'Remove candidate',
      });
      return;
    }
    if (e.target.id === 'pageant-use-template') {
      pageantSegments = cloneSegments(DEFAULT_SEGMENTS).map(function (seg, i) {
        seg.id = 's' + (i + 1);
        (seg.criteria || []).forEach(function (c, ci) { c.id = seg.id + 'c' + (ci + 1); });
        return seg;
      });
      renderSegments();
      return;
    }
    if (e.target.id === 'pageant-add-segment') { openSegmentEditor(null); return; }
    var se = e.target.closest('[data-seg-edit]');
    if (se) { openSegmentEditor(Number(se.dataset.segEdit)); return; }
    var sd = e.target.closest('[data-seg-dup]');
    if (sd) {
      var copy = cloneSegments([pageantSegments[Number(sd.dataset.segDup)]])[0];
      copy.id = uid('s');
      copy.name = (copy.name || 'Segment') + ' Copy';
      pageantSegments.splice(Number(sd.dataset.segDup) + 1, 0, copy);
      renderSegments();
      return;
    }
    var su = e.target.closest('[data-seg-up]');
    if (su) {
      var u = Number(su.dataset.segUp);
      if (u > 0) {
        var tmp = pageantSegments[u - 1];
        pageantSegments[u - 1] = pageantSegments[u];
        pageantSegments[u] = tmp;
        renderSegments();
      }
      return;
    }
    var sdown = e.target.closest('[data-seg-down]');
    if (sdown) {
      var d = Number(sdown.dataset.segDown);
      if (d < pageantSegments.length - 1) {
        var tmp2 = pageantSegments[d + 1];
        pageantSegments[d + 1] = pageantSegments[d];
        pageantSegments[d] = tmp2;
        renderSegments();
      }
      return;
    }
    var sr = e.target.closest('[data-seg-remove]');
    if (sr) {
      var segmentIndex = Number(sr.dataset.segRemove);
      requestModeChange('Remove this segment and all of its scoring criteria?', function () {
        pageantSegments.splice(segmentIndex, 1);
        renderSegments();
      }, {
        title: 'Remove Pageant segment?',
        cancelLabel: 'Keep segment',
        acceptLabel: 'Remove segment',
      });
      return;
    }
    var ar = e.target.closest('[data-award-remove]');
    if (ar) {
      specialAwards.splice(Number(ar.dataset.awardRemove), 1);
      renderAwards();
      return;
    }
    var goto = e.target.closest('[data-goto-step]');
    if (goto) { window.CriteriaWizard.setStep(Number(goto.dataset.gotoStep)); return; }
    if (e.target.classList.contains('pageant-award-preset')) {
      specialAwards.push({
        id: uid('a'),
        title: e.target.dataset.award,
        category: '',
        method: 'Highest score from a segment',
      });
      renderAwards();
    }
  });

  $('#pageant-cand-save')?.addEventListener('click', saveCandidateFromForm);
  $('#pageant-confirm-cancel')?.addEventListener('click', function () {
    pendingModeChange = null;
    $('#pageant-confirm-dialog').close();
  });
  $('#pageant-confirm-accept')?.addEventListener('click', function () {
    var accept = pendingModeChange;
    pendingModeChange = null;
    $('#pageant-confirm-dialog').close();
    if (accept) accept();
  });
  $('#pageant-cand-cancel')?.addEventListener('click', function () {
    $('#pageant-candidate-dialog')?.close();
  });
  document.getElementById('pageant-cand-cancel-2')?.addEventListener('click', function () {
    $('#pageant-candidate-dialog')?.close();
  });
  $('#pageant-seg-save')?.addEventListener('click', saveSegmentFromForm);
  $('#pageant-seg-cancel')?.addEventListener('click', function () {
    $('#pageant-segment-dialog')?.close();
  });
  document.getElementById('pageant-seg-cancel-2')?.addEventListener('click', function () {
    $('#pageant-segment-dialog')?.close();
  });
  $('#pageant-add-criterion-row')?.addEventListener('click', function () {
    var rows = readCriteriaEditor();
    rows.push({ name: '', weight: 0, max_score: 100, description: '' });
    renderCriteriaEditor(rows);
  });
  $('#pageant-criteria-editor-body')?.addEventListener('click', function (e) {
    var btn = e.target.closest('[data-crit-remove]');
    if (!btn) return;
    var rows = readCriteriaEditor();
    rows.splice(Number(btn.dataset.critRemove), 1);
    renderCriteriaEditor(rows);
  });
  $('#pageant-criteria-editor-body')?.addEventListener('input', function () {
    renderCriteriaEditor(readCriteriaEditor());
  });

  // Tab sections in step 4
  $$('[data-pageant-tab]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var tab = btn.dataset.pageantTab;
      $$('[data-pageant-tab]').forEach(function (b) { b.classList.toggle('is-active', b === btn); });
      $$('[data-pageant-tab-panel]').forEach(function (p) {
        p.hidden = p.dataset.pageantTabPanel !== tab;
      });
    });
  });

  // Hook fill after standard editor loads an event
  var origFill = window.__criteriaFillEditor;
  window.__criteriaHydratePageant = hydrateFromEvent;

  // Observe edit opens: monkey-patch by wrapping fillEditor if exposed
  document.addEventListener('criteria:event-loaded', function (ev) {
    if (ev.detail) hydrateFromEvent(ev.detail);
  });

  // Initial
  syncSpecialTypeVisibility();

  // Expose for criteriabasedevent.js integration
  window.PageantWizard = {
    isActive: function () { return pageantMode; },
    hydrate: hydrateFromEvent,
    sync: syncPageantHidden,
    validate: validatePageantStep,
    publishIssues: publishIssues,
    render: renderPageant,
  };
})();
