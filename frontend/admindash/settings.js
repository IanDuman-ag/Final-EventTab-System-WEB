document.addEventListener('DOMContentLoaded', () => {
  const form = document.getElementById('scoring-form');
  if (!form) return;
  const configNode = document.getElementById('scoring-config');
  const defaultsNode = document.getElementById('scoring-defaults');
  if (!configNode || !defaultsNode) return;
  const draft = JSON.parse(configNode.textContent);
  const savedNode = document.getElementById('scoring-saved');
  const saved = savedNode ? JSON.parse(savedNode.textContent) || draft : draft;
  const defaults = JSON.parse(defaultsNode.textContent);
  const rows = document.getElementById('placement-rows');
  const placementJSON = document.getElementById('placements-json');
  const winSection = document.getElementById('win-configuration');
  const placementSection = document.getElementById('placement-configuration');
  const previewClassification = document.getElementById('preview-classification');
  const previewWins = document.getElementById('preview-wins');
  const previewRank = document.getElementById('preview-rank');
  const orderWarning = document.getElementById('order-warning');
  const confirmOrder = document.getElementById('confirm-nonmonotonic');
  const saveButton = document.getElementById('save-scoring');
  const originalSaveLabel = saveButton.textContent;
  let baseline = '';
  let rulesBaseline = '';
  const rulesSignature = () => {
    const state = JSON.parse(stateSignature());
    delete state.reason;
    return JSON.stringify(state);
  };
  let submitting = false;

  const selectedMethod = () => form.querySelector('input[name="method"]:checked')?.value || 'placement';
  const wholeNumber = (value, minimum = 0, maximum = 1000000) => {
    if (value === '' || !/^\d+$/.test(String(value))) return null;
    const number = Number(value);
    return Number.isSafeInteger(number) && number >= minimum && number <= maximum ? number : null;
  };
  const ordinal = (value) => {
    const rank = Number(value);
    const suffix = rank % 100 >= 11 && rank % 100 <= 13 ? 'th' : ({ 1: 'st', 2: 'nd', 3: 'rd' }[rank % 10] || 'th');
    return `${rank}${suffix} Place`;
  };
  const placementValues = () => Array.from(rows.querySelectorAll('tr')).map((row) => ({
    rank: row.querySelector('[data-field="rank"]').value,
    major: row.querySelector('[data-field="major"]').value,
    minor: row.querySelector('[data-field="minor"]').value,
  }));
  const stateSignature = () => JSON.stringify({
    method: selectedMethod(),
    win_points: { major: document.getElementById('major-win').value, minor: document.getElementById('minor-win').value },
    placements: placementValues(),
    approval_required: document.getElementById('approval-required').checked,
    automatic_calculation: document.getElementById('automatic-calculation').checked,
    tie_policy: document.getElementById('tie-policy').value,
    reason: document.getElementById('override-reason')?.value || '',
    resolution: Array.from(form.querySelectorAll('[name^="placement_"]')).map((input) => [input.name, input.value]),
  });
  const isDirty = () => stateSignature() !== baseline;
  const updateDirty = () => {
    const dirty = isDirty();
    document.getElementById('dirty-dot').hidden = !dirty;
    document.getElementById('save-state').textContent = dirty ? 'Unsaved changes' : 'No unsaved changes';
  };
  const clearErrors = () => {
    form.querySelectorAll('.scoring-field-error').forEach((element) => { element.textContent = ''; });
    form.querySelectorAll('[aria-invalid]').forEach((input) => input.removeAttribute('aria-invalid'));
  };
  const setError = (input, message) => {
    input.setAttribute('aria-invalid', 'true');
    const error = document.getElementById(input.getAttribute('aria-describedby'));
    if (error) error.textContent = message;
  };
  const createPlacementRow = (data) => {
    const row = document.createElement('tr');
    const index = rows.children.length;
    ['rank', 'major', 'minor'].forEach((field) => {
      const cell = document.createElement('td');
      const input = document.createElement('input');
      const error = document.createElement('span');
      input.type = 'number';
      input.className = 'form-control';
      input.min = field === 'rank' ? '1' : '0';
      input.max = field === 'rank' ? '100' : '1000000';
      input.step = '1';
      input.value = data[field] ?? '';
      input.dataset.field = field;
      input.setAttribute('aria-label', field === 'rank' ? 'Placement position' : `${field === 'major' ? 'Major' : 'Minor'} points for this placement`);
      error.className = 'scoring-field-error';
      error.id = `placement-${index}-${field}-${Date.now()}`;
      input.setAttribute('aria-describedby', error.id);
      cell.append(input, error);
      row.append(cell);
    });
    const actionCell = document.createElement('td');
    const remove = document.createElement('button');
    remove.type = 'button';
    remove.className = 'scoring-remove';
    remove.textContent = '\u00d7';
    remove.setAttribute('aria-label', 'Remove this placement');
    remove.addEventListener('click', () => {
      if (!window.confirm('Remove this placement from the editable configuration? This is not saved until you save the form.')) return;
      row.remove();
      updateAll();
      document.getElementById('add-placement').focus();
    });
    actionCell.append(remove);
    row.append(actionCell);
    rows.append(row);
    return row;
  };
  const restore = (config) => {
    const method = form.querySelector(`input[name="method"][value="${config.method}"]`);
    if (method && !method.disabled) method.checked = true;
    else form.querySelector('input[name="method"][value="placement"]').checked = true;
    document.getElementById('major-win').value = config.win_points.major;
    document.getElementById('minor-win').value = config.win_points.minor;
    document.getElementById('approval-required').checked = Boolean(config.approval_required);
    document.getElementById('automatic-calculation').checked = Boolean(config.automatic_calculation);
    document.getElementById('tie-policy').value = config.tie_policy;
    rows.replaceChildren();
    (config.placements || []).forEach(createPlacementRow);
    confirmOrder.checked = false;
    clearErrors();
    updateAll();
  };
  const hasNonmonotonicAwards = () => {
    const placements = placementValues().filter((row) => wholeNumber(row.rank, 1, 100) !== null).sort((a, b) => Number(a.rank) - Number(b.rank));
    return ['major', 'minor'].some((field) => placements.some((row, index) => index > 0
      && wholeNumber(row[field]) !== null && wholeNumber(placements[index - 1][field]) !== null
      && Number(row[field]) > Number(placements[index - 1][field])));
  };
  const addPreviewLine = (label, value) => {
    const line = document.createElement('div');
    const name = document.createElement('span');
    const amount = document.createElement('strong');
    name.textContent = label;
    amount.textContent = value;
    line.append(name, amount);
    document.getElementById('preview-awards').append(line);
  };
  const updatePreview = () => {
    const method = selectedMethod();
    const classification = previewClassification.value;
    const usesWins = method !== 'placement';
    const usesPlacement = method !== 'per_win';
    document.getElementById('preview-wins-field').hidden = !usesWins;
    document.getElementById('preview-rank-field').hidden = !usesPlacement;
    const awards = placementValues();
    const previousRank = previewRank.value;
    previewRank.replaceChildren();
    awards.forEach((row) => {
      if (wholeNumber(row.rank, 1, 100) === null) return;
      const option = document.createElement('option');
      option.value = row.rank;
      option.textContent = ordinal(row.rank);
      previewRank.append(option);
    });
    if (Array.from(previewRank.options).some((option) => option.value === previousRank)) previewRank.value = previousRank;
    const winPoints = wholeNumber(document.getElementById(`${classification}-win`).value);
    const wins = wholeNumber(previewWins.value, 0, 1000);
    const placement = awards.find((row) => String(row.rank) === previewRank.value);
    const placementPoints = placement ? wholeNumber(placement[classification]) : null;
    const valid = (!usesWins || (winPoints !== null && wins !== null)) && (!usesPlacement || placementPoints !== null);
    const winTotal = usesWins && winPoints !== null && wins !== null ? winPoints * wins : 0;
    document.getElementById('preview-awards').replaceChildren();
    if (method === 'placement') {
      awards.forEach((row) => {
        const points = wholeNumber(row[classification]);
        addPreviewLine(wholeNumber(row.rank, 1, 100) !== null ? ordinal(row.rank) : 'Invalid placement', points === null ? 'Check points' : `${points.toLocaleString()} points`);
      });
    } else {
      addPreviewLine('Confirmed match wins', wins === null ? 'Check wins' : String(wins));
      addPreviewLine('Points per victory', winPoints === null ? 'Check points' : `${winPoints.toLocaleString()} points`);
      addPreviewLine('Match win subtotal', usesWins && wins !== null && winPoints !== null ? `${winTotal.toLocaleString()} points` : 'Check inputs');
      if (usesPlacement) addPreviewLine('Final placement award', placementPoints === null ? 'Check placement' : `${placementPoints.toLocaleString()} points`);
    }
    document.getElementById('preview-total').textContent = valid ? (winTotal + (usesPlacement ? placementPoints : 0)).toLocaleString() : '\u2014';
    document.getElementById('preview-formula').textContent = method === 'combined' ? 'Win points + placement points' : method === 'per_win' ? 'Confirmed wins \u00d7 points per win' : 'Selected final placement award';
  };
  const updateAll = () => {
    const method = selectedMethod();
    winSection.hidden = method === 'placement';
    placementSection.hidden = method === 'per_win';
    orderWarning.hidden = method === 'per_win' || !hasNonmonotonicAwards();
    placementJSON.value = JSON.stringify(placementValues().map((row) => ({
      rank: wholeNumber(row.rank, 1, 100) ?? row.rank,
      major: wholeNumber(row.major) ?? row.major,
      minor: wholeNumber(row.minor) ?? row.minor,
    })));
    updatePreview();
    updateDirty();
  };
  const validate = () => {
    clearErrors();
    let valid = true;
    const method = selectedMethod();
    if (method !== 'placement') {
      ['major', 'minor'].forEach((classification) => {
        const input = document.getElementById(`${classification}-win`);
        if (wholeNumber(input.value) === null) { setError(input, 'Enter a whole number from 0 to 1,000,000.'); valid = false; }
      });
    }
    if (method !== 'per_win') {
      const seen = new Set();
      if (rows.children.length === 0) { document.getElementById('placement-error').textContent = 'Add at least one placement.'; valid = false; }
      rows.querySelectorAll('tr').forEach((row) => {
        const rank = row.querySelector('[data-field="rank"]');
        const number = wholeNumber(rank.value, 1, 100);
        if (number === null) { setError(rank, 'Enter a position from 1 to 100.'); valid = false; }
        else if (seen.has(number)) { setError(rank, 'Each placement must be unique.'); valid = false; }
        seen.add(number);
        ['major', 'minor'].forEach((field) => {
          const input = row.querySelector(`[data-field="${field}"]`);
          if (wholeNumber(input.value) === null) { setError(input, 'Enter whole points from 0 to 1,000,000.'); valid = false; }
        });
      });
      if (hasNonmonotonicAwards() && !confirmOrder.checked) {
        document.getElementById('placement-error').textContent = 'Confirm the lower-placement point exception before saving.';
        valid = false;
      }
    }
    const reason = document.getElementById('override-reason');
    if (reason && !reason.value.trim()) {
      reason.setAttribute('aria-invalid', 'true');
      document.getElementById('reason-error').textContent = 'Explain why this event override or recalculation is needed.';
      valid = false;
    }
    const resolutions = Array.from(form.querySelectorAll('[name^="placement_"]'));
    if (resolutions.some((input) => input.value !== '')) {
      resolutions.forEach((input) => {
        if (wholeNumber(input.value, 1, 100) === null) { setError(input, 'Record a position from 1 to 100 for every team, or leave all blank.'); valid = false; }
      });
    }
    return valid;
  };

  rows.replaceChildren();
  (draft.placements || []).forEach(createPlacementRow);
  const officialNode = document.getElementById('official-placements');
  const officialPlacements = officialNode ? JSON.parse(officialNode.textContent) || {} : {};
  const enteredNode = document.getElementById('entered-placements');
  const enteredPlacements = enteredNode ? JSON.parse(enteredNode.textContent) : null;
  const signatureFor = (config, resolution, reason) => JSON.stringify({
    method: config.method,
    win_points: { major: String(config.win_points.major), minor: String(config.win_points.minor) },
    placements: (config.placements || []).map((row) => ({ rank: String(row.rank), major: String(row.major), minor: String(row.minor) })),
    approval_required: Boolean(config.approval_required),
    automatic_calculation: Boolean(config.automatic_calculation),
    tie_policy: config.tie_policy,
    reason,
    resolution: Array.from(form.querySelectorAll('[data-team-id]')).map((input) => [input.name, String(resolution[input.dataset.teamId] ?? '')]),
  });
  form.querySelectorAll('[data-team-id]').forEach((input) => {
    const savedValue = officialPlacements[input.dataset.teamId];
    const value = enteredPlacements === null ? savedValue : enteredPlacements[input.dataset.teamId];
    input.value = value === undefined || value === null ? '' : value;
    input.defaultValue = savedValue === undefined || savedValue === null ? '' : savedValue;
  });
  baseline = signatureFor(saved, officialPlacements, '');
  const persistedRules = JSON.parse(baseline);
  delete persistedRules.reason;
  rulesBaseline = JSON.stringify(persistedRules);
  updateAll();
  const errorNode = document.getElementById('scoring-errors');
  const serverErrors = errorNode ? JSON.parse(errorNode.textContent) || {} : {};
  const errorTargets = {
    method: 'method-error', placements: 'placement-error', major_win: 'major-win-error',
    minor_win: 'minor-win-error', tie_policy: 'tie-policy-error', reason: 'reason-error',
    official_placements: 'resolution-error', placements_resolution: 'resolution-error',
    resolution: 'resolution-error', final_placements: 'resolution-error', correction_match: 'correction-match-error',
    confirm_nonmonotonic: 'placement-error',
  };
  Object.entries(serverErrors).forEach(([field, message]) => {
    const target = document.getElementById(errorTargets[field] || 'placement-error');
    if (target) target.textContent = Array.isArray(message) ? message.join(' ') : String(message);
    const input = form.elements.namedItem(field);
    if (input instanceof HTMLElement) input.setAttribute('aria-invalid', 'true');
  });
  form.addEventListener('input', (event) => {
    if (event.target.closest('#placement-rows') || event.target.name === 'method') confirmOrder.checked = false;
    updateAll();
  });
  form.addEventListener('change', updateAll);
  document.getElementById('add-placement').addEventListener('click', () => {
    const existing = new Set(placementValues().map((row) => Number(row.rank)));
    let rank = 1;
    while (existing.has(rank) && rank <= 100) rank += 1;
    if (rank > 100) { document.getElementById('placement-error').textContent = 'A maximum of 100 unique placements is supported.'; return; }
    const row = createPlacementRow({ rank, major: 0, minor: 0 });
    updateAll();
    row.querySelector('input').focus();
  });
  document.getElementById('reset-scoring').addEventListener('click', () => {
    if (window.confirm('Restore the documented defaults in this editable form? Nothing will be saved until you save the configuration.')) restore(defaults);
  });
  document.getElementById('discard-scoring').addEventListener('click', () => {
    if (!isDirty() || window.confirm('Discard unsaved scoring changes and restore the last saved configuration?')) {
      restore(saved);
      const reason = document.getElementById('override-reason');
      if (reason) reason.value = ''; 
      form.querySelectorAll('[name^="placement_"]').forEach((input) => { input.value = input.defaultValue; });
      updateAll();
    }
  });
  form.addEventListener('submit', (event) => {
    if (submitting) { event.preventDefault(); return; }
    const operation = event.submitter?.value;
    const savedRuleOperation = operation === 'reopen' || operation === 'recalculate';
    let valid;
    if (savedRuleOperation) {
      clearErrors();
      valid = true;
      const reason = document.getElementById('override-reason');
      if (reason && !reason.value.trim()) {
        reason.setAttribute('aria-invalid', 'true');
        document.getElementById('reason-error').textContent = 'Provide an audit reason for this action.';
        valid = false;
      }
      if (operation === 'reopen') {
        const match = document.getElementById('correction-match');
        if (!match || !match.value) {
          if (match) setError(match, 'Select a confirmed match to reopen.');
          valid = false;
        }
      }
    } else valid = validate();
    if (!valid) {
      event.preventDefault();
      const invalid = form.querySelector('[aria-invalid="true"]');
      if (invalid) invalid.focus();
      else document.getElementById('placement-error').scrollIntoView({ block: 'center' });
      return;
    }
    if (savedRuleOperation && rulesSignature() !== rulesBaseline && !window.confirm('This action uses the saved event rules. Your unsaved rule edits will not be applied. Continue?')) {
      event.preventDefault();
      return;
    }
    if (operation === 'reopen' && !window.confirm('Reopen this confirmed match for correction? The event publication will be withdrawn. Authorized review and publication are required again.')) {
      event.preventDefault();
      return;
    }
    updateAll();
    submitting = true;
    form.setAttribute('aria-busy', 'true');
    // Keep submitter values enabled for the POST; prevent duplicates in the handler.
    saveButton.setAttribute('aria-disabled', 'true');
    saveButton.textContent = operation === 'recalculate' ? 'Recalculating...' : operation === 'reopen' ? 'Reopening match...' : 'Saving configuration...';
    document.getElementById('save-state').textContent = 'Saving changes...';
  });
  window.addEventListener('beforeunload', (event) => {
    if (!submitting && isDirty()) { event.preventDefault(); event.returnValue = ''; }
  });
  window.addEventListener('pageshow', () => {
    submitting = false;
    form.removeAttribute('aria-busy');
    saveButton.removeAttribute('aria-disabled');
    saveButton.textContent = originalSaveLabel;
    updateDirty();
  });
});
