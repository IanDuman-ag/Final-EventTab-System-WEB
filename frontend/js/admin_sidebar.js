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

  initNavGroup('accounts-nav-toggle', 'accounts-nav-group', 'eventtab_accounts_nav_open');
  initNavGroup('participants-nav-toggle', 'participants-nav-group', 'eventtab_participants_nav_open');
});
