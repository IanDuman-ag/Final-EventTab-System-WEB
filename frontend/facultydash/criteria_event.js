(() => {
  'use strict';
  const page = document.querySelector('.criteria-page');
  if (!page) return;
  const dateFormat = new Intl.DateTimeFormat('en-PH', {
    timeZone: 'Asia/Manila', month: 'short', day: 'numeric', year: 'numeric',
    hour: 'numeric', minute: '2-digit',
  });
  document.querySelectorAll('[data-local-time]').forEach(time => {
    const value = new Date(time.dateTime);
    if (!Number.isNaN(value.getTime())) time.textContent = dateFormat.format(value);
  });
  let posting = false;
  let fetching = false;
  let refreshPending = false;
  const notice = document.getElementById('criteria-sync');
  const dialogs = [...document.querySelectorAll('.criteria-dialog')];
  const isDialogOpen = () => dialogs.some(dialog => dialog.open);
  const refresh = () => {
    if (posting || isDialogOpen()) { refreshPending = true; return; }
    window.location.reload();
  };
  document.querySelectorAll('[data-open-dialog]').forEach(button => {
    button.addEventListener('click', () => {
      const dialog = document.getElementById(button.dataset.openDialog);
      if (dialog && !button.disabled) dialog.showModal();
    });
  });
  dialogs.forEach(dialog => {
    dialog.querySelectorAll('[data-close-dialog]').forEach(button => {
      button.addEventListener('click', () => dialog.close());
    });
    dialog.addEventListener('close', () => { if (refreshPending) refresh(); });
    dialog.addEventListener('click', event => {
      const rect = dialog.getBoundingClientRect();
      if (event.target === dialog && (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom)) dialog.close();
    });
    dialog.querySelector('form')?.addEventListener('submit', event => {
      if (posting) { event.preventDefault(); return; }
      posting = true;
      const button = event.submitter;
      if (button) {
        button.disabled = true;
        button.setAttribute('aria-busy', 'true');
        const spinner = document.createElement('span');
        spinner.className = 'criteria-spinner';
        spinner.setAttribute('aria-hidden', 'true');
        button.textContent = button.dataset.loadingLabel || 'Saving…';
        button.prepend(spinner);
      }
      dialog.querySelectorAll('[data-close-dialog]').forEach(button => { button.disabled = true; });
    });
    dialog.addEventListener('cancel', event => { if (posting) event.preventDefault(); });
  });
  document.querySelectorAll('[data-round-select]').forEach(select => {
    select.addEventListener('change', () => select.form.requestSubmit());
  });
  const poll = async () => {
    if (document.hidden || posting || fetching || isDialogOpen()) return;
    fetching = true;
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 10000);
    try {
      const url = new URL(window.location.href);
      url.searchParams.set('format', 'json');
      url.searchParams.set('round', page.dataset.round);
      const response = await fetch(url, {credentials: 'same-origin', headers: {'Accept': 'application/json'}, signal: controller.signal});
      if (!response.ok || !response.headers.get('content-type')?.includes('application/json')) throw new Error('Refresh unavailable');
      const data = await response.json();
      const portal = data.portal || data;
      notice.hidden = true;
      if (portal.revision && portal.revision !== page.dataset.revision) refresh();
    } catch (_) {
      notice.textContent = 'Live updates are temporarily unavailable. Your current data is still shown; refresh to check the latest submissions.';
      notice.hidden = false;
    } finally {
      window.clearTimeout(timeout);
      fetching = false;
    }
  };
  window.setInterval(poll, 15000);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) poll(); });
  window.addEventListener('pageshow', event => { if (event.persisted && posting) window.location.reload(); });
})();
