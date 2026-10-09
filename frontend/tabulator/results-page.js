(function () {
  'use strict';
  var main = document.querySelector('[data-results-main]');
  if (!main) return;
  var controller = null, requestId = 0, pollTimer = null, retryUrl = location.href, tabRevision = 0;
  function currentTab() { var selected = main.querySelector('[data-results-tab][aria-selected="true"]'); return selected ? selected.dataset.resultsTab : 'live'; }
  function polling() {
    clearInterval(pollTimer); pollTimer = null;
    var enabled = !document.hidden && currentTab() === 'live' && !!main.querySelector('[data-results-filters]');
    var indicator = main.querySelector('[data-results-refresh]'); if (indicator) indicator.hidden = !enabled;
    if (enabled) pollTimer = setInterval(function () { if (!controller) load(location.href, false, true); }, 30000);
  }
  function activate(name, updateHistory) {
    var tabs = main.querySelectorAll('[data-results-tab]');
    if (!Array.from(tabs).some(function (el) { return el.dataset.resultsTab === name; })) name = main.dataset.defaultTab || 'live';
    tabs.forEach(function (el) { var active = el.dataset.resultsTab === name; el.classList.toggle('active', active); el.setAttribute('aria-selected', String(active)); el.tabIndex = active ? 0 : -1; });
    main.querySelectorAll('[data-results-panel]').forEach(function (el) { var active = el.dataset.resultsPanel === name; el.hidden = !active; el.classList.toggle('active', active); });
    var input = main.querySelector('[data-results-tab-input]'); if (input) input.value = name;
    main.querySelectorAll('[data-results-print], [data-results-export]').forEach(function (el) { var url = new URL(el.href); url.searchParams.set('tab', name); el.href = url.href; });
    var exportLink = main.querySelector('[data-results-export]');
    if (exportLink) {
      var available = main.dataset['csv' + name.charAt(0).toUpperCase() + name.slice(1)] === 'true';
      exportLink.classList.toggle('disabled', !available);
      exportLink.setAttribute('aria-disabled', String(!available));
      exportLink.tabIndex = available ? 0 : -1;
    }
    if (updateHistory) { tabRevision += 1; var url = new URL(location.href); url.searchParams.set('tab', name); history.replaceState(null, '', url.href); }
    polling();
  }
  async function load(url, pushHistory, background) {
    retryUrl = url;
    if (controller) controller.abort();
    var localController = new AbortController(), id = ++requestId, startingTabRevision = tabRevision;
    controller = localController;
    var timeout = setTimeout(function () { localController.abort(); }, 15000);
    if (!background) { main.classList.add('is-loading'); main.setAttribute('aria-busy', 'true'); }
    try {
      var response = await fetch(url, { credentials: 'same-origin', headers: { Accept: 'text/html' }, signal: localController.signal });
      if (!response.ok) throw new Error('Request failed');
      var doc = new DOMParser().parseFromString(await response.text(), 'text/html');
      var next = doc.querySelector('[data-results-main]');
      if (!next) { if (response.redirected) { location.assign(response.url); return; } throw new Error('Invalid results response'); }
      if (id !== requestId) return;
      var targetUrl = new URL(url, location.origin);
      var selectedTab = background || startingTabRevision !== tabRevision ? currentTab() : targetUrl.searchParams.get('tab') || next.dataset.defaultTab;
      if (startingTabRevision !== tabRevision) targetUrl.searchParams.set('tab', selectedTab);
      // Keep an open review checklist when refreshed.
      var openReview = main.querySelector('.results-review[open]');
      if (openReview && next.querySelector('.results-review')) next.querySelector('.results-review').open = true;
      main.replaceWith(next); main = next;
      var selectedEvent = main.querySelector('[name=event_id]'); if (selectedEvent) document.querySelectorAll('.et-nav-link').forEach(function (link) { var navUrl = new URL(link.href); if (navUrl.searchParams.has('event_id')) { navUrl.searchParams.set('event_id', selectedEvent.value); link.href = navUrl.href; } });
      if (pushHistory) history.pushState(null, '', targetUrl.href);
      activate(selectedTab, false);
    } catch (error) {
      if (id !== requestId) return;
      main.querySelectorAll('[data-results-filters] select').forEach(function (select) { var selected = select.querySelector('option[selected]') || select.options[0]; if (selected) select.value = selected.value; });
      var alert = main.querySelector('[data-results-error]'); if (alert) { alert.hidden = false; alert.querySelector('span').textContent = background ? 'Unable to refresh. Showing the last available results.' : 'Unable to load results. Check your connection and try again.'; }
      var indicator = main.querySelector('[data-results-refresh]'); if (indicator) indicator.hidden = true;
    } finally {
      clearTimeout(timeout);
      if (id === requestId) { controller = null; main.classList.remove('is-loading'); main.removeAttribute('aria-busy'); }
    }
  }
  document.addEventListener('change', function (event) {
    var form = event.target.closest('[data-results-filters]'); if (!form) return;
    if (event.target.name === 'event_id') { form.elements.round_id.value = ''; form.elements.category.value = ''; }
    if (event.target.name === 'round_id') form.elements.category.value = '';
    var url = new URL(form.action); url.search = new URLSearchParams(new FormData(form)).toString(); if (event.target.name === 'event_id') { url.searchParams.delete('round_id'); url.searchParams.delete('category'); url.searchParams.delete('tab'); } load(url.href, true, false);
  });
  document.addEventListener('submit', function (event) {
    if (event.target.matches('[data-results-filters]')) { event.preventDefault(); var url = new URL(event.target.action); url.search = new URLSearchParams(new FormData(event.target)).toString(); load(url.href, true, false); }
    if (event.target.matches('[data-results-confirm]') && !window.confirm('Confirm that you have reviewed these complete results?')) event.preventDefault();
  });
  document.addEventListener('click', function (event) {
    var tab = event.target.closest('[data-results-tab]'); if (tab) { activate(tab.dataset.resultsTab, true); return; }
    if (event.target.closest('[data-results-export][aria-disabled="true"]')) event.preventDefault();
    if (event.target.closest('[data-results-retry]')) load(retryUrl, retryUrl !== location.href, false);
  });
  document.addEventListener('keydown', function (event) {
    if (!event.target.matches('[data-results-tab]') || !['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault(); var tabs = Array.from(main.querySelectorAll('[data-results-tab]')), index = tabs.indexOf(event.target);
    index = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : (index + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
    activate(tabs[index].dataset.resultsTab, true); tabs[index].focus();
  });
  window.addEventListener('popstate', function () { load(location.href, false, false); });
  document.addEventListener('visibilitychange', polling);
  activate(main.dataset.defaultTab, false);
})();


