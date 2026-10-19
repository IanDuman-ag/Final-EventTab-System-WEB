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
