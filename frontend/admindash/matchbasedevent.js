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
  let bracketZoom = 1;
  let lastBracketEdges = [];
  let connectorResizeObserver = null;
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
    return 'random_draw';
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
    bracketZoom = 1;
    $('#draw-order').value = '[]';
    $('#bracket-preview').innerHTML = `<p>${escapeHtml(message)}</p>`;
    $('#bracket-stats').hidden = true;
    const toolbar = $('#bracket-toolbar');
    if (toolbar) toolbar.hidden = true;
    $('#schedule-preview').innerHTML = '<p>Generate the bracket first, then create its schedule.</p>';
    const conflicts = $('#schedule-conflicts');
    if (conflicts) { conflicts.hidden = true; conflicts.textContent = ''; }
  }

  function powerOfTwoCeil(n) {
    let size = 1;
    while (size < n) size *= 2;
    return size;
  }

  function openingPairsFromDraw(orderedIds) {
    const ids = orderedIds.map(Number).filter(Boolean);
    const bracketSize = powerOfTwoCeil(Math.max(ids.length, 1));
    const byes = bracketSize - ids.length;
    const pairs = [];
    let cursor = 0;
    for (let i = 0; i < byes; i += 1) {
      pairs.push([ids[cursor], null]);
      cursor += 1;
    }
    while (cursor < ids.length) {
      pairs.push([ids[cursor], ids[cursor + 1]]);
      cursor += 2;
    }
    return { bracketSize, pairs };
  }

  function formatTournamentLabel(value) {
    const key = String(value || form.elements.tournament_type?.value || '');
    if (key === 'double_elimination') return 'Double Elimination';
    if (key === 'round_robin') return 'Round Robin';
    return 'Single Elimination';
  }

  function statusLabel(status) {
    const key = String(status || 'pending').toLowerCase();
    if (key === 'ongoing') return 'Ongoing';
    if (key === 'completed') return 'Completed';
    if (key === 'forfeit') return 'Forfeit';
    return 'Pending';
  }

  function teamLogoHtml(participant) {
    const src = participant?.team_logo;
    if (!src) return '<span class="tb-logo tb-logo--empty" aria-hidden="true"></span>';
    return `<img class="tb-logo" src="${escapeHtml(src)}" alt="">`;
  }

  function sideMeta(match, side) {
    const participant = match[`participant_${side}`] || null;
    const label = participantLabel(match, side);
    const teamId = participant?.team_id ?? match[`team_${side}_id`] ?? null;
    const score = match[`score_${side}`] ?? '';
    const winnerId = match.winner_team_id ?? null;
    const winnerName = (match.winner_name || '').trim();
    const isWinner = Boolean(
      (winnerId && teamId && Number(winnerId) === Number(teamId))
      || (winnerName && label && winnerName.toLowerCase() === label.toLowerCase())
    );
    const isDependency = /^(Winner|Loser) of /i.test(label) || /^Awaiting /i.test(label);
    return { label, participant, score, isWinner, isDependency, teamId };
  }

  function renderTbSide(meta, options = {}) {
    const dim = options.completed && !meta.isWinner && !meta.isDependency;
    const win = meta.isWinner ? ' tb-side--winner' : '';
    const dimCls = dim ? ' tb-side--eliminated' : '';
    const check = meta.isWinner
      ? '<span class="tb-winner-check" aria-hidden="true">✓</span>'
      : '';
    const score = meta.score !== '' && meta.score != null
      ? `<span class="tb-score">${escapeHtml(meta.score)}</span>`
      : '';
    return `<div class="tb-side${win}${dimCls}">
      ${teamLogoHtml(meta.participant)}
      <span class="tb-team-name" title="${escapeHtml(meta.label)}">${escapeHtml(meta.label)}</span>
      ${score}${check}
    </div>`;
  }

  function renderAdvanceCard(match) {
    const name = participantLabel(match, 'a');
    const tip = match.tooltip || 'Automatic Advance (BYE) — not a playable match';
    return `<article class="tb-card tb-card--advance" data-node-key="${escapeHtml(match.key)}" title="${escapeHtml(tip)}">
      <div class="tb-card-head">
        <span class="tb-badge tb-badge--advance">Automatic Advance</span>
        <span class="tb-round-tag">${escapeHtml(match.round || '')}</span>
      </div>
      <div class="tb-side tb-side--advance">
        ${teamLogoHtml(match.participant_a)}
        <span class="tb-team-name" title="${escapeHtml(name)}">${escapeHtml(name)}</span>
      </div>
      <p class="tb-card-note">BYE · seats into next round</p>
    </article>`;
  }

  function renderMatchCard(match) {
    const completed = ['completed', 'forfeit'].includes(String(match.status || '').toLowerCase())
      || Boolean(match.winner_name || match.winner_team_id);
    const sideA = sideMeta(match, 'a');
    const sideB = sideMeta(match, 'b');
    const scheduleBits = [match.date, match.time, match.venue || match.playing_area].filter(Boolean);
    const schedule = scheduleBits.length
      ? `<p class="tb-schedule">${escapeHtml(scheduleBits.join(' · '))}</p>`
      : '';
    const winnerLine = completed && (match.winner_name || sideA.isWinner || sideB.isWinner)
      ? `<p class="tb-confirmed">Winner: ${escapeHtml(match.winner_name || (sideA.isWinner ? sideA.label : sideB.label))}</p>`
      : '';
    return `<article class="tb-card tb-card--match tb-card--${escapeHtml(String(match.status || 'pending').toLowerCase())}" data-node-key="${escapeHtml(match.key)}">
      <div class="tb-card-head">
        <strong>Game ${escapeHtml(match.number || '—')}</strong>
        <span class="tb-round-tag">${escapeHtml(match.round || '')}</span>
        <span class="tb-status">${escapeHtml(statusLabel(match.status))}</span>
      </div>
      ${renderTbSide(sideA, { completed })}
      <div class="tb-vs" aria-hidden="true">vs</div>
      ${renderTbSide(sideB, { completed })}
      ${schedule}${winnerLine}
    </article>`;
  }

  function renderChampionCard(finalMatch) {
    const winnerName = (finalMatch?.winner_name || '').trim();
    const completed = Boolean(winnerName || finalMatch?.winner_team_id);
    if (!completed) {
      return `<article class="tb-card tb-card--champion tb-card--awaiting" data-node-key="champion">
        <div class="tb-card-head"><strong>Champion</strong></div>
        <p class="tb-awaiting">Awaiting Final Winner</p>
      </article>`;
    }
    const winnerSide = sideMeta(finalMatch, 'a').isWinner ? 'a' : 'b';
    const participant = finalMatch[`participant_${winnerSide}`];
    const scoreLine = (finalMatch.score_a || finalMatch.score_b)
      ? `<p class="tb-final-score">${escapeHtml(finalMatch.score_a || '0')} – ${escapeHtml(finalMatch.score_b || '0')}</p>`
      : '';
    return `<article class="tb-card tb-card--champion" data-node-key="champion">
      <div class="tb-card-head">
        <strong>Champion</strong>
        <span class="tb-trophy" aria-hidden="true">♛</span>
      </div>
      <div class="tb-side tb-side--winner">
        ${teamLogoHtml(participant)}
        <span class="tb-team-name">${escapeHtml(winnerName || participantLabel(finalMatch, winnerSide))}</span>
      </div>
      ${scoreLine}
    </article>`;
  }

  function reconstructAdvances(matches, orderedIds) {
    const existing = matches.filter(m => m.is_automatic_advance);
    if (existing.length) return matches;
    const actual = matches.filter(m => !m.is_automatic_advance);
    if (!orderedIds.length) return matches;
    const { pairs } = openingPairsFromDraw(orderedIds);
    const advances = [];
    pairs.forEach((pair, slot) => {
      const [teamA, teamB] = pair;
      if (teamB != null) return;
      const nameFromPicker = selectedTeams().find(t => Number(t.id) === Number(teamA))?.name;
      const nextSlot = Math.floor(slot / 2);
      const nextRoundMatches = actual.filter(m => {
        const ids = [m.team_a_id, m.team_b_id].map(v => (v == null ? null : Number(v)));
        return ids.includes(Number(teamA));
      });
      const target = nextRoundMatches[0];
      advances.push({
        key: `aa-recon-${slot}`,
        number: 0,
        round: pairs.some(p => p[1] != null)
          ? (actual.find(m => m.team_a_id && m.team_b_id)?.round || 'Opening Round')
          : 'Opening Round',
        team_a_id: teamA,
        team_b_id: null,
        is_automatic_advance: true,
        synthetic_advance: true,
        team_a: nameFromPicker || `Team ${teamA}`,
        team_b: 'Automatic Advance',
        display_label: `${nameFromPicker || 'Team'} — Automatic Advance`,
        participant_a: { team_id: teamA, team_name: nameFromPicker || null, team_logo: '' },
        participant_b: null,
        next_winner_key: target?.key || null,
        opening_slot: slot,
        next_side: target && Number(target.team_a_id) === Number(teamA) ? 'a' : 'b',
        tooltip: 'Automatic Advance (BYE)',
      });
    });
    return actual.concat(advances);
  }

  function buildEliminationColumns(matches, orderedIds) {
    const all = reconstructAdvances(matches, orderedIds);
    const advances = all.filter(m => m.is_automatic_advance);
    const actual = all.filter(m => !m.is_automatic_advance);
    const third = actual.filter(m => /third/i.test(m.round || ''));
    const mainActual = actual.filter(m => !/third/i.test(m.round || ''));
    const byKey = Object.fromEntries(all.map(m => [m.key, m]));
    const { bracketSize, pairs } = openingPairsFromDraw(orderedIds.length ? orderedIds : []);

    const edges = [];
    const columns = [];
    let openingNodes = [];

    if (pairs.length) {
      const advanceByTeam = {};
      advances.forEach(a => {
        if (a.team_a_id != null) advanceByTeam[Number(a.team_a_id)] = a;
      });
      openingNodes = pairs.map((pair, slot) => {
        const [teamA, teamB] = pair;
        if (teamB == null) {
          const advance = advanceByTeam[Number(teamA)];
          if (!advance) return null;
          const nextKey = advance.next_winner_key
            || (bracketSize > 2
              ? mainActual.find(m => [m.team_a_id, m.team_b_id].map(Number).includes(Number(teamA)))?.key
              : null);
          if (nextKey) {
            edges.push({ from: advance.key, to: nextKey, kind: 'advance' });
          }
          return { key: advance.key, match: advance, kind: 'advance', slot };
        }
        const match = mainActual.find(m => {
          const ids = new Set([Number(m.team_a_id), Number(m.team_b_id)]);
          return ids.has(Number(teamA)) && ids.has(Number(teamB));
        });
        if (!match) return null;
        if (match.next_winner_key) {
          edges.push({ from: match.key, to: match.next_winner_key, kind: 'winner' });
        }
        return { key: match.key, match, kind: 'match', slot };
      }).filter(Boolean);

      if (openingNodes.length) {
        const roundName = openingNodes[0].match.round || 'Opening Round';
        columns.push({ name: roundName, nodes: openingNodes, slotCount: pairs.length });
      }
    }

    const usedKeys = new Set(openingNodes.map(n => n.key));
    const remaining = mainActual.filter(m => !usedKeys.has(m.key));
    const roundOrder = [];
    const byRound = {};
    remaining.forEach(match => {
      const name = match.round || 'Round';
      if (!byRound[name]) {
        byRound[name] = [];
        roundOrder.push(name);
      }
      byRound[name].push(match);
      if (match.next_winner_key && byKey[match.next_winner_key]) {
        edges.push({ from: match.key, to: match.next_winner_key, kind: 'winner' });
      }
    });
    roundOrder.forEach(name => {
      columns.push({
        name,
        nodes: byRound[name].map((match, index) => ({
          key: match.key,
          match,
          kind: 'match',
          slot: index,
        })),
        slotCount: byRound[name].length,
      });
    });

    if (!columns.length && mainActual.length) {
      const byRoundFallback = {};
      const order = [];
      mainActual.forEach(match => {
        const name = match.round || 'Round';
        if (!byRoundFallback[name]) {
          byRoundFallback[name] = [];
          order.push(name);
        }
        byRoundFallback[name].push(match);
        if (match.next_winner_key) {
          edges.push({ from: match.key, to: match.next_winner_key, kind: 'winner' });
        }
      });
      order.forEach(name => {
        columns.push({
          name,
          nodes: byRoundFallback[name].map((match, index) => ({
            key: match.key, match, kind: 'match', slot: index,
          })),
          slotCount: byRoundFallback[name].length,
        });
      });
    }

    const finalMatch = [...mainActual].reverse().find(m =>
      /final/i.test(m.round || '') && !m.next_winner_key
    ) || mainActual[mainActual.length - 1] || null;
    if (finalMatch) {
      edges.push({ from: finalMatch.key, to: 'champion', kind: 'champion' });
      columns.push({
        name: 'Champion',
        nodes: [{ key: 'champion', match: finalMatch, kind: 'champion', slot: 0 }],
        slotCount: 1,
        isChampion: true,
      });
    }

    return { columns, edges, third, bracketSize, allMatches: all };
  }

  function computeNodeTops(columns) {
    const firstCount = Math.max(
      columns[0]?.slotCount || columns[0]?.nodes.length || 1,
      columns[0]?.nodes.length || 1,
      1,
    );
    const leafSlots = powerOfTwoCeil(firstCount);
    const treeSlots = leafSlots;
    const slotH = 96;
    const treeHeight = Math.max(treeSlots * slotH, 220);

    columns.forEach((col, rIdx) => {
      const count = Math.max(col.nodes.length, 1);
      const span = treeSlots / count;
      col.nodes.forEach((node, mIdx) => {
        const slotIndex = node.slot != null && col.slotCount
          ? node.slot
          : mIdx;
        const center = col.slotCount && col.slotCount > count
          ? (slotIndex + 0.5) * (treeSlots / col.slotCount)
          : (mIdx + 0.5) * span;
        node.topPx = center * slotH - 48;
        node.rIdx = rIdx;
        node.mIdx = mIdx;
      });
      col.treeHeight = treeHeight;
    });
    return treeHeight;
  }

  function renderRoundRobinPreview(matches) {
    const rows = matches.filter(m => !m.is_automatic_advance);
    const teamMap = {};
    rows.forEach(match => {
      ['a', 'b'].forEach(side => {
        const meta = sideMeta(match, side);
        if (!meta.teamId && meta.isDependency) return;
        const key = meta.teamId || meta.label;
        if (!teamMap[key]) {
          teamMap[key] = {
            name: meta.label,
            logo: meta.participant?.team_logo || '',
            played: 0,
            wins: 0,
            losses: 0,
          };
        }
      });
    });
    const standings = Object.values(teamMap);
    return `<div class="tb-rr">
      <div class="tb-rr-standings">
        <h4>Standings</h4>
        <table>
          <thead><tr><th>Team</th><th>P</th><th>W</th><th>L</th></tr></thead>
          <tbody>
            ${standings.map(row => `<tr>
              <td>${row.logo ? `<img class="tb-logo" src="${escapeHtml(row.logo)}" alt="">` : ''} ${escapeHtml(row.name)}</td>
              <td>${row.played}</td><td>${row.wins}</td><td>${row.losses}</td>
            </tr>`).join('') || '<tr><td colspan="4">No participants</td></tr>'}
          </tbody>
        </table>
      </div>
      <div class="tb-rr-matches">
        <h4>Match List</h4>
        <div class="tb-rr-grid">
          ${rows.map(renderMatchCard).join('') || '<p>No matches.</p>'}
        </div>
      </div>
    </div>`;
  }

  function drawBracketConnectors() {
    const canvas = $('#tb-canvas');
    const svg = $('#tb-connectors');
    if (!canvas || !svg) return;
    const edges = lastBracketEdges;
    const width = Math.max(canvas.scrollWidth, canvas.offsetWidth);
    const height = Math.max(canvas.scrollHeight, canvas.offsetHeight);
    svg.setAttribute('width', String(width));
    svg.setAttribute('height', String(height));
    svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
    const canvasBox = canvas.getBoundingClientRect();
    const scaleX = width / Math.max(canvasBox.width, 1);
    const scaleY = height / Math.max(canvasBox.height, 1);

    function centerRight(el) {
      const box = el.getBoundingClientRect();
      return {
        x: (box.right - canvasBox.left) * scaleX,
        y: (box.top + box.height / 2 - canvasBox.top) * scaleY,
      };
    }
    function centerLeft(el) {
      const box = el.getBoundingClientRect();
      return {
        x: (box.left - canvasBox.left) * scaleX,
        y: (box.top + box.height / 2 - canvasBox.top) * scaleY,
      };
    }

    const paths = edges.map(edge => {
      const fromEl = canvas.querySelector(`[data-node-key="${String(edge.from).replace(/"/g, '')}"]`);
      const toEl = canvas.querySelector(`[data-node-key="${String(edge.to).replace(/"/g, '')}"]`);
      if (!fromEl || !toEl) return '';
      const a = centerRight(fromEl);
      const b = centerLeft(toEl);
      const mid = (a.x + b.x) / 2;
      return `<path class="tb-connector-path tb-connector-path--${escapeHtml(edge.kind)}" d="M ${a.x} ${a.y} H ${mid} V ${b.y} H ${b.x}" fill="none" />`;
    }).join('');
    svg.innerHTML = paths;
  }

  function scheduleConnectorRedraw() {
    window.requestAnimationFrame(() => {
      drawBracketConnectors();
      window.requestAnimationFrame(drawBracketConnectors);
    });
  }

  function setBracketZoom(next) {
    bracketZoom = Math.min(1.6, Math.max(0.55, next));
    const canvas = $('#tb-canvas');
    const label = $('#bracket-zoom-label');
    if (canvas) {
      canvas.style.transform = `scale(${bracketZoom})`;
      canvas.style.transformOrigin = 'top left';
    }
    if (label) label.textContent = `${Math.round(bracketZoom * 100)}%`;
    scheduleConnectorRedraw();
  }

  function fitBracketZoom() {
    const scroll = $('#tb-scroll');
    const canvas = $('#tb-canvas');
    if (!scroll || !canvas) return setBracketZoom(1);
    canvas.style.transform = 'scale(1)';
    const need = canvas.scrollWidth;
    const avail = Math.max(scroll.clientWidth - 16, 200);
    setBracketZoom(Math.min(1, avail / need));
  }

  function renderBracketStats() {
    const stats = $('#bracket-stats');
    if (!stats) return;
    const participants = selectedTeams().length || drawOrder.length;
    stats.hidden = !matchPreview.length;
    stats.innerHTML = `
      <span class="status-badge status-badge--participants">${participants} participants</span>
      <span class="status-badge status-badge--scheduled">${actualMatchCount} actual matches</span>
      <span class="status-badge status-badge--advance">${automaticAdvanceCount} automatic advances</span>
      <span class="status-badge status-badge--format">${escapeHtml(formatTournamentLabel())}</span>
      <span class="status-badge status-badge--pairing">Random Draw</span>
    `;
  }

  function renderBracket() {
    renderBracketStats();
    const toolbar = $('#bracket-toolbar');
    const preview = $('#bracket-preview');
    if (!matchPreview.length) {
      if (toolbar) toolbar.hidden = true;
      preview.innerHTML = '<p>Select teams, then generate a preview.</p>';
      return;
    }

    const format = form.elements.tournament_type.value;
    if (format === 'round_robin') {
      if (toolbar) toolbar.hidden = true;
      preview.innerHTML = renderRoundRobinPreview(matchPreview);
      return;
    }

    if (toolbar) toolbar.hidden = false;
    const { columns, edges, third, allMatches } = buildEliminationColumns(matchPreview, drawOrder);
    lastBracketEdges = edges;
    const treeHeight = computeNodeTops(columns);
    const thirdHtml = third.length
      ? `<div class="tb-third"><h4>Third Place</h4>${third.map(renderMatchCard).join('')}</div>`
      : '';

    preview.innerHTML = `
      <div class="tb-shell" id="tb-shell">
        <div class="tb-scroll" id="tb-scroll">
          <div class="tb-canvas" id="tb-canvas" style="--tb-height:${treeHeight}px; transform:scale(${bracketZoom}); transform-origin:top left;">
            <svg class="tb-connectors" id="tb-connectors" aria-hidden="true"></svg>
            <div class="tb-rounds">
              ${columns.map(col => `
                <section class="tb-round${col.isChampion ? ' tb-round--champion' : ''}">
                  <h4 class="tb-round-title">${escapeHtml(col.name)}</h4>
                  <div class="tb-track" style="height:${treeHeight}px">
                    ${col.nodes.map(node => {
                      const card = node.kind === 'advance'
                        ? renderAdvanceCard(node.match)
                        : node.kind === 'champion'
                          ? renderChampionCard(node.match)
                          : renderMatchCard(node.match);
                      return `<div class="tb-node" style="top:${node.topPx}px">${card}</div>`;
                    }).join('')}
                  </div>
                </section>
              `).join('')}
            </div>
          </div>
        </div>
        ${thirdHtml}
      </div>
    `;
    preview.dataset.nodeCount = String(allMatches.length);
    bindBracketViewport();
    scheduleConnectorRedraw();
  }

  function bindBracketViewport() {
    if (connectorResizeObserver) return;
    connectorResizeObserver = new ResizeObserver(() => scheduleConnectorRedraw());
    const preview = $('#bracket-preview');
    if (preview) connectorResizeObserver.observe(preview);
    window.addEventListener('resize', scheduleConnectorRedraw);
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
    const recommended = RESULT_BY_SPORT[sport] || 'team_final_score';
    if (form.elements.result_entry_format) {
      form.elements.result_entry_format.value = recommended;
    }
  }

  function syncTieBreakHidden() {
    if ($('#tie-break-rules')) {
      $('#tie-break-rules').value = JSON.stringify(['head_to_head', 'score_difference', 'points_scored']);
    }
  }

  function syncPairingUi() {
    const label = $('#pairing-summary-label');
    if (label) label.textContent = 'Random Draw';
  }

  function participantLabel(match, side) {
    const participant = match[`participant_${side}`];
    if (participant && participant.team_name) return participant.team_name;
    const value = match[`team_${side}`];
    if (value == null || value === '') return 'TBD';
    const text = String(value).trim();
    if (/^\d+$/.test(text)) return 'Team information unavailable';
    return text;
  }

  function matchDisplayLabel(match) {
    const raw = match.display_label || match.dependency_label || '';
    if (raw && !/(^|\s)\d+(\s+vs\s+|\s*$)/.test(raw) && !/\svs\s+\d+(\s|$)/.test(raw)) {
      return raw;
    }
    if (match.is_automatic_advance) {
      return `${participantLabel(match, 'a')} — Automatic Advance`;
    }
    return `${participantLabel(match, 'a')} vs ${participantLabel(match, 'b')}`;
  }

  function syncPlayingAreas() {
    const venue = (form.elements.venue?.value || '').trim() || 'Court 1';
    if (form.elements.playing_area_count) form.elements.playing_area_count.value = '1';
    if ($('#playing-areas')) $('#playing-areas').value = JSON.stringify([venue]);
  }

  function syncHiddenFields() {
    $('#team-ids').value = JSON.stringify(selectedTeams().map(team => team.id));
    $('#draw-order').value = JSON.stringify(drawOrder);
    syncPointsFromDom();
    $('#points-config').value = JSON.stringify(pointsRows);
    syncTieBreakHidden();
    syncPlayingAreas();
    if (form.elements.scoresheet_template_mode?.value === 'auto') {
      form.elements.scoresheet_template.value = '';
    }
    if (form.elements.pairing_method) form.elements.pairing_method.value = 'random_draw';
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
    const venue = (form.elements.venue?.value || '').trim() || 'Court 1';
    const startDate = form.elements.first_match_date?.value || form.elements.start_date.value;
    const endDate = form.elements.end_date.value;
    const start = minutes(form.elements.daily_start_time.value || '08:00');
    const end = minutes(form.elements.daily_end_time.value || '17:00');
    const duration = 60;
    const breakMins = 10;
    syncPlayingAreas();
    if (mode === 'auto' && start >= end) { error.textContent = 'Daily end time must be later than daily start time.'; return; }

    let day = 0;
    let slot = start;
    const rows = actual.map(match => {
      if (mode === 'auto' && slot + duration > end) { day += 1; slot = start; }
      const matchDate = mode === 'auto' ? addDays(startDate, day) : startDate;
      const matchTime = mode === 'auto' ? timeLabel(slot) : form.elements.daily_start_time.value || '08:00';
      if (mode === 'auto') slot += duration + breakMins;
      return {
        match,
        number: match.number,
        date: matchDate,
        time: matchTime,
        venue,
        area: venue,
        label: matchDisplayLabel(match)
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
    $('#schedule-preview').innerHTML = `${advanceNote}<table><thead><tr><th>Game</th><th>Matchup</th><th>Date</th><th>Time</th><th>Venue</th></tr></thead><tbody>${
      rows.map(row => `<tr data-match-key="${escapeHtml(row.match.key)}" data-date="${row.date}" data-time="${row.time}" data-venue="${escapeHtml(row.venue)}" data-area="${escapeHtml(row.area)}">
        <td>Game ${row.number}</td>
        <td>${escapeHtml(row.label)}</td>
        <td>${editable ? `<input data-date type="date" value="${row.date}" min="${form.elements.start_date.value}" max="${endDate}">` : row.date}</td>
        <td>${editable ? `<input data-time type="time" value="${row.time}">` : row.time}</td>
        <td>${editable ? `<input data-venue value="${escapeHtml(row.venue)}">` : escapeHtml(row.venue)}</td>
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
      ['Pairing Method', 'Random Draw'],
      ['Actual Matches', String(actualMatchCount || actual.length)],
      ['Automatic Advances', String(automaticAdvanceCount || advances.length)],
      ['Unresolved / Pending Matchups', unresolved.length ? unresolved.map(match => matchDisplayLabel(match)).join(' · ') : 'None'],
      ['Schedule', $('#schedule-preview tbody') ? `${actual.length} scheduled actual matches · ${valueText('schedule_mode')}` : 'Not generated'],
      ['Faculty In Charge', valueText('faculty_account')],
      ['Scoresheet Template', form.elements.scoresheet_template_mode?.value === 'auto' ? 'Auto by Sport/Game Type' : valueText('scoresheet_template')],
      ['Championship Points', form.elements.apply_championship_points.checked
        ? pointsRows.map(point => `${point.label}: ${point.points}`).join(' · ')
        : 'Disabled']
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
    if (form.elements.pairing_method) form.elements.pairing_method.value = 'random_draw';
    legacyFormatPending = Boolean(event.legacy_double_elimination);
    $('#legacy-format-notice').hidden = !legacyFormatPending;
    $('#generate-bracket').disabled = legacyFormatPending;
    form.elements.include_third_place.checked = event.include_third_place;
    syncThirdPlaceVisibility();
    form.elements.schedule_mode.value = event.schedule_mode;
    form.elements.daily_start_time.value = event.daily_start_time || '08:00';
    form.elements.daily_end_time.value = event.daily_end_time || '17:00';
    const cfg = event.schedule_config || {};
    if (form.elements.first_match_date) form.elements.first_match_date.value = cfg.first_match_date || event.start_date;
    syncPlayingAreas();
    form.elements.faculty_account.value = event.faculty_account_id || '';
    recommendResultFormat();
    if (form.elements.result_entry_format && event.result_entry_format) {
      form.elements.result_entry_format.value = event.result_entry_format;
    }
    syncTieBreakHidden();
    if (form.elements.scoresheet_template) {
      form.elements.scoresheet_template.value = event.scoresheet_template_id || '';
      if (form.elements.scoresheet_template_mode) {
        form.elements.scoresheet_template_mode.value = event.scoresheet_template_id ? 'existing' : 'auto';
      }
    }
    form.elements.apply_championship_points.checked = event.apply_championship_points;
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
      display_label: row.dependency_label || `${row.team_a} vs ${row.team_b}`,
      dependency_label: row.dependency_label || '',
      participant_a: row.participant_a || null,
      participant_b: row.participant_b || null,
      team_a_id: row.participant_a?.team_id || null,
      team_b_id: row.participant_b?.team_id || null,
      next_winner_key: row.next_winner_key || null,
      next_loser_key: row.next_loser_key || null,
      status: row.status || 'pending',
      score_a: row.score_a || '',
      score_b: row.score_b || '',
      winner_name: row.winner_name || '',
      winner_team_id: row.winner_team_id || null,
      date: row.date || '',
      time: row.time || '',
      venue: row.venue || '',
      playing_area: row.playing_area || '',
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
      $('#schedule-preview').innerHTML = `<table><thead><tr><th>Game</th><th>Matchup</th><th>Date</th><th>Time</th><th>Venue</th></tr></thead><tbody>${
        schedulable.map(row => `<tr data-match-key="${escapeHtml(row.match_key)}" data-date="${row.date}" data-time="${row.time}" data-venue="${escapeHtml(row.venue)}" data-area="${escapeHtml(row.playing_area || row.venue || '')}">
          <td>Game ${row.match_number}</td>
          <td>${escapeHtml(row.dependency_label || `${row.team_a} vs ${row.team_b}`)}</td>
          <td>${editable ? `<input data-date type="date" value="${row.date}">` : row.date}</td>
          <td>${editable ? `<input data-time type="time" value="${row.time}">` : row.time}</td>
          <td>${editable ? `<input data-venue value="${escapeHtml(row.venue)}">` : escapeHtml(row.venue)}</td>
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
      ['Pairing', 'Random Draw'],
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
  $('#bracket-zoom-in')?.addEventListener('click', () => setBracketZoom(bracketZoom + 0.1));
  $('#bracket-zoom-out')?.addEventListener('click', () => setBracketZoom(bracketZoom - 0.1));
  $('#bracket-zoom-reset')?.addEventListener('click', fitBracketZoom);
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
    invalidateBlueprint('Team selection changed. Generate a new authoritative preview.');
  }));
  $$('[name="tournament_type"]').forEach(input => input.addEventListener('change', () => {
    legacyFormatPending = false;
    $('#legacy-format-notice').hidden = true;
    $('#generate-bracket').disabled = false;
    syncThirdPlaceVisibility();
    invalidateBlueprint('Format changed. Generate a new authoritative preview.');
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
