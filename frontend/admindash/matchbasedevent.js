(function () {
  'use strict';
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));
  const data = JSON.parse($('#match-events-data').textContent || '[]');
  const byId = Object.fromEntries(data.map(event => [String(event.id), event]));
  const wizard = $('#event-wizard');
  const form = $('#wizard-form');
  const error = $('#wizard-error');
  let step = 1;
  let matchPreview = [];
  let drawOrder = [];
  let legacyFormatPending = false;
  let pointsRows = defaultPointsRows();

  function defaultPointsRows() {
    return [
      { label: '1st Place', points: 15 },
      { label: '2nd Place', points: 10 },
      { label: '3rd Place', points: 7 },
      { label: '4th Place', points: 5 },
    ];
  }

  function ordinalLabel(n) {
    const mod100 = n % 100;
    if (mod100 >= 11 && mod100 <= 13) return `${n}th Place`;
    const mod10 = n % 10;
    if (mod10 === 1) return `${n}st Place`;
    if (mod10 === 2) return `${n}nd Place`;
    if (mod10 === 3) return `${n}rd Place`;
    return `${n}th Place`;
  }

  function syncPointsFromDom() {
    pointsRows = $$('#points-editor > div').map(row => {
      const inputs = $$('input', row);
      return {
        label: (inputs[0]?.value || '').trim(),
        points: Number(inputs[1]?.value) || 0,
      };
    });
  }

  function renderPointsEditor() {
    const editor = $('#points-editor');
    if (!editor) return;
    editor.innerHTML = pointsRows.map((row, index) => `
      <div data-points-index="${index}">
        <input value="${escapeHtml(row.label)}" aria-label="Rank label" data-points-label>
        <input type="number" min="0" value="${Number(row.points) || 0}" aria-label="Points" data-points-value>
        <button type="button" class="points-remove" data-remove-points ${pointsRows.length <= 1 ? 'disabled' : ''}>Remove</button>
      </div>
    `).join('');
    $$('#points-editor [data-points-label], #points-editor [data-points-value]').forEach(input => {
      input.addEventListener('input', syncPointsFromDom);
      input.addEventListener('change', syncPointsFromDom);
    });
    $$('#points-editor [data-remove-points]').forEach(button => {
      button.addEventListener('click', () => {
        syncPointsFromDom();
        if (pointsRows.length <= 1) return;
        const index = Number(button.closest('[data-points-index]')?.dataset.pointsIndex);
        if (Number.isNaN(index)) return;
        pointsRows.splice(index, 1);
        renderPointsEditor();
        updatePointsState();
      });
    });
    updatePointsState();
  }

  function addPointsRow() {
    syncPointsFromDom();
    pointsRows.push({ label: ordinalLabel(pointsRows.length + 1), points: 0 });
    renderPointsEditor();
  }

  function showStep(next) {
    step = Math.max(1, Math.min(5, next));
    $$('.wizard-panel').forEach(panel => panel.classList.toggle('active', Number(panel.dataset.step) === step));
    $$('[data-step-marker]').forEach(marker => {
      const value = Number(marker.dataset.stepMarker);
      marker.classList.toggle('active', value === step);
      marker.classList.toggle('done', value < step);
    });
    wizard.classList.toggle('at-review', step === 5);
    $('#wizard-back').style.visibility = step === 1 ? 'hidden' : 'visible';
    error.textContent = '';
    if (step === 4) syncThirdPlaceVisibility();
    if (step === 5) buildReview();
    $('.wizard-body').scrollTop = 0;
  }

  function selectedTeams() {
    return $$('.team-check:checked').map(input => ({ id: Number(input.value), name: input.dataset.name }));
  }

  function updateSelectedTeamCount() {
    const count = selectedTeams().length;
    $('#selected-team-count').textContent = `${count} selected`;
  }

  function invalidateBlueprint(message) {
    drawOrder = [];
    matchPreview = [];
    $('#draw-order').value = '[]';
    $('#bracket-preview').innerHTML = `<p>${escapeHtml(message)}</p>`;
    $('#schedule-preview').innerHTML = '<p>Generate the bracket first, then create its schedule.</p>';
  }

  function updatePointsState() {
    const enabled = form.elements.apply_championship_points.checked;
    $('#points-fieldset').classList.toggle('points-disabled', !enabled);
    $$('#points-editor input, #points-editor button').forEach(input => { input.disabled = !enabled; });
    if (enabled && pointsRows.length === 1) {
      const onlyRemove = $('#points-editor [data-remove-points]');
      if (onlyRemove) onlyRemove.disabled = true;
    }
  }

  function syncHiddenFields() {
    $('#team-ids').value = JSON.stringify(selectedTeams().map(team => team.id));
    $('#draw-order').value = JSON.stringify(drawOrder);
    syncPointsFromDom();
    $('#points-config').value = JSON.stringify(pointsRows);
    $('#schedule-rows').value = JSON.stringify(
      $$('#schedule-preview tbody tr').map(row => ({
        match_key: row.dataset.matchKey || '',
        date: $('[data-date]', row)?.value || row.dataset.date || '',
        time: $('[data-time]', row)?.value || row.dataset.time || '',
        venue: $('[data-venue]', row)?.value || row.dataset.venue || ''
      }))
    );
  }

  function validateStep() {
    error.textContent = '';
    if (step === 1) {
      const required = $$('[required]', $('.wizard-panel[data-step="1"]'));
      const invalid = required.find(field => !field.value.trim());
      if (invalid) { invalid.focus(); error.textContent = 'Complete every required basic information field.'; return false; }
      if (form.elements.end_date.value < form.elements.start_date.value) {
        form.elements.end_date.focus(); error.textContent = 'End Date cannot precede Start Date.'; return false;
      }
    }
    if (step === 2) {
      if (legacyFormatPending) { error.textContent = 'Choose a supported format explicitly before continuing.'; return false; }
      if (selectedTeams().length < 2) { error.textContent = 'Select at least two participating teams.'; return false; }
      if (!matchPreview.length || !drawOrder.length) { error.textContent = 'Generate the authoritative bracket preview before continuing.'; return false; }
    }
    if (step === 3) {
      if (!$('#schedule-preview tbody')) { error.textContent = 'Generate a schedule before continuing.'; return false; }
      const invalid = $$('input', $('#schedule-preview')).find(input => !input.value);
      if (invalid) { invalid.focus(); error.textContent = 'Complete every manual schedule field.'; return false; }
    }
    if (step === 4) {
      if (!form.elements.faculty_account.value) { form.elements.faculty_account.focus(); error.textContent = 'Select a Faculty In Charge.'; return false; }
      if (
        form.elements.tournament_type.value === 'single_elimination'
        && form.elements.include_third_place.checked
        && selectedTeams().length < 4
      ) {
        error.textContent = 'A Third Place Match requires at least four participating teams.'; return false;
      }
      syncPointsFromDom();
      if (form.elements.apply_championship_points.checked) {
        if (!pointsRows.length) {
          error.textContent = 'Add at least one championship points place.'; return false;
        }
        if (pointsRows.some(row => !row.label || row.points < 0 || Number.isNaN(row.points))) {
          error.textContent = 'Complete every championship points row with a label and non-negative value.'; return false;
        }
        const labels = {};
        for (const row of pointsRows) {
          const key = row.label.toLowerCase();
          if (labels[key]) { error.textContent = 'Championship point place labels must be unique.'; return false; }
          labels[key] = true;
        }
      }
    }
    return true;
  }

  function syncThirdPlaceVisibility() {
    const isSingle = form.elements.tournament_type.value === 'single_elimination';
    const wrap = $('#third-place-wrap');
    const hint = $('#third-place-hint');
    if (wrap) wrap.hidden = !isSingle;
    if (hint) hint.hidden = !isSingle;
    if (!isSingle && form.elements.include_third_place) {
      form.elements.include_third_place.checked = false;
    }
  }

  function renderBracket() {
    $('#bracket-preview').innerHTML = matchPreview.map(match =>
      `<span><strong>Game ${match.number}</strong> · ${escapeHtml(match.team_a)} vs ${escapeHtml(match.team_b)} <small>${escapeHtml(match.round)}</small></span>`
    ).join('');
  }

  async function refreshBlueprintWithCurrentDraw() {
    const teams = selectedTeams();
    if (teams.length < 2 || !drawOrder.length) {
      invalidateBlueprint('Third-place settings changed. Generate a new authoritative preview.');
      return;
    }
    try {
      const response = await fetch(window.MATCH_EVENT_PREVIEW_URL, {
        method: 'POST',
        credentials: 'same-origin',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': form.elements.csrfmiddlewaretoken.value
        },
        body: JSON.stringify({
          team_ids: teams.map(team => team.id),
          tournament_type: form.elements.tournament_type.value,
          include_third_place: form.elements.include_third_place.checked,
          draw_order: drawOrder
        })
      });
      const result = await response.json();
      if (!response.ok || !result.success) throw new Error(result.message || 'Unable to update bracket.');
      drawOrder = result.draw_order;
      matchPreview = result.matches;
      renderBracket();
      $('#schedule-preview').innerHTML = '<p>Bracket updated for Third Place settings. Generate the schedule again.</p>';
    } catch (exception) {
      invalidateBlueprint(exception.message || 'Third-place settings changed. Generate a new authoritative preview.');
    }
  }

  async function generateBracket() {
    const teams = selectedTeams();
    if (teams.length < 2) { error.textContent = 'Select at least two teams to generate a bracket.'; return; }
    if (legacyFormatPending) { error.textContent = 'Choose a supported format before generating a new bracket.'; return; }
    const format = form.elements.tournament_type.value;
    const button = $('#generate-bracket');
    button.disabled = true;
    button.textContent = 'Generating…';
    error.textContent = '';
    try {
      const response = await fetch(window.MATCH_EVENT_PREVIEW_URL, {
        method: 'POST',
        credentials: 'same-origin',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': form.elements.csrfmiddlewaretoken.value
        },
        body: JSON.stringify({
          team_ids: teams.map(team => team.id),
          tournament_type: format,
          include_third_place: form.elements.include_third_place.checked
        })
      });
      const result = await response.json();
      if (!response.ok || !result.success) throw new Error(result.message || 'Unable to generate bracket.');
      drawOrder = result.draw_order;
      matchPreview = result.matches;
      renderBracket();
      $('#schedule-preview').innerHTML = '<p>Authoritative bracket updated. Generate the schedule for these matchups.</p>';
    } catch (exception) {
      drawOrder = [];
      matchPreview = [];
      $('#bracket-preview').innerHTML = '<p>The bracket preview could not be generated.</p>';
      error.textContent = exception.message;
    } finally {
      button.disabled = false;
      button.textContent = 'Generate Bracket';
    }
  }

  function minutes(value) {
    const [hour, minute] = value.split(':').map(Number);
    return hour * 60 + minute;
  }

  function timeLabel(total) {
    return `${String(Math.floor(total / 60)).padStart(2, '0')}:${String(total % 60).padStart(2, '0')}`;
  }

  function addDays(iso, count) {
    const [year, month, day] = iso.split('-').map(Number);
    const date = new Date(year, month - 1, day);
    date.setDate(date.getDate() + count);
    return [
      date.getFullYear(),
      String(date.getMonth() + 1).padStart(2, '0'),
      String(date.getDate()).padStart(2, '0')
    ].join('-');
  }

  function generateSchedule() {
    if (!matchPreview.length) { error.textContent = 'Generate the bracket before scheduling matches.'; return; }
    const mode = form.elements.schedule_mode.value;
    const venue = form.elements.venue.value;
    const startDate = form.elements.start_date.value;
    const endDate = form.elements.end_date.value;
    const start = minutes(form.elements.daily_start_time.value || '08:00');
    const end = minutes(form.elements.daily_end_time.value || '17:00');
    if (mode === 'auto' && start >= end) { error.textContent = 'Daily end time must be later than daily start time.'; return; }
    let day = 0;
    let slot = start;
    const rows = matchPreview.map((match, index) => {
      if (mode === 'auto' && slot > end) { day += 1; slot = start; }
      const matchDate = mode === 'auto' ? addDays(startDate, day) : startDate;
      const matchTime = mode === 'auto' ? timeLabel(slot) : form.elements.daily_start_time.value || '08:00';
      slot += 60;
      return { match, number: match.number, date: matchDate, time: matchTime, venue };
    });
    if (rows.some(row => row.date > endDate)) { error.textContent = 'The date range and daily hours cannot fit every match.'; return; }
    const editable = mode === 'manual';
    $('#schedule-preview').innerHTML = `<table><thead><tr><th>Game</th><th>Matchup</th><th>Date</th><th>Time</th><th>Venue</th></tr></thead><tbody>${
      rows.map(row => `<tr data-match-key="${escapeHtml(row.match.key)}" data-date="${row.date}" data-time="${row.time}" data-venue="${escapeHtml(row.venue)}">
        <td>Game ${row.number}</td><td>${escapeHtml(row.match.team_a)} vs ${escapeHtml(row.match.team_b)}</td>
        <td>${editable ? `<input data-date type="date" value="${row.date}" min="${startDate}" max="${endDate}">` : row.date}</td>
        <td>${editable ? `<input data-time type="time" value="${row.time}">` : row.time}</td>
        <td>${editable ? `<input data-venue value="${escapeHtml(row.venue)}">` : escapeHtml(row.venue)}</td>
      </tr>`).join('')
    }</tbody></table>`;
    error.textContent = '';
  }

  function valueText(name) {
    const field = form.elements[name];
    if (!field) return '—';
    if (field instanceof RadioNodeList) {
      const selected = $$(`[name="${name}"]:checked`)[0];
      return selected?.closest('label')?.querySelector('strong')?.textContent || selected?.value || '—';
    }
    if (field.tagName === 'SELECT') return field.selectedOptions[0]?.text || '—';
    return field.value || '—';
  }

  function buildReview() {
    syncHiddenFields();
    const schedule = $('#schedule-preview tbody') ? `${matchPreview.length} generated matches · ${valueText('schedule_mode')}` : 'Not generated';
    const intendedStatus = $('#status-preview').selectedOptions[0].textContent;
    const points = pointsRows;
    const pointsSummary = form.elements.apply_championship_points.checked
      ? points.map(point => `${point.label}: ${point.points} points`).join(' · ')
      : 'Championship points disabled';
    const thirdPlace = form.elements.tournament_type.value === 'single_elimination'
      ? (form.elements.include_third_place.checked ? 'Enabled' : 'Disabled')
      : 'Not applicable';
    const rows = [
      ['Event Name', valueText('event_name')], ['Category', 'Sports'],
      ['Event Classification', valueText('event_classification')], ['Division', valueText('division')],
      ['Venue', valueText('venue')], ['Event Date Range', `${valueText('start_date')} to ${valueText('end_date')}`],
      ['Participating Teams', selectedTeams().map(team => team.name).join(', ')],
      ['Event Type', valueText('tournament_type')], ['Third Place Match', thirdPlace],
      ['Seeding Method', 'Random Draw'],
      ['Schedule', schedule], ['Faculty In Charge', valueText('faculty_account')],
      ['Championship Points', pointsSummary],
      ['Result Processing', 'Automatic (bracket updates, faculty confirmation, publication)'],
      ['Intended Status', intendedStatus]
    ];
    $('#review-summary').innerHTML = rows.map(row => `<div><span>${row[0]}</span><strong>${escapeHtml(row[1])}</strong></div>`).join('');
    updateFinalActionLabels();
  }

  function updateFinalActionLabels() {
    const intended = $('#status-preview').value;
    $('#save-draft-action').textContent = intended === 'draft' ? 'Save as Draft' : 'Save as Draft instead';
    $('#publish-action').textContent = intended === 'published' ? 'Publish Event' : 'Publish instead';
  }

  function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, character => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[character]));
  }

  function resetWizard() {
    form.reset();
    form.action = '/admin/events/match/';
    $('#wizard-title').textContent = 'Create match event';
    $('#team-ids').value = '[]';
    $('#draw-order').value = '[]';
    $('#schedule-rows').value = '[]';
    $('#points-config').value = '[]';
    $('#publication-status').value = 'draft';
    drawOrder = [];
    matchPreview = [];
    legacyFormatPending = false;
    pointsRows = defaultPointsRows();
    renderPointsEditor();
    $('#legacy-format-notice').hidden = true;
    $('#generate-bracket').disabled = false;
    $('#bracket-preview').innerHTML = '<p>Select teams, then generate a preview.</p>';
    $('#schedule-preview').innerHTML = '<p>Generate the bracket first, then create its schedule.</p>';
    updateSelectedTeamCount();
    updatePointsState();
    updateFinalActionLabels();
    syncThirdPlaceVisibility();
    showStep(1);
  }

  function openCreate() {
    resetWizard();
    wizard.showModal();
    form.elements.event_name.focus();
  }

  function fillEditor(event) {
    resetWizard();
    form.action = `/admin/events/match/${event.id}/edit/`;
    $('#wizard-title').textContent = `Edit ${event.name}`;
    form.elements.event_name.value = event.name;
    form.elements.event_classification.value = event.classification;
    form.elements.division.value = event.division;
    form.elements.venue.value = event.venue;
    form.elements.start_date.value = event.start_date;
    form.elements.end_date.value = event.end_date;
    $('#status-preview').value = event.publication_status;
    $('#publication-status').value = event.publication_status;
    const storedFormat = $(`[name="tournament_type"][value="${event.tournament_type}"]`);
    if (storedFormat) storedFormat.checked = true;
    legacyFormatPending = Boolean(event.legacy_double_elimination);
    $('#legacy-format-notice').hidden = !legacyFormatPending;
    $('#generate-bracket').disabled = legacyFormatPending;
    form.elements.include_third_place.checked = event.include_third_place;
    syncThirdPlaceVisibility();
    form.elements.schedule_mode.value = event.schedule_mode;
    form.elements.daily_start_time.value = event.daily_start_time || '08:00';
    form.elements.daily_end_time.value = event.daily_end_time || '17:00';
    form.elements.faculty_account.value = event.faculty_account_id || '';
    if (form.elements.scoresheet_template) {
      form.elements.scoresheet_template.value = event.scoresheet_template_id || '';
    }
    form.elements.apply_championship_points.checked = event.apply_championship_points;
    $$('.team-check').forEach(input => { input.checked = event.team_ids.includes(Number(input.value)); });
    pointsRows = (event.points_config && event.points_config.length)
      ? event.points_config.map(point => ({
          label: point.label || '',
          points: Number(point.points) || 0,
        }))
      : defaultPointsRows();
    renderPointsEditor();
    drawOrder = event.draw_order || [];
    $('#draw-order').value = JSON.stringify(drawOrder);
    matchPreview = event.schedule_rows.map(row => ({
      key: row.match_key,
      number: row.match_number,
      round: row.round_name,
      team_a: row.team_a,
      team_b: row.team_b
    }));
    const requiresRegeneration = event.schedule_rows.some(row => String(row.match_key).startsWith('legacy-'));
    if (requiresRegeneration && !legacyFormatPending) {
      invalidateBlueprint('This legacy bracket needs a new authoritative preview before it can be saved.');
    } else {
      renderBracket();
    }
    if (event.schedule_rows.length && !requiresRegeneration) {
      const editable = event.schedule_mode === 'manual';
      $('#schedule-preview').innerHTML = `<table><thead><tr><th>Game</th><th>Matchup</th><th>Date</th><th>Time</th><th>Venue</th></tr></thead><tbody>${
        event.schedule_rows.map(row => `<tr data-match-key="${escapeHtml(row.match_key)}" data-date="${row.date}" data-time="${row.time}" data-venue="${escapeHtml(row.venue)}"><td>Game ${row.match_number}</td><td>${escapeHtml(row.team_a)} vs ${escapeHtml(row.team_b)}</td><td>${editable ? `<input data-date type="date" value="${row.date}">` : row.date}</td><td>${editable ? `<input data-time type="time" value="${row.time}">` : row.time}</td><td>${editable ? `<input data-venue value="${escapeHtml(row.venue)}">` : escapeHtml(row.venue)}</td></tr>`).join('')
      }</tbody></table>`;
    }
    updateSelectedTeamCount();
    updatePointsState();
    updateFinalActionLabels();
    wizard.showModal();
  }

  function showSummary(event) {
    const rows = [
      ['Event', event.name], ['Status', event.publication_label], ['Classification', event.classification_label],
      ['Division', event.division], ['Venue', event.venue], ['Date range', `${event.start_date} to ${event.end_date}`],
      ['Teams', event.team_names.join(', ')], ['Format', event.tournament_type_label],
      ['Faculty In Charge', event.faculty_name || '—'], ['Schedule', `${event.schedule_rows.length} matches · ${event.schedule_mode}`]
    ];
    $('#view-summary').className = 'review-summary';
    $('#view-summary').innerHTML = rows.map(row => `<div><span>${row[0]}</span><strong>${escapeHtml(row[1])}</strong></div>`).join('');
    $('#view-event').showModal();
  }

  $('#open-wizard').addEventListener('click', openCreate);
  $('#close-wizard').addEventListener('click', () => wizard.close());
  $('#wizard-back').addEventListener('click', () => showStep(step - 1));
  $('#wizard-next').addEventListener('click', () => { if (validateStep()) showStep(step + 1); });
  $('#generate-bracket').addEventListener('click', generateBracket);
  $('#generate-schedule').addEventListener('click', generateSchedule);
  $('#team-search').addEventListener('input', event => {
    const query = event.target.value.toLowerCase();
    $$('.team-options label').forEach(label => { label.hidden = !label.dataset.teamSearch.includes(query); });
  });
  $$('.team-check').forEach(input => input.addEventListener('change', () => {
    updateSelectedTeamCount();
    invalidateBlueprint('Team selection changed. Generate a new authoritative preview.');
  }));
  $$('[name="tournament_type"]').forEach(input => input.addEventListener('change', () => {
    legacyFormatPending = false;
    $('#legacy-format-notice').hidden = true;
    $('#generate-bracket').disabled = false;
    syncThirdPlaceVisibility();
    invalidateBlueprint('Format changed. Generate a new authoritative preview.');
  }));
  form.elements.include_third_place.addEventListener('change', () => {
    refreshBlueprintWithCurrentDraw();
  });
  $$('[name="schedule_mode"]').forEach(input => input.addEventListener('change', () => {
    $('#auto-settings').hidden = form.elements.schedule_mode.value === 'manual';
    $('#schedule-preview').innerHTML = '<p>Schedule mode changed. Generate the schedule again.</p>';
  }));
  $('#status-preview').addEventListener('change', event => {
    $('#publication-status').value = event.target.value;
    updateFinalActionLabels();
  });
  form.elements.apply_championship_points.addEventListener('change', updatePointsState);
  $('#add-points-row').addEventListener('click', addPointsRow);
  renderPointsEditor();
  syncThirdPlaceVisibility();
  $('#change-legacy-format').addEventListener('click', () => {
    legacyFormatPending = false;
    $('#legacy-format-notice').hidden = true;
    $('#generate-bracket').disabled = false;
    $('[name="tournament_type"][value="double_elimination"]').checked = true;
    syncThirdPlaceVisibility();
    invalidateBlueprint('Double Elimination selected. Generate a new authoritative preview.');
  });
  $$('.final-action').forEach(button => button.addEventListener('click', () => {
    $('#publication-status').value = button.dataset.status;
    syncHiddenFields();
  }));
  form.addEventListener('submit', event => {
    if (!validateStep()) event.preventDefault();
    else syncHiddenFields();
  });
  $$('[data-edit]').forEach(button => button.addEventListener('click', () => fillEditor(byId[button.dataset.edit])));
  $$('[data-view]').forEach(button => button.addEventListener('click', () => showSummary(byId[button.dataset.view])));
  $('[data-close-view]').addEventListener('click', () => $('#view-event').close());
  $$('[data-delete]').forEach(button => button.addEventListener('click', () => {
    $('#delete-name').textContent = button.dataset.name;
    $('#delete-form').action = `/admin/events/match/${button.dataset.delete}/delete/`;
    $('#delete-event').showModal();
  }));
  $('[data-cancel-delete]').addEventListener('click', () => $('#delete-event').close());
  wizard.addEventListener('cancel', event => { event.preventDefault(); wizard.close(); });
  if (window.MATCH_EVENT_EDIT_ID && byId[window.MATCH_EVENT_EDIT_ID]) fillEditor(byId[window.MATCH_EVENT_EDIT_ID]);
  showStep(1);
}());
