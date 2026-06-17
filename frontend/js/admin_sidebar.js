document.addEventListener('DOMContentLoaded', function () {
  var toggle = document.getElementById('accounts-nav-toggle');
  var group = document.getElementById('accounts-nav-group');
  if (!toggle || !group) return;

  var storageKey = 'eventtab_accounts_nav_open';

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
});
