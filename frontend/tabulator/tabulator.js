(function () {
  'use strict';

  function qs(sel, root) {
    return (root || document).querySelector(sel);
  }

  function qsa(sel, root) {
    return Array.prototype.slice.call((root || document).querySelectorAll(sel));
  }

  function currentParams() {
    return new URLSearchParams(window.location.search);
  }

  function withParams(url, params) {
    var u = new URL(url, window.location.origin);
    Object.keys(params || {}).forEach(function (key) {
      var val = params[key];
      if (val === undefined || val === null || val === '') u.searchParams.delete(key);
      else u.searchParams.set(key, val);
    });
    return u.pathname + u.search;
  }

  function initSidebar() {
    var sidebar = qs('#etSidebar');
    var backdrop = qs('#etBackdrop');
    var btn = qs('#etMenuBtn');
    if (!sidebar || !btn) return;

    function close() {
      sidebar.classList.remove('is-open');
      if (backdrop) {
        backdrop.classList.remove('is-open');
        backdrop.hidden = true;
      }
    }

    function open() {
      sidebar.classList.add('is-open');
      if (backdrop) {
        backdrop.hidden = false;
        backdrop.classList.add('is-open');
      }
    }

    btn.addEventListener('click', function () {
      if (sidebar.classList.contains('is-open')) close();
      else open();
    });
    if (backdrop) backdrop.addEventListener('click', close);
  }

  function initFilters() {
    var form = qs('#etFilters');
    if (!form) return;
    qsa('[data-filter]', form).forEach(function (el) {
      el.addEventListener('change', function () {
        form.submit();
      });
    });
  }

  function initTabs() {
    var tabs = qsa('.res-tab');
    if (!tabs.length) return;
    tabs.forEach(function (tab) {
      tab.addEventListener('click', function () {
        var key = tab.getAttribute('data-tab');
        tabs.forEach(function (t) { t.classList.toggle('is-active', t === tab); });
        qsa('.res-pane').forEach(function (pane) {
          pane.hidden = pane.getAttribute('data-pane') !== key;
        });
        var params = currentParams();
        params.set('tab', key);
        var next = window.location.pathname + '?' + params.toString();
        window.history.replaceState({}, '', next);
      });
    });
  }

  function medalHtml(rank) {
    if (rank === 1) return '<span class="et-medal gold">1</span>';
    if (rank === 2) return '<span class="et-medal silver">2</span>';
    if (rank === 3) return '<span class="et-medal bronze">3</span>';
    if (rank) return '<span class="et-medal plain">' + rank + '</span>';
    return '<span class="et-medal plain">—</span>';
  }

  function renderLiveRows(payload) {
    var tbody = qs('#etLiveTable tbody');
    if (!tbody || !payload) return;
    var mode = payload.mode;
    var rows = payload.results || [];
    if (!rows.length) {
      tbody.innerHTML = '<tr><td colspan="8" class="et-empty">No live standings yet.</td></tr>';
      return;
    }
    tbody.innerHTML = rows.map(function (row) {
      if (mode === 'ranking') {
        var id = row.team_id;
        return (
          '<tr data-row-id="' + id + '">' +
            '<td>' + medalHtml(row.rank) + '</td>' +
            '<td><strong>' + escapeHtml(row.name) + '</strong><div class="live-details">' + escapeHtml(row.department || '') + '</div></td>' +
            '<td>' + row.wins + '</td><td>' + row.losses + '</td>' +
            '<td class="res-score">' + row.points + '</td>' +
            '<td>' + row.pf + '</td><td>' + row.pa + '</td>' +
            '<td><button type="button" class="et-expand" data-expand="' + id + '">Details</button></td>' +
          '</tr>' +
          '<tr class="live-row-details" data-details-for="' + id + '" hidden><td colspan="8">' +
            escapeHtml(row.prize || '') + ' · ' + row.wins + ' wins · ' + row.losses + ' losses' +
          '</td></tr>'
        );
      }
      var cid = row.candidate_id;
      return (
        '<tr data-row-id="' + cid + '">' +
          '<td>' + medalHtml(row.rank) + '</td>' +
          '<td><strong>' + (row.number ? '#' + row.number + ' ' : '') + escapeHtml(row.name) + '</strong>' +
            '<div class="live-details">' + escapeHtml(row.breakdown || '') + '</div></td>' +
          '<td>' + escapeHtml(row.department || '') + '</td>' +
          '<td class="res-score">' + (row.final_score != null ? row.final_score : '—') + '</td>' +
          '<td>' + escapeHtml(row.prize || '—') + '</td>' +
          '<td><button type="button" class="et-expand" data-expand="' + cid + '">Details</button></td>' +
        '</tr>' +
        '<tr class="live-row-details" data-details-for="' + cid + '" hidden><td colspan="6">' +
          escapeHtml(row.qualification_status || '—') + ' · ' + escapeHtml(row.breakdown || '') +
        '</td></tr>'
      );
    }).join('');
    bindExpand();
  }

  function escapeHtml(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function bindExpand() {
    qsa('.et-expand').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var id = btn.getAttribute('data-expand');
        var row = qs('[data-details-for="' + id + '"]');
        if (row) row.hidden = !row.hidden;
      });
    });
  }

  function initAutoRefresh() {
    var toggle = qs('#etAutoRefresh');
    var root = qs('[data-refresh-url]');
    if (!root) return;
    var url = root.getAttribute('data-refresh-url');
    var eventId = root.getAttribute('data-event-id');
    if (!url || !eventId) return;

    var timer = null;
    function tick() {
      var params = currentParams();
      params.set('event_id', eventId);
      fetch(withParams(url, {
        event_id: eventId,
        category: params.get('category') || '',
        q: params.get('q') || '',
        tab: params.get('tab') || ''
      }), { headers: { 'Accept': 'application/json' } })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (!data || !data.success) return;
          var payload = data.payload || data;
          window.ET_PAYLOAD = payload;
          if (qs('#etLiveTable')) renderLiveRows(payload);
          var stamp = qs('#etUpdatedAt');
          if (stamp) {
            stamp.textContent = 'Updated ' + new Date(data.updated_at || Date.now()).toLocaleTimeString();
          }
        })
        .catch(function () { /* keep current UI */ });
    }

    function sync() {
      if (timer) {
        clearInterval(timer);
        timer = null;
      }
      if (!toggle || toggle.checked) {
        tick();
        timer = setInterval(tick, 15000);
      }
    }

    if (toggle) toggle.addEventListener('change', sync);
    if (document.body.getAttribute('data-page') === 'live' || document.body.getAttribute('data-page') === 'dashboard') {
      sync();
    }
  }

  function initPrint() {
    var btn = qs('#etPrintBtn');
    if (btn) btn.addEventListener('click', function () { window.print(); });
  }

  function initExportLink() {
    var link = qs('#etExportCsv');
    var root = qs('#etResultsApp');
    if (!link || !root) return;
    var base = root.getAttribute('data-csv-url');
    if (!base) return;
    var params = currentParams();
    link.href = withParams(base, {
      event_id: root.getAttribute('data-event-id'),
      category: params.get('category') || '',
      q: params.get('q') || ''
    });
  }

  document.addEventListener('DOMContentLoaded', function () {
    initSidebar();
    initFilters();
    initTabs();
    bindExpand();
    initAutoRefresh();
    initPrint();
    initExportLink();
  });
})();
