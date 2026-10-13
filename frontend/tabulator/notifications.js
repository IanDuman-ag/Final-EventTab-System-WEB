(function () {
  'use strict';
  if (window.eventTabNotificationsBound) return;
  window.eventTabNotificationsBound = true;
  document.addEventListener('click', function (event) {
    document.querySelectorAll('[data-assignment-notifications][open]').forEach(function (dropdown) {
      if (!dropdown.contains(event.target)) dropdown.open = false;
    });
  });
  document.addEventListener('keydown', function (event) {
    if (event.key !== 'Escape') return;
    document.querySelectorAll('[data-assignment-notifications][open]').forEach(function (dropdown) {
      dropdown.open = false;
      dropdown.querySelector('summary').focus();
    });
  });
}());
