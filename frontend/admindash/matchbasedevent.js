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
  let actualMatchCount = 0;
  let automaticAdvanceCount = 0;
  let legacyFormatPending = false;
  let pointsRows = defaultPointsFor('major');
  let pendingBracketAction = null;
  let eventNameManual = false;
  let editingEventId = null;
  const seasonNames = Array.isArray(window.MATCH_EVENT_SEASON_NAMES)
    ? window.MATCH_EVENT_SEASON_NAMES.map(name => String(name).trim().toLowerCase())
    : [];

  const RESULT_BY_SPORT = {
    Basketball: 'team_final_score',
    Volleyball: 'best_of_sets',
    Badminton: 'best_of_games',
    'Table Tennis': 'best_of_games',
    Chess: 'win_draw_loss',
    'Sepak Takraw': 'best_of_sets',
    Esports: 'maps_rounds_custom',
    Custom: 'manual_winner'
  };

  function defaultPointsFor(classification) {
    if (classification === 'minor') {
      return [
        { label: '1st Place', points: 50 },
        { label: '2nd Place', points: 35 },
        { label: '3rd Place', points: 25 },
        { label: '4th Place', points: 15 }
      ];
    }
    return [
      { label: '1st Place', points: 100 },
      { label: '2nd Place', points: 75 },
      { label: '3rd Place', points: 50 },
      { label: '4th Place', points: 25 }
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

  function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, character => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[character]));
  }

  function syncPointsFromDom() {
    pointsRows = $$('#points-editor > div').map(row => {
      const inputs = $$('input', row);
      return {
        label: (inputs[0]?.value || '').trim(),
        points: Number(inputs[1]?.value) || 0
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

  function selectedTeams() {
    return $$('.team-check:checked').map(input => ({ id: Number(input.value), name: input.dataset.name }));
  }

  function pairingMethod() {
    return form.elements.pairing_method?.value || 'random_draw';
  }

  function minParticipants() {
    const format = form.elements.tournament_type.value;
    if (format === 'single_elimination' && form.elements.include_third_place.checked) return 4;
    return 2;
  }

  function updateSelectedTeamCount() {
    const count = selectedTeams().length;
    const min = minParticipants();
    $('#selected-team-count').textContent = `${count} selected · min ${min}`;
    const hint = $('#min-participants-hint');
    if (hint) hint.textContent = `Select at least ${min} participants for the chosen tournament format.`;
  }

  function invalidateBlueprint(message) {
    drawOrder = [];
    matchPreview = [];
    actualMatchCount = 0;
    automaticAdvanceCount = 0;
    $('#draw-order').value = '[]';
    $('#bracket-preview').innerHTML = `<p>${escapeHtml(message)}</p>`;
    $('#bracket-stats').hidden = true;
    $('#schedule-preview').innerHTML = '<p>Generate the bracket first, then create its schedule.</p>';
    const conflicts = $('#schedule-conflicts');
    if (conflicts) { conflicts.hidden = true; conflicts.textContent = ''; }
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

  function syncSportCustom() {
    const isCustom = form.elements.sport_type.value === 'Custom';
    const wrap = $('#sport-custom-wrap');
    if (wrap) wrap.hidden = !isCustom;
    if (!isCustom) form.elements.sport_custom_name.value = '';
  }

  function sportLabel() {
    const sport = form.elements.sport_type.value;
    if (sport === 'Custom') return (form.elements.sport_custom_name.value || '').trim();
    return (sport || '').trim();
  }

  function buildEventName(sport, division) {
    const sportName = (sport || '').trim();
    const div = (division || '').trim();
    if (!sportName || !div) return '';
    if (div === 'Men' || div === 'Women') return `${div}'s ${sportName}`;
    if (div === 'Open') return `${sportName} – Open Division`;
    if (div === 'Mixed') return `${sportName} – Mixed Division`;
    return `${sportName} – ${div} Division`;
  }

  function isEventNameTaken(name) {
    const key = String(name || '').trim().toLowerCase();
    if (!key) return false;
    const current = editingEventId ? byId[String(editingEventId)] : null;
    return seasonNames.some(existing => {
      if (existing !== key) return false;
      if (current && String(current.name || '').trim().toLowerCase() === key) return false;
      return true;
    });
  }

  function syncGeneratedEventName(force = false) {
    const generated = buildEventName(sportLabel(), form.elements.division.value);
    const nameField = form.elements.event_name;
    if (!generated) return generated;
    if (force || !eventNameManual) {
      nameField.value = generated;
    }
    updateEventNameHint(generated);
    return generated;
  }

  function updateEventNameHint(generated) {
    const hint = $('#event-name-hint');
    if (!hint) return;
    const season = window.MATCH_EVENT_ACADEMIC_YEAR || 'the current intramurals season';
    const taken = isEventNameTaken(form.elements.event_name.value);
    let text = `Auto-generated from Sport/Game Type and Division (for example, Men’s Basketball or Chess – Open Division). Edit anytime for a custom title. Must be unique in ${season}.`;
    if (eventNameManual && generated && form.elements.event_name.value.trim() !== generated) {
      text = `Custom title in use. Suggested name: ${generated}. Must be unique in ${season}.`;
    }
    if (taken) {
      text = `This Event Name already exists in ${season}. Choose a unique title.`;
    }
    hint.textContent = text;
    hint.classList.toggle('field-hint--error', taken);
  }

  function recommendResultFormat() {
    const sport = form.elements.sport_type.value;
    const recommended = RESULT_BY_SPORT[sport];
    if (recommended && form.elements.result_entry_format) {
      form.elements.result_entry_format.value = recommended;
    }
  }

  function syncPairingUi() {
    const method = pairingMethod();
    const editor = $('#seed-editor');
    const label = $('#pairing-summary-label');
    if (label) {
      label.textContent = method === 'seeded_draw'
        ? 'Seeded Draw'
        : method === 'manual_pairing'
          ? 'Manual Pairing'
          : 'Random Draw';
    }
    if (editor) editor.hidden = method === 'random_draw';
    renderSeedList();
  }

  function renderSeedList() {
    const list = $('#seed-list');
    if (!list) return;
    const teams = selectedTeams();
    const ordered = drawOrder.length
      ? drawOrder.map(id => teams.find(team => team.id === id)).filter(Boolean)
      : teams;
    list.innerHTML = ordered.map((team, index) => `
      <li data-team-id="${team.id}">
        <span class="seed-badge">Seed ${index + 1}</span>
        <strong>${escapeHtml(team.name)}</strong>
        <span class="seed-move">
          <button type="button" data-seed-up ${index === 0 ? 'disabled' : ''}>↑</button>
          <button type="button" data-seed-down ${index === ordered.length - 1 ? 'disabled' : ''}>↓</button>
        </span>
      </li>
    `).join('');
    $$('[data-seed-up]', list).forEach(button => button.addEventListener('click', () => moveSeed(button, -1)));
    $$('[data-seed-down]', list).forEach(button => button.addEventListener('click', () => moveSeed(button, 1)));
    drawOrder = ordered.map(team => team.id);
    $('#draw-order').value = JSON.stringify(drawOrder);
  }

  function moveSeed(button, delta) {
    const item = button.closest('li');
    const id = Number(item.dataset.teamId);
    const index = drawOrder.indexOf(id);
    const next = index + delta;
    if (index < 0 || next < 0 || next >= drawOrder.length) return;
    const copy = drawOrder.slice();
    [copy[index], copy[next]] = [copy[next], copy[index]];
    drawOrder = copy;
    renderSeedList();
    invalidateBlueprint('Seed order changed. Generate a new authoritative preview.');
  }

  function syncPlayingAreas() {
    const count = Math.max(1, Number(form.elements.playing_area_count?.value || 1));
    const editor = $('#playing-areas-editor');
    if (!editor) return;
    const existing = $$('input[data-playing-area]', editor).map(input => input.value);
    editor.innerHTML = Array.from({ length: count }, (_, index) => `
      <label>Playing Area ${index + 1}
        <input data-playing-area value="${escapeHtml(existing[index] || `Court ${index + 1}`)}">
      </label>
    `).join('');
    $$('input[data-playing-area]', editor).forEach(input => {
      input.addEventListener('input', () => {
        $('#playing-areas').value = JSON.stringify(
          $$('input[data-playing-area]', editor).map(field => field.value.trim()).filter(Boolean)
        );
      });
    });
    $('#playing-areas').value = JSON.stringify(
      $$('input[data-playing-area]', editor).map(field => field.value.trim()).filter(Boolean)
    );
  }

  function syncTieBreakHidden() {
    const selected = $$('input[name="tie_break_option"]:checked').map(input => input.value);
    $('#tie-break-rules').value = JSON.stringify(selected);
  }

  function syncHiddenFields() {
    $('#team-ids').value = JSON.stringify(selectedTeams().map(team => team.id));
    if (pairingMethod() !== 'random_draw') renderSeedList();
    $('#draw-order').value = JSON.stringify(drawOrder);
    syncPointsFromDom();
    $('#points-config').value = JSON.stringify(pointsRows);
    syncTieBreakHidden();
    syncPlayingAreas();
    if (form.elements.scoresheet_template_mode?.value === 'auto') {
      form.elements.scoresheet_template.value = '';
    }
    $('#schedule-rows').value = JSON.stringify(
      $$('#schedule-preview tbody tr').filter(row => row.dataset.advance !== '1').map(row => ({
        match_key: row.dataset.matchKey || '',
        date: $('[data-date]', row)?.value || row.dataset.date || '',
        time: $('[data-time]', row)?.value || row.dataset.time || '',
        venue: $('[data-venue]', row)?.value || row.dataset.venue || '',
        playing_area: $('[data-area]', row)?.value || row.dataset.area || ''
      }))
    );
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
    updateSelectedTeamCount();
  }

  function renderBracket() {
    const stats = $('#bracket-stats');
    if (stats) {
      stats.hidden = !matchPreview.length;
      stats.innerHTML = `
        <span class="status-badge status-badge--scheduled">${actualMatchCount} actual matches</span>
        <span class="status-badge status-badge--advance">${automaticAdvanceCount} automatic advances</span>
      `;
    }
    $('#bracket-preview').innerHTML = matchPreview.map(match => {
      if (match.is_automatic_advance) {
        return `<span class="advance-card" title="${escapeHtml(match.tooltip || 'Automatic Advance (BYE)')}">
          <strong>${escapeHtml(match.team_a)}</strong> — Automatic Advance
          <small>${escapeHtml(match.round)} · BYE</small>
        </span>`;
      }
      const label = match.display_label || `${match.team_a} vs ${match.team_b}`;
      return `<span class="match-card"><strong>Game ${match.number}</strong> · ${escapeHtml(label)} <small>${escapeHtml(match.round)}</small></span>`;
    }).join('') || '<p>Select teams, then generate a preview.</p>';
  }

  async function requestBlueprint(reuseDraw) {
    const teams = selectedTeams();
    if (teams.length < minParticipants()) {
      throw new Error(`Select at least ${minParticipants()} participating teams.`);
    }
    if (legacyFormatPending) {
      throw new Error('Choose a supported format before generating a new bracket.');
    }
    const body = {
      team_ids: teams.map(team => team.id),
      tournament_type: form.elements.tournament_type.value,
      include_third_place: form.elements.include_third_place.checked,
      pairing_method: pairingMethod()
    };
    if (reuseDraw && drawOrder.length) body.draw_order = drawOrder;
    else if (pairingMethod() !== 'random_draw' && drawOrder.length === teams.length) body.draw_order = drawOrder;

    const response = await fetch(window.MATCH_EVENT_PREVIEW_URL, {
      method: 'POST',
      credentials: 'same-origin',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': form.elements.csrfmiddlewaretoken.value
      },
      body: JSON.stringify(body)
    });
    const result = await response.json();
    if (!response.ok || !result.success) throw new Error(result.message || 'Unable to generate bracket.');
    drawOrder = result.draw_order;
    matchPreview = result.matches;
    actualMatchCount = result.actual_match_count ?? matchPreview.filter(match => !match.is_automatic_advance).length;
    automaticAdvanceCount = result.automatic_advance_count ?? matchPreview.filter(match => match.is_automatic_advance).length;
    $('#draw-order').value = JSON.stringify(drawOrder);
    renderBracket();
    renderSeedList();
    $('#schedule-preview').innerHTML = '<p>Authoritative bracket updated. Generate the schedule for these matchups.</p>';
  }

  async function generateBracket() {
    const button = $('#generate-bracket');
    button.disabled = true;
    button.textContent = 'Generating…';
    error.textContent = '';
    try {
      await requestBlueprint(false);
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

  function confirmBracketAction(action, message) {
    pendingBracketAction = action;
    $('#bracket-confirm-message').textContent = message;
    $('#bracket-confirm').showModal();
  }

  async function regenerateBracket() {
    if (!matchPreview.length) return generateBracket();
    confirmBracketAction('regenerate', 'Regenerate the bracket? Unconfirmed matches will be replaced. Confirmed results stay unless you perform a controlled reset.');
  }

  function resetBracket() {
    confirmBracketAction('reset', 'Reset the bracket preview? This clears generated matches and schedule rows in the wizard.');
  }

  function minutes(value) {
    const [hour, minute] = String(value || '00:00').split(':').map(Number);
    return hour * 60 + minute;
  }

  function timeLabel(total) {
    const normalized = ((total % (24 * 60)) + (24 * 60)) % (24 * 60);
    return `${String(Math.floor(normalized / 60)).padStart(2, '0')}:${String(normalized % 60).padStart(2, '0')}`;
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
    const actual = matchPreview.filter(match => !match.is_automatic_advance);
    if (!actual.length) { error.textContent = 'Generate the bracket before scheduling matches.'; return; }
    const mode = form.elements.schedule_mode.value;
    const venue = form.elements.default_schedule_venue?.value || form.elements.venue.value;
    const startDate = form.elements.first_match_date?.value || form.elements.start_date.value;
    const endDate = form.elements.end_date.value;
    const start = minutes(form.elements.daily_start_time.value || '08:00');
    const end = minutes(form.elements.daily_end_time.value || '17:00');
    const duration = Number(form.elements.match_duration_minutes?.value || 60);
    const breakMins = Number(form.elements.break_between_matches_minutes?.value || 10);
    const areas = JSON.parse($('#playing-areas').value || '[]');
    const courts = areas.length ? areas : [venue || 'Court 1'];
    if (mode === 'auto' && start >= end) { error.textContent = 'Daily end time must be later than daily start time.'; return; }

    let day = 0;
    let slot = start;
    let courtIndex = 0;
    const rows = actual.map(match => {
      if (mode === 'auto' && slot + duration > end) { day += 1; slot = start; courtIndex = 0; }
      const matchDate = mode === 'auto' ? addDays(startDate, day) : startDate;
      const matchTime = mode === 'auto' ? timeLabel(slot) : form.elements.daily_start_time.value || '08:00';
      const area = courts[courtIndex % courts.length];
      courtIndex += 1;
      if (mode === 'auto' && courtIndex % courts.length === 0) slot += duration + breakMins;
      return {
        match,
        number: match.number,
        date: matchDate,
        time: matchTime,
        venue,
        area,
        label: match.display_label || `${match.team_a} vs ${match.team_b}`
      };
    });
    if (rows.some(row => row.date > endDate)) {
      error.textContent = 'The date range and daily hours cannot fit every match.';
      return;
    }
    const editable = mode === 'manual';
    const advanceNote = automaticAdvanceCount
      ? `<p class="field-hint">${automaticAdvanceCount} Automatic Advance slot(s) excluded from the schedule.</p>`
      : '';
    $('#schedule-preview').innerHTML = `${advanceNote}<table><thead><tr><th>Game</th><th>Matchup</th><th>Date</th><th>Time</th><th>Venue</th><th>Playing Area</th></tr></thead><tbody>${
      rows.map(row => `<tr data-match-key="${escapeHtml(row.match.key)}" data-date="${row.date}" data-time="${row.time}" data-venue="${escapeHtml(row.venue)}" data-area="${escapeHtml(row.area)}">
        <td>Game ${row.number}</td>
        <td>${escapeHtml(row.label)}</td>
        <td>${editable ? `<input data-date type="date" value="${row.date}" min="${form.elements.start_date.value}" max="${endDate}">` : row.date}</td>
        <td>${editable ? `<input data-time type="time" value="${row.time}">` : row.time}</td>
        <td>${editable ? `<input data-venue value="${escapeHtml(row.venue)}">` : escapeHtml(row.venue)}</td>
        <td>${editable ? `<input data-area value="${escapeHtml(row.area)}">` : escapeHtml(row.area)}</td>
      </tr>`).join('')
    }</tbody></table>`;
    $('#schedule-conflicts').hidden = true;
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

  function collectPublishGaps() {
    const gaps = [];
    if (!form.elements.sport_type.value) gaps.push('Sport/Game Type');
    if (form.elements.sport_type.value === 'Custom' && !form.elements.sport_custom_name.value.trim()) gaps.push('Custom Sport/Game Name');
    if (!form.elements.division.value) gaps.push('Division');
    if (!form.elements.event_name.value.trim()) gaps.push('Event Name');
    else if (isEventNameTaken(form.elements.event_name.value)) gaps.push('Unique Event Name for this season');
    if (!form.elements.event_classification.value) gaps.push('Event Classification');
    if (!form.elements.venue.value.trim()) gaps.push('Venue');
    if (!form.elements.start_date.value || !form.elements.end_date.value) gaps.push('Event dates');
    if (form.elements.end_date.value < form.elements.start_date.value) gaps.push('Valid date range');
    if (selectedTeams().length < minParticipants()) gaps.push(`At least ${minParticipants()} participants`);
    if (!matchPreview.length) gaps.push('Generated bracket');
    if (!$('#schedule-preview tbody')) gaps.push('Generated schedule');
    if (!form.elements.faculty_account.value) gaps.push('Faculty In Charge');
    if (!form.elements.result_entry_format.value) gaps.push('Result Entry Format');
    syncPointsFromDom();
    if (form.elements.apply_championship_points.checked && !pointsRows.length) gaps.push('Championship points');
    return gaps;
  }

  function buildReview() {
    syncHiddenFields();
    const gaps = collectPublishGaps();
    const req = $('#review-requirements');
    if (req) {
      req.innerHTML = gaps.length
        ? `<strong>Remaining requirements before publish</strong><ul>${gaps.map(item => `<li>${escapeHtml(item)}</li>`).join('')}</ul>`
        : '<strong>Ready to publish</strong><p>All required validations currently pass.</p>';
      req.className = `review-requirements ${gaps.length ? 'is-incomplete' : 'is-ready'}`;
    }
    const actual = matchPreview.filter(match => !match.is_automatic_advance);
    const advances = matchPreview.filter(match => match.is_automatic_advance);
    const unresolved = actual.filter(match => !match.team_a_id || !match.team_b_id || match.team_a === 'TBD' || String(match.team_b).startsWith('Winner') || String(match.team_b).startsWith('Loser') || String(match.team_a).startsWith('Winner'));
    const sport = form.elements.sport_type.value === 'Custom'
      ? form.elements.sport_custom_name.value || 'Custom'
      : valueText('sport_type');
    const rows = [
      ['Event Name', valueText('event_name')],
      ['Sport/Game Type', sport],
      ['Category', 'Sports'],
      ['Event Classification', valueText('event_classification')],
      ['Division', valueText('division')],
      ['Venue', valueText('venue')],
      ['Event Date Range', `${valueText('start_date')} to ${valueText('end_date')}`],
      ['Selected Participants', selectedTeams().map(team => team.name).join(', ') || '—'],
      ['Tournament Format', valueText('tournament_type')],
      ['Pairing Method', valueText('pairing_method')],
      ['Actual Matches', String(actualMatchCount || actual.length)],
      ['Automatic Advances', String(automaticAdvanceCount || advances.length)],
      ['Unresolved / Pending Matchups', unresolved.length ? unresolved.map(match => match.display_label || `${match.team_a} vs ${match.team_b}`).join(' · ') : 'None'],
      ['Schedule', $('#schedule-preview tbody') ? `${actual.length} scheduled actual matches · ${valueText('schedule_mode')}` : 'Not generated'],
      ['Faculty In Charge', valueText('faculty_account')],
      ['Assigned Scorer', valueText('assigned_scorer')],
      ['Result Entry Format', valueText('result_entry_format')],
      ['Scoresheet Template', form.elements.scoresheet_template_mode?.value === 'auto' ? 'Auto by Sport/Game Type' : valueText('scoresheet_template')],
      ['Championship Points', form.elements.apply_championship_points.checked
        ? pointsRows.map(point => `${point.label}: ${point.points}`).join(' · ')
        : 'Disabled'],
      ['Tie-Breaking Rules', $$('input[name="tie_break_option"]:checked').map(input => input.parentElement.textContent.trim()).join(' · ') || '—']
    ];
    $('#review-summary').innerHTML = rows.map(row => `<div><span>${row[0]}</span><strong>${escapeHtml(row[1])}</strong></div>`).join('');
    $('#publish-action').disabled = gaps.length > 0;
  }

  function validateStep() {
    error.textContent = '';
    if (step === 1) {
      const required = $$('[required]', $('.wizard-panel[data-step="1"]'));
      const invalid = required.find(field => !field.value.trim());
      if (invalid) { invalid.focus(); error.textContent = 'Complete every required basic information field.'; return false; }
      if (form.elements.sport_type.value === 'Custom' && !form.elements.sport_custom_name.value.trim()) {
        form.elements.sport_custom_name.focus();
        error.textContent = 'Enter a custom sport or game name.';
        return false;
      }
      if (!form.elements.event_name.value.trim()) {
        syncGeneratedEventName(true);
      }
      if (!form.elements.event_name.value.trim()) {
        form.elements.event_name.focus();
        error.textContent = 'Event Name is required. Select Sport/Game Type and Division, or enter a custom title.';
        return false;
      }
      if (isEventNameTaken(form.elements.event_name.value)) {
        form.elements.event_name.focus();
        error.textContent = 'Event Name must be unique within the current intramurals season.';
        return false;
      }
      if (form.elements.end_date.value < form.elements.start_date.value) {
        form.elements.end_date.focus();
        error.textContent = 'End Date cannot precede Start Date.';
        return false;
      }
    }
    if (step === 2) {
      if (legacyFormatPending) { error.textContent = 'Choose a supported format explicitly before continuing.'; return false; }
      if (selectedTeams().length < minParticipants()) {
        error.textContent = `Select at least ${minParticipants()} participating teams.`;
        return false;
      }
      if (!matchPreview.length || !drawOrder.length) {
        error.textContent = 'Generate the authoritative bracket preview before continuing.';
        return false;
      }
    }
    if (step === 3) {
      if (!$('#schedule-preview tbody')) { error.textContent = 'Generate a schedule before continuing.'; return false; }
      const invalid = $$('input', $('#schedule-preview')).find(input => !input.value);
      if (invalid) { invalid.focus(); error.textContent = 'Complete every manual schedule field.'; return false; }
    }
    if (step === 4) {
      if (!form.elements.faculty_account.value) {
        form.elements.faculty_account.focus();
        error.textContent = 'Select a Faculty In Charge.';
        return false;
      }
      if (!form.elements.result_entry_format.value) {
        error.textContent = 'Select a result entry format.';
        return false;
      }
      syncPointsFromDom();
      if (form.elements.apply_championship_points.checked) {
        if (!pointsRows.length) {
          error.textContent = 'Add at least one championship points place.';
          return false;
        }
        if (pointsRows.some(row => !row.label || row.points < 0 || Number.isNaN(row.points))) {
          error.textContent = 'Complete every championship points row with a label and non-negative value.';
          return false;
        }
      }
    }
    return true;
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
    if (step === 2) syncThirdPlaceVisibility();
    if (step === 3) syncPlayingAreas();
    if (step === 5) buildReview();
    $('.wizard-body').scrollTop = 0;
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
    actualMatchCount = 0;
    automaticAdvanceCount = 0;
    legacyFormatPending = false;
    eventNameManual = false;
    editingEventId = null;
    pointsRows = defaultPointsFor('major');
    renderPointsEditor();
    $('#legacy-format-notice').hidden = true;
    $('#generate-bracket').disabled = false;
    $('#bracket-preview').innerHTML = '<p>Select teams, then generate a preview.</p>';
    $('#schedule-preview').innerHTML = '<p>Generate the bracket first, then create its schedule.</p>';
    $('#publish-action').disabled = false;
    updateSelectedTeamCount();
    updatePointsState();
    syncThirdPlaceVisibility();
    syncSportCustom();
    syncPairingUi();
    syncPlayingAreas();
    syncGeneratedEventName(true);
    showStep(1);
  }

  function openCreate() {
    resetWizard();
    wizard.showModal();
    form.elements.sport_type.focus();
  }

  function fillEditor(event) {
    resetWizard();
    editingEventId = event.id;
    form.action = `/admin/events/match/${event.id}/edit/`;
    $('#wizard-title').textContent = `Edit ${event.name}`;
    if (form.elements.sport_type) form.elements.sport_type.value = event.sport_type || '';
    if (form.elements.sport_custom_name) form.elements.sport_custom_name.value = event.sport_custom_name || '';
    syncSportCustom();
    form.elements.event_classification.value = event.classification;
    form.elements.division.value = event.division;
    const suggested = buildEventName(sportLabel(), event.division);
    form.elements.event_name.value = event.name;
    eventNameManual = Boolean(event.name && suggested && event.name !== suggested);
    updateEventNameHint(suggested);
    form.elements.venue.value = event.venue;
    form.elements.start_date.value = event.start_date;
    form.elements.end_date.value = event.end_date;
    $('#publication-status').value = event.publication_status;
    const storedFormat = $(`[name="tournament_type"][value="${event.tournament_type}"]`);
    if (storedFormat) storedFormat.checked = true;
    const pairing = $(`[name="pairing_method"][value="${event.pairing_method || 'random_draw'}"]`);
    if (pairing) pairing.checked = true;
    legacyFormatPending = Boolean(event.legacy_double_elimination);
    $('#legacy-format-notice').hidden = !legacyFormatPending;
    $('#generate-bracket').disabled = legacyFormatPending;
    form.elements.include_third_place.checked = event.include_third_place;
    syncThirdPlaceVisibility();
    form.elements.schedule_mode.value = event.schedule_mode;
    form.elements.daily_start_time.value = event.daily_start_time || '08:00';
    form.elements.daily_end_time.value = event.daily_end_time || '17:00';
    const cfg = event.schedule_config || {};
    if (form.elements.match_duration_minutes) form.elements.match_duration_minutes.value = cfg.match_duration_minutes || 60;
    if (form.elements.break_between_matches_minutes) form.elements.break_between_matches_minutes.value = cfg.break_between_matches_minutes || 10;
    if (form.elements.min_rest_minutes) form.elements.min_rest_minutes.value = cfg.min_rest_minutes || 30;
    if (form.elements.playing_area_count) form.elements.playing_area_count.value = cfg.playing_area_count || (cfg.playing_areas || []).length || 1;
    if (form.elements.first_match_date) form.elements.first_match_date.value = cfg.first_match_date || event.start_date;
    syncPlayingAreas();
    if (Array.isArray(cfg.playing_areas) && cfg.playing_areas.length) {
      $$('#playing-areas-editor input[data-playing-area]').forEach((input, index) => {
        if (cfg.playing_areas[index]) input.value = cfg.playing_areas[index];
      });
      syncPlayingAreas();
    }
    form.elements.faculty_account.value = event.faculty_account_id || '';
    if (form.elements.assigned_scorer) form.elements.assigned_scorer.value = event.assigned_scorer_id || '';
    if (form.elements.result_entry_format) form.elements.result_entry_format.value = event.result_entry_format || RESULT_BY_SPORT[event.sport_type] || 'manual_winner';
    if (form.elements.scoresheet_template) {
      form.elements.scoresheet_template.value = event.scoresheet_template_id || '';
      if (form.elements.scoresheet_template_mode) {
        form.elements.scoresheet_template_mode.value = event.scoresheet_template_id ? 'existing' : 'auto';
      }
    }
    form.elements.apply_championship_points.checked = event.apply_championship_points;
    $$('input[name="tie_break_option"]').forEach(input => {
      input.checked = (event.tie_break_rules || []).includes(input.value);
    });
    $$('.team-check').forEach(input => { input.checked = event.team_ids.includes(Number(input.value)); });
    pointsRows = (event.points_config && event.points_config.length)
      ? event.points_config.map(point => ({ label: point.label || '', points: Number(point.points) || 0 }))
      : defaultPointsFor(event.classification);
    renderPointsEditor();
    drawOrder = event.draw_order || [];
    $('#draw-order').value = JSON.stringify(drawOrder);
    matchPreview = (event.schedule_rows || []).map(row => ({
      key: row.match_key,
      number: row.match_number,
      round: row.round_name,
      team_a: row.team_a,
      team_b: row.team_b,
      is_automatic_advance: row.is_automatic_advance,
      display_label: row.dependency_label || `${row.team_a} vs ${row.team_b}`
    }));
    actualMatchCount = event.actual_match_count ?? matchPreview.filter(match => !match.is_automatic_advance).length;
    automaticAdvanceCount = event.automatic_advance_count ?? matchPreview.filter(match => match.is_automatic_advance).length;
    const requiresRegeneration = event.schedule_rows.some(row => String(row.match_key).startsWith('legacy-'));
    if (requiresRegeneration && !legacyFormatPending) {
      invalidateBlueprint('This legacy bracket needs a new authoritative preview before it can be saved.');
    } else {
      renderBracket();
    }
    if (event.schedule_rows.length && !requiresRegeneration) {
      const editable = event.schedule_mode === 'manual';
      const schedulable = event.schedule_rows.filter(row => !row.is_automatic_advance);
      $('#schedule-preview').innerHTML = `<table><thead><tr><th>Game</th><th>Matchup</th><th>Date</th><th>Time</th><th>Venue</th><th>Playing Area</th></tr></thead><tbody>${
        schedulable.map(row => `<tr data-match-key="${escapeHtml(row.match_key)}" data-date="${row.date}" data-time="${row.time}" data-venue="${escapeHtml(row.venue)}" data-area="${escapeHtml(row.playing_area || row.venue || '')}">
          <td>Game ${row.match_number}</td>
          <td>${escapeHtml(row.dependency_label || `${row.team_a} vs ${row.team_b}`)}</td>
          <td>${editable ? `<input data-date type="date" value="${row.date}">` : row.date}</td>
          <td>${editable ? `<input data-time type="time" value="${row.time}">` : row.time}</td>
          <td>${editable ? `<input data-venue value="${escapeHtml(row.venue)}">` : escapeHtml(row.venue)}</td>
          <td>${editable ? `<input data-area value="${escapeHtml(row.playing_area || row.venue || '')}">` : escapeHtml(row.playing_area || row.venue || '')}</td>
        </tr>`).join('')
      }</tbody></table>`;
    }
    updateSelectedTeamCount();
    updatePointsState();
    syncPairingUi();
    wizard.showModal();
  }

  function showSummary(event) {
    const rows = [
      ['Event', event.name],
      ['Sport/Game Type', event.sport_custom_name || event.sport_type || '—'],
      ['Status', event.publication_label],
      ['Classification', event.classification_label],
      ['Division', event.division],
      ['Venue', event.venue],
      ['Date range', `${event.start_date} to ${event.end_date}`],
      ['Teams', event.team_names.join(', ')],
      ['Format', event.tournament_type_label],
      ['Pairing', event.pairing_method || '—'],
      ['Actual Matches', String(event.actual_match_count ?? event.schedule_rows.length)],
      ['Automatic Advances', String(event.automatic_advance_count ?? 0)],
      ['Faculty In Charge', event.faculty_name || '—'],
      ['Schedule', `${event.actual_match_count ?? event.schedule_rows.length} actual matches · ${event.schedule_mode}`]
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
  $('#regenerate-bracket').addEventListener('click', regenerateBracket);
  $('#reset-bracket').addEventListener('click', resetBracket);
  $('#bracket-confirm-cancel').addEventListener('click', () => $('#bracket-confirm').close());
  $('#bracket-confirm-ok').addEventListener('click', async () => {
    $('#bracket-confirm').close();
    if (pendingBracketAction === 'reset') {
      invalidateBlueprint('Bracket reset. Select teams and generate a new preview.');
    } else if (pendingBracketAction === 'regenerate') {
      await generateBracket();
    }
    pendingBracketAction = null;
  });
  $('#generate-schedule').addEventListener('click', generateSchedule);
  $('#team-search').addEventListener('input', event => {
    const query = event.target.value.toLowerCase();
    $$('.team-options label').forEach(label => { label.hidden = !label.dataset.teamSearch.includes(query); });
  });
  $$('.team-check').forEach(input => input.addEventListener('change', () => {
    updateSelectedTeamCount();
    renderSeedList();
    invalidateBlueprint('Team selection changed. Generate a new authoritative preview.');
  }));
  $$('[name="tournament_type"]').forEach(input => input.addEventListener('change', () => {
    legacyFormatPending = false;
    $('#legacy-format-notice').hidden = true;
    $('#generate-bracket').disabled = false;
    syncThirdPlaceVisibility();
    invalidateBlueprint('Format changed. Generate a new authoritative preview.');
  }));
  $$('[name="pairing_method"]').forEach(input => input.addEventListener('change', () => {
    syncPairingUi();
    invalidateBlueprint('Pairing method changed. Generate a new authoritative preview.');
  }));
  form.elements.include_third_place.addEventListener('change', async () => {
    updateSelectedTeamCount();
    if (!drawOrder.length) return;
    try {
      await requestBlueprint(true);
    } catch (exception) {
      invalidateBlueprint(exception.message || 'Third-place settings changed. Generate a new authoritative preview.');
    }
  });
  $$('[name="schedule_mode"]').forEach(input => input.addEventListener('change', () => {
    $('#auto-settings').hidden = form.elements.schedule_mode.value === 'manual';
    $('#schedule-preview').innerHTML = '<p>Schedule mode changed. Generate the schedule again.</p>';
  }));
  form.elements.sport_type.addEventListener('change', () => {
    syncSportCustom();
    recommendResultFormat();
    syncGeneratedEventName();
  });
  form.elements.sport_custom_name.addEventListener('input', () => syncGeneratedEventName());
  form.elements.division.addEventListener('change', () => syncGeneratedEventName());
  form.elements.event_name.addEventListener('input', () => {
    const generated = buildEventName(sportLabel(), form.elements.division.value);
    eventNameManual = form.elements.event_name.value.trim() !== generated;
    updateEventNameHint(generated);
  });
  form.elements.event_classification.addEventListener('change', () => {
    pointsRows = defaultPointsFor(form.elements.event_classification.value);
    renderPointsEditor();
  });
  form.elements.playing_area_count?.addEventListener('change', syncPlayingAreas);
  form.elements.apply_championship_points.addEventListener('change', updatePointsState);
  $('#add-points-row').addEventListener('click', addPointsRow);
  renderPointsEditor();
  syncThirdPlaceVisibility();
  syncSportCustom();
  syncPairingUi();
  syncPlayingAreas();
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
    if (button.dataset.status === 'published') {
      const gaps = collectPublishGaps();
      if (gaps.length) {
        error.textContent = `Cannot publish yet: ${gaps.join(', ')}.`;
      }
    }
  }));
  form.addEventListener('submit', event => {
    const publishing = $('#publication-status').value === 'published';
    syncHiddenFields();
    if (publishing) {
      const gaps = collectPublishGaps();
      if (gaps.length) {
        event.preventDefault();
        error.textContent = `Cannot publish yet: ${gaps.join(', ')}.`;
        return;
      }
    }
    // Draft may be incomplete; publish requires review-step validations.
    if (publishing && !validateStep()) {
      event.preventDefault();
    }
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
