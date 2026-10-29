document.addEventListener('DOMContentLoaded', function () {
  function initNavGroup(toggleId, groupId, storageKey) {
    var toggle = document.getElementById(toggleId);
    var group = document.getElementById(groupId);
    if (!toggle || !group) return;

    function setOpen(open) {
      group.classList.toggle('is-open', open);
      toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
      sessionStorage.setItem(storageKey, open ? 'true' : 'false');
    }

    var hasActiveChild = !!group.querySelector('.v2-nav-sub a.active');
    if (hasActiveChild) {
      setOpen(true);
    } else {
      var saved = sessionStorage.getItem(storageKey);
      setOpen(saved === 'true');
    }

    toggle.addEventListener('click', function (e) {
      e.preventDefault();
      e.stopPropagation();
      setOpen(!group.classList.contains('is-open'));
    });
  }

  initNavGroup('settings-nav-toggle', 'settings-nav-group', 'eventtab_settings_nav_open');
  initNavGroup('events-nav-toggle', 'events-nav-group', 'eventtab_events_nav_open');
  initNavGroup('accounts-nav-toggle', 'accounts-nav-group', 'eventtab_accounts_nav_open');
  initNavGroup('participants-nav-toggle', 'participants-nav-group', 'eventtab_participants_nav_open');
});

/* Landing-page-style mobile navigation shared by all dashboard portals. */
document.addEventListener('DOMContentLoaded', function () {
  var sidebar = document.querySelector('aside[data-portal-mobile]');
  if (!sidebar) return;
  document.body.classList.add('portal-nav-ready');
  sidebar.classList.add('portal-mobile-sidebar');
  if (!sidebar.id) sidebar.id = 'portal-mobile-navigation';
  var header = document.createElement('header');
  header.className = 'portal-mobile-header';
  var brand = document.createElement('a');
  brand.className = 'portal-mobile-brand';
  brand.href = sidebar.querySelector('nav a[href]').href;
  var logo = document.createElement('img');
  logo.src = sidebar.dataset.portalLogo;
  logo.alt = '';
  logo.width = 44;
  logo.height = 44;
  var name = document.createElement('span');
  name.append(document.createTextNode('Event'));
  var accent = document.createElement('b');
  accent.textContent = 'Tab';
  name.append(accent);
  brand.append(logo, name);
  var toggle = document.createElement('button');
  toggle.type = 'button';
  toggle.className = 'portal-mobile-toggle';
  toggle.setAttribute('aria-controls', sidebar.id);
  toggle.setAttribute('aria-expanded', 'false');
  toggle.setAttribute('aria-label', 'Open navigation');
  for (var i = 0; i < 3; i++) {
    var line = document.createElement('span');
    line.setAttribute('aria-hidden', 'true');
    toggle.append(line);
  }
  header.append(brand, toggle);
  document.body.prepend(header);
  var media = window.matchMedia('(max-width:960px)');
  function setOpen(open) {
    open = media.matches && open;
    document.body.classList.toggle('portal-menu-open', open);
    toggle.setAttribute('aria-expanded', String(open));
    toggle.setAttribute('aria-label', open ? 'Close navigation' : 'Open navigation');
    sidebar.inert = media.matches && !open;
    if (!open) sidebar.scrollTop = 0;
  }
  toggle.addEventListener('click', function () { setOpen(toggle.getAttribute('aria-expanded') !== 'true'); });
  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape' && toggle.getAttribute('aria-expanded') === 'true') {
      setOpen(false);
      toggle.focus();
    }
  });
  document.addEventListener('click', function (event) {
    if (!sidebar.contains(event.target) && !header.contains(event.target)) setOpen(false);
  });
  sidebar.querySelectorAll('a[href]').forEach(function (link) {
    link.addEventListener('click', function () { setOpen(false); });
  });
  media.addEventListener('change', function () { setOpen(false); });
  setOpen(false);
});

/* Live dashboard notifications; Tabulator keeps its existing assignment bell. */
document.addEventListener('DOMContentLoaded', function () {
  var sidebar = document.querySelector('[data-portal-notifications]');
  if (!sidebar) return;
  var header = document.querySelector('.v2-topbar, .tab-topbar, .topbar, .criteria-header');
  if (!header) return;
  var widget = document.createElement('details');
  widget.className = 'portal-notifications';
  var trigger = document.createElement('summary');
  trigger.setAttribute('aria-label', 'Notifications');
  trigger.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3a6 6 0 0 0-6 6v4l-2 3v2h16v-2l-2-3V9a6 6 0 0 0-6-6Zm-3 17h6a3 3 0 0 1-6 0Z"/></svg>';
  var badge = document.createElement('span');
  badge.className = 'portal-notification-count';
  badge.hidden = true;
  trigger.append(badge);
  var panel = document.createElement('section');
  panel.className = 'portal-notification-panel';
  panel.setAttribute('aria-label', 'Recent notifications');
  var title = document.createElement('strong');title.textContent = 'Notifications';
  var caption = document.createElement('p');caption.textContent = 'Recent updates';
  var list = document.createElement('ul');
  list.setAttribute('aria-live', 'polite');
  var retry = document.createElement('button');retry.type = 'button';retry.textContent = 'Retry';retry.hidden = true;
  panel.append(title, caption, list, retry);
  widget.append(trigger, panel);
  var existing = header.querySelector('.v2-bell, .events-bell, [aria-label="Notifications"], [aria-label="Audit logs"], [aria-label="View system activity"]');
  if (existing) existing.replaceWith(widget);
  else {
    var actions = header.querySelector('.topbar-actions, .v2-topbar-right, .events-account');
    if (!actions) {
      actions = document.createElement('div');actions.className = 'portal-header-actions';
      var date = header.querySelector('.fac-dashboard-date');
      if (date) actions.append(date);
      header.append(actions);
    }
    actions.prepend(widget);
  }
  var home = document.createComment('notification position');
  widget.before(home);
  var media = window.matchMedia('(max-width:960px)');
  function positionBell() {
    var mobile = document.querySelector('.portal-mobile-header');
    if (media.matches && mobile) mobile.insertBefore(widget, mobile.querySelector('.portal-mobile-toggle'));
    else home.after(widget);
    widget.open = false;
  }
  media.addEventListener('change', positionBell);
  positionBell();
  var busy = false;
  async function refresh() {
    if (busy) return;
    busy = true;retry.hidden = true;
    if (!list.children.length) list.textContent = 'Loading notifications...';
    try {
      var response = await fetch(sidebar.dataset.portalNotifications, {headers: {'Accept': 'application/json'}, cache: 'no-store'});
      if (!response.ok) throw new Error(response.status === 401 ? 'Please sign in again to view notifications.' : 'Unable to load notifications. Please try again.');
      var data = await response.json();
      list.replaceChildren();
      (data.notifications || []).forEach(function (note) {
        var item = document.createElement('li');var link = document.createElement('a');
        var url = new URL(note.url, window.location.origin);
        if (url.origin !== window.location.origin) return;
        link.href = url.href;
        var heading = document.createElement('strong');heading.textContent = note.title;
        var message = document.createElement('span');message.textContent = note.message;
        var time = document.createElement('time');time.dateTime = note.timestamp;
        time.textContent = new Date(note.timestamp).toLocaleString();
        link.append(heading, message, time);item.append(link);list.append(item);
      });
      if (!list.children.length) {
        var empty = document.createElement('li');empty.className = 'portal-notification-empty';empty.textContent = 'No notifications yet.';list.append(empty);
      }
      badge.textContent = String(data.count || 0);badge.hidden = !data.count;
      trigger.setAttribute('aria-label', 'Notifications: ' + (data.count || 0) + ' recent updates');
    } catch (error) {
      list.textContent = error.message || 'Unable to load notifications. Please try again.';
      retry.hidden = false;
    } finally { busy = false; }
  }
  retry.addEventListener('click', refresh);
  widget.addEventListener('toggle', function () {
    if (!widget.open) return;
    if (document.body.classList.contains('portal-menu-open')) document.querySelector('.portal-mobile-toggle').click();
    refresh();
  });
  document.addEventListener('click', function (event) { if (!widget.contains(event.target)) widget.open = false; });
  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape' && widget.open) { widget.open = false;trigger.focus(); }
  });
  document.addEventListener('visibilitychange', function () { if (!document.hidden) refresh(); });
  window.setInterval(function () { if (!document.hidden) refresh(); }, 60000);
  refresh();
});
