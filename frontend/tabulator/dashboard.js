(function () {
  'use strict';
  var refreshing = false;
  var pendingRefresh = null;
  var currentUrl = new URL(window.location.href);
  var main = function () { return document.querySelector('[data-dashboard-main]'); };

  function request(url, accept) {
    var controller = new AbortController();
    var timeout = window.setTimeout(function () { controller.abort(); }, 12000);
    return fetch(url, {credentials: 'same-origin', headers: {Accept: accept}, signal: controller.signal})
      .then(function (response) {
        if (!response.ok || response.redirected) throw new Error('Unable to retrieve dashboard data.');
        return accept === 'text/html' ? response.text() : response.json();
      }).finally(function () { window.clearTimeout(timeout); });
  }

  function syncNavigation() {
    var selected = document.querySelector('[data-dashboard-select]');
    if (!selected) return;
    document.querySelectorAll('.et-nav-link').forEach(function (link) {
      var url = new URL(link.href, window.location.origin);
      url.searchParams.set('event_id', selected.value);
      link.href = url.toString();
    });
  }

  function refreshDashboard(url, navigate) {
    if (!main()) return;
    if (refreshing) {
      pendingRefresh = {url: url, navigate: navigate};
      if (navigate) {
        main().classList.add('dashboard-loading');
        main().setAttribute('aria-busy', 'true');
      }
      return;
    }
    refreshing = true;
    var previous = main();
    var error = previous.querySelector('[data-dashboard-error]');
    error.hidden = true;
    if (navigate) {
      previous.classList.add('dashboard-loading');
      previous.setAttribute('aria-busy', 'true');
      var busySelect = previous.querySelector('[data-dashboard-select]');
      if (busySelect) busySelect.disabled = true;
    }
    request(url.toString(), 'text/html')
      .then(function (html) {
        if (pendingRefresh) return;
        var parsed = new DOMParser().parseFromString(html, 'text/html');
        var replacement = parsed.querySelector('body[data-page="dashboard"] [data-dashboard-main]');
        if (!replacement) throw new Error('The dashboard could not be refreshed.');
        var active = document.activeElement;
        var activeId = previous.contains(active) ? active.id : '';
        previous.replaceWith(document.importNode(replacement, true));
        var status = parsed.querySelector('[data-dashboard-status]');
        var currentStatus = document.querySelector('[data-dashboard-status]');
        if (status && currentStatus) currentStatus.replaceChildren.apply(currentStatus, Array.from(status.childNodes).map(function (node) { return document.importNode(node, true); }));
        currentUrl = new URL(url.toString());
        if (navigate) window.history.pushState({}, '', currentUrl.toString());
        syncNavigation();
        var label = main().querySelector('[data-refresh-label]');
        if (label) label.textContent = 'Updated ' + new Date().toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'});
        if (activeId) {
          var restored = document.getElementById(activeId);
          if (restored) restored.focus({preventScroll: true});
        }
      }).catch(function () {
        if (pendingRefresh) return;
        previous.classList.remove('dashboard-loading');
        previous.removeAttribute('aria-busy');
        error.hidden = false;
        error.querySelector('span').textContent = navigate ? 'Unable to load the selected event. Showing the previous event. Please retry.' : 'Unable to refresh the dashboard. Showing the last available data.';
        error.querySelector('[data-dashboard-retry]').dataset.retryUrl = url.toString();
        error.querySelector('[data-dashboard-retry]').dataset.navigate = navigate ? 'true' : 'false';
        var selected = previous.querySelector('[data-dashboard-select]');
        if (selected) {
          selected.disabled = false;
          var originalOption = selected.querySelector('option[selected]');
          selected.value = selected.dataset.currentEvent || (originalOption ? originalOption.value : selected.value);
        }
      }).finally(function () {
      refreshing = false;
      if (pendingRefresh) {
        var next = pendingRefresh;
        pendingRefresh = null;
        refreshDashboard(next.url, next.navigate);
      }
    });
  }

  document.addEventListener('change', function (event) {
    if (!event.target.matches('[data-dashboard-select]')) return;
    var url = new URL(event.target.form.action, window.location.origin);
    url.searchParams.set('event_id', event.target.value);
    refreshDashboard(url, true);
  });
  document.addEventListener('submit', function (event) {
    if (!event.target.matches('[data-event-form]')) return;
    event.preventDefault();
    var url = new URL(event.target.action, window.location.origin);
    url.searchParams.set('event_id', event.target.elements.event_id.value);
    refreshDashboard(url, true);
  });
  document.addEventListener('click', function (event) {
    var link = event.target.closest('[data-dashboard-event]');
    if (link && !event.ctrlKey && !event.metaKey && !event.shiftKey && !event.altKey && event.button === 0) {
      event.preventDefault();
      refreshDashboard(new URL(link.href), true);
    }
    var retry = event.target.closest('[data-dashboard-retry]');
    if (retry) refreshDashboard(new URL(retry.dataset.retryUrl || currentUrl), retry.dataset.navigate === 'true');
  });
  window.addEventListener('popstate', function () { refreshDashboard(new URL(window.location.href), false); });
  window.setInterval(function () {
    if (!document.hidden && !document.querySelector('.dashboard-profile[open]')) refreshDashboard(currentUrl, false);
  }, 30000);
  document.addEventListener('visibilitychange', function () { if (!document.hidden) refreshDashboard(currentUrl, false); });
  syncNavigation();
}());
