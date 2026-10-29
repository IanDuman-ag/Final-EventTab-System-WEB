(() => {
  'use strict';
  const bar = document.getElementById('event-bulk-actions');
  if (!bar || document.body.classList.contains('individual-workspace')) return;
  const allIds = JSON.parse(document.getElementById('bulk-event-ids').textContent).map(String);
  const selected = new Set();
  const rows = [...document.querySelectorAll('.event-select')];
  const pageSelect = document.getElementById('events-page-select');
  const selectAll = document.getElementById('events-select-all');
  const clear = document.getElementById('events-clear-selection');
  const remove = document.getElementById('events-delete-selected');
  const dialog = document.getElementById('events-bulk-confirm');
  const confirm = document.getElementById('events-bulk-confirm-delete');
  const cancel = document.getElementById('events-bulk-cancel');
  const error = document.getElementById('events-bulk-error');
  let busy = false;
  function update() {
    rows.forEach(row => { row.checked = selected.has(row.value); row.disabled = busy; });
    pageSelect.checked = rows.length > 0 && rows.every(row => row.checked);
    pageSelect.indeterminate = rows.some(row => row.checked) && !pageSelect.checked;
    pageSelect.disabled = busy || !rows.length;
    selectAll.disabled = busy || !allIds.length || selected.size === allIds.length;
    clear.hidden = !selected.size;
    clear.disabled = busy;
    remove.disabled = busy || !selected.size;
    document.getElementById('events-selection-count').textContent = selected.size + ' selected';
  }
  selectAll.addEventListener('click', () => { allIds.forEach(id => selected.add(id)); update(); });
  clear.addEventListener('click', () => { selected.clear(); update(); });
  pageSelect.addEventListener('change', () => {
    rows.forEach(row => pageSelect.checked ? selected.add(row.value) : selected.delete(row.value)); update();
  });
  rows.forEach(row => row.addEventListener('change', () => { row.checked ? selected.add(row.value) : selected.delete(row.value); update(); }));
  remove.addEventListener('click', () => {
    error.hidden = true;
    document.getElementById('events-bulk-confirm-message').textContent = 'Permanently delete ' + selected.size + ' selected ' + bar.dataset.eventType + '-based event(s), including their schedules and scoring records? This cannot be undone.';
    dialog.showModal();
  });
  cancel.addEventListener('click', () => dialog.close());
  dialog.addEventListener('cancel', event => { if (busy) event.preventDefault(); });
  confirm.addEventListener('click', async () => {
    if (busy || !selected.size) return;
    busy = true; confirm.disabled = true; cancel.disabled = true; confirm.textContent = 'Deleting...'; update();
    try {
      const response = await fetch(bar.dataset.url, {
        method: 'POST', credentials: 'same-origin',
        headers: {'Content-Type': 'application/json', 'X-CSRFToken': bar.querySelector('[name=csrfmiddlewaretoken]').value},
        body: JSON.stringify({ids: [...selected].map(Number), event_type: bar.dataset.eventType})
      });
      let data;
      try { data = await response.json(); } catch (_) { throw new Error('Unable to delete events. Refresh the page and try again.'); }
      if (!response.ok || !data.success) throw new Error(data.message || 'Unable to delete the selected events.');
      window.location.reload();
    } catch (issue) {
      dialog.close(); error.textContent = issue.message; error.hidden = false;
      busy = false; confirm.disabled = false; cancel.disabled = false; confirm.textContent = 'Delete Events'; update();
    }
  });
  update();
})();
