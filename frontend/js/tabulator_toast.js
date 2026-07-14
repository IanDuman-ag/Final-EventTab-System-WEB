/**
 * Toast + confirm toast for the Tabulator portal.
 * Usage:
 *   TabToast.success('Result approved');
 *   TabToast.error('Network error');
 *   const ok = await TabToast.confirm('Approve this result?', { confirmLabel: 'Approve', danger: false });
 */
(function (global) {
  'use strict';

  var host = null;
  var confirmBusy = false;

  function ensureHost() {
    if (host && document.body.contains(host)) return host;
    host = document.createElement('div');
    host.className = 'tab-toast-host';
    host.setAttribute('aria-live', 'polite');
    document.body.appendChild(host);
    return host;
  }

  function show(message, type, opts) {
    opts = opts || {};
    var duration = opts.duration != null ? opts.duration : 3200;
    var el = document.createElement('div');
    el.className = 'tab-toast tab-toast--' + (type || 'info');
    el.setAttribute('role', 'status');

    var icon = document.createElement('span');
    icon.className = 'tab-toast-icon';
    icon.setAttribute('aria-hidden', 'true');
    if (type === 'success') icon.textContent = '✓';
    else if (type === 'error') icon.textContent = '!';
    else if (type === 'warning') icon.textContent = '!';
    else icon.textContent = 'i';

    var text = document.createElement('span');
    text.className = 'tab-toast-text';
    text.textContent = message || '';

    var close = document.createElement('button');
    close.type = 'button';
    close.className = 'tab-toast-close';
    close.setAttribute('aria-label', 'Dismiss');
    close.innerHTML = '&times;';

    el.appendChild(icon);
    el.appendChild(text);
    el.appendChild(close);
    ensureHost().appendChild(el);

    requestAnimationFrame(function () {
      el.classList.add('is-visible');
    });

    var timer = null;
    function dismiss() {
      if (timer) clearTimeout(timer);
      el.classList.remove('is-visible');
      setTimeout(function () {
        if (el.parentNode) el.parentNode.removeChild(el);
      }, 280);
    }

    close.addEventListener('click', dismiss);
    if (duration > 0) timer = setTimeout(dismiss, duration);
    return { dismiss: dismiss, el: el };
  }

  function confirm(message, opts) {
    opts = opts || {};
    if (confirmBusy) return Promise.resolve(false);
    confirmBusy = true;

    return new Promise(function (resolve) {
      var el = document.createElement('div');
      el.className = 'tab-toast tab-toast--confirm' + (opts.danger ? ' tab-toast--danger' : '');
      el.setAttribute('role', 'alertdialog');
      el.setAttribute('aria-modal', 'true');

      var icon = document.createElement('span');
      icon.className = 'tab-toast-icon';
      icon.setAttribute('aria-hidden', 'true');
      icon.textContent = opts.danger ? '!' : '?';

      var body = document.createElement('div');
      body.className = 'tab-toast-body';

      var title = document.createElement('strong');
      title.className = 'tab-toast-title';
      title.textContent = opts.title || (opts.danger ? 'Confirm reject' : 'Confirm approve');

      var text = document.createElement('span');
      text.className = 'tab-toast-text';
      text.textContent = message || 'Are you sure?';

      body.appendChild(title);
      body.appendChild(text);

      var actions = document.createElement('div');
      actions.className = 'tab-toast-actions';

      var cancelBtn = document.createElement('button');
      cancelBtn.type = 'button';
      cancelBtn.className = 'tab-toast-btn tab-toast-btn--ghost';
      cancelBtn.textContent = opts.cancelLabel || 'Cancel';

      var okBtn = document.createElement('button');
      okBtn.type = 'button';
      okBtn.className = 'tab-toast-btn ' + (opts.danger ? 'tab-toast-btn--danger' : 'tab-toast-btn--primary');
      okBtn.textContent = opts.confirmLabel || 'Confirm';

      actions.appendChild(cancelBtn);
      actions.appendChild(okBtn);

      el.appendChild(icon);
      el.appendChild(body);
      el.appendChild(actions);
      ensureHost().appendChild(el);

      requestAnimationFrame(function () {
        el.classList.add('is-visible');
        okBtn.focus();
      });

      function finish(result) {
        confirmBusy = false;
        el.classList.remove('is-visible');
        setTimeout(function () {
          if (el.parentNode) el.parentNode.removeChild(el);
        }, 280);
        document.removeEventListener('keydown', onKey);
        resolve(result);
      }

      function onKey(e) {
        if (e.key === 'Escape') finish(false);
        if (e.key === 'Enter') finish(true);
      }

      cancelBtn.addEventListener('click', function () { finish(false); });
      okBtn.addEventListener('click', function () { finish(true); });
      document.addEventListener('keydown', onKey);
    });
  }

  global.TabToast = {
    show: show,
    success: function (msg, opts) { return show(msg, 'success', opts); },
    error: function (msg, opts) { return show(msg, 'error', opts); },
    warning: function (msg, opts) { return show(msg, 'warning', opts); },
    info: function (msg, opts) { return show(msg, 'info', opts); },
    confirm: confirm,
  };
})(window);
