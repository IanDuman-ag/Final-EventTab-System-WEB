document.addEventListener('DOMContentLoaded', function () {
  const form = document.getElementById('report-generator-form');
  if (!form) return;
  const status = document.getElementById('generator-status');
  const buttons = form.querySelectorAll('button[type="submit"]');
  const dialog = document.getElementById('sr-preview-dialog');
  const formats = form.querySelectorAll('.format-option');
  function syncFormats() {
    formats.forEach(option => option.classList.toggle('active', option.querySelector('input').checked));
  }
  formats.forEach(option => option.querySelector('input').addEventListener('change', syncFormats));
  document.getElementById('sr-new-report').addEventListener('click', () => {
    form.reset();
    syncFormats();
    status.textContent = '';
    form.scrollIntoView({behavior: 'smooth', block: 'center'});
    form.elements.event.focus({preventScroll: true});
  });
  document.getElementById('sr-preview-close').addEventListener('click', () => dialog.close());
  function showPreview(preview) {
    document.getElementById('sr-preview-title').textContent = preview.title || 'Report Preview';
    const rows = preview.rows || [];
    document.getElementById('sr-preview-description').textContent = rows.length > 100
      ? `${rows.length} rows. Showing the first 100.` : `${rows.length} rows.`;
    const head = document.getElementById('sr-preview-head');
    const body = document.getElementById('sr-preview-body');
    head.replaceChildren();
    body.replaceChildren();
    const heading = document.createElement('tr');
    (preview.headers || []).forEach(value => {
      const th = document.createElement('th');
      th.scope = 'col';
      th.textContent = value;
      heading.append(th);
    });
    head.append(heading);
    rows.slice(0, 100).forEach(values => {
      const row = document.createElement('tr');
      values.forEach(value => {
        const cell = document.createElement('td');
        cell.textContent = value == null ? '—' : String(value);
        row.append(cell);
      });
      body.append(row);
    });
    if (!rows.length) {
      const row = document.createElement('tr');
      const cell = document.createElement('td');
      cell.colSpan = Math.max(1, (preview.headers || []).length);
      cell.textContent = 'No data matches the selected report filters.';
      row.append(cell);
      body.append(row);
    }
    dialog.showModal();
  }
  form.addEventListener('submit', async event => {
    event.preventDefault();
    const preview = event.submitter && event.submitter.value === 'preview';
    const data = new FormData(form);
    data.set('action', preview ? 'preview' : 'generate');
    buttons.forEach(button => { button.disabled = true; });
    form.setAttribute('aria-busy', 'true');
    status.textContent = preview ? 'Preparing preview...' : 'Generating report...';
    try {
      const response = await fetch(form.getAttribute('action'), {method: 'POST', body: data, headers: {'X-Requested-With': 'XMLHttpRequest'}});
      if (!response.ok || response.headers.get('content-type')?.includes('application/json')) {
        const result = await response.json();
        if (!response.ok || !result.success) throw new Error(result.message || 'Report generation failed.');
        showPreview(result.preview);
        status.textContent = 'Preview ready.';
        return;
      }
      if (response.redirected || response.headers.get('content-type')?.includes('text/html')) {
        throw new Error('Your session has expired. Refresh this page and sign in again.');
      }
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = response.headers.get('content-disposition')?.match(/filename="([^"]+)"/)?.[1] || 'EventTab_report';
      document.body.append(link);
      link.click();
      link.remove();
      status.textContent = 'Report generated. Download started.';
      setTimeout(() => { URL.revokeObjectURL(url); window.location.reload(); }, 1500);
    } catch (error) {
      status.textContent = error.message || 'Unable to generate the report. Please try again.';
    } finally {
      buttons.forEach(button => { button.disabled = false; });
      form.removeAttribute('aria-busy');
    }
  });
});
