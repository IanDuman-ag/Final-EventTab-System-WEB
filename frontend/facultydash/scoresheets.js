(() => {
  const csrf = window.FAC_SS_CSRF || '';
  const dialog = document.getElementById('ss-preview-dialog');
  const frame = document.getElementById('ss-preview-frame');
  const banner = document.getElementById('ss-template-banner');
  const downloadBtn = document.getElementById('ss-download-btn');

  function urlsFor(eventId, itemKey) {
    const base = `/faculty/scoresheets/${eventId}/${encodeURIComponent(itemKey)}`;
    return {
      pdf: `${base}/pdf/`,
      download: `${base}/pdf/?download=1`,
      generate: `${base}/generate/`,
    };
  }

  function openPreview(eventId, itemKey, templateName) {
    const urls = urlsFor(eventId, itemKey);
    banner.textContent = `Using template: ${templateName || 'Default layout'} (read-only)`;
    frame.src = urls.pdf;
    downloadBtn.href = urls.download;
    dialog?.showModal();
  }

  async function generate(eventId, itemKey, templateName) {
    const urls = urlsFor(eventId, itemKey);
    const res = await fetch(urls.generate, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': csrf,
      },
      body: '{}',
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || data.success === false) {
      window.alert(data.message || 'Failed to generate scoresheet.');
      return;
    }
    openPreview(eventId, itemKey, data.template_name || templateName);
  }

  document.getElementById('scoresheet-rows')?.addEventListener('click', (e) => {
    const btn = e.target.closest('[data-action]');
    if (!btn) return;
    const row = btn.closest('tr');
    if (!row) return;
    const eventId = row.dataset.eventId;
    const itemKey = row.dataset.itemKey;
    const templateName = row.dataset.template;
    const action = btn.dataset.action;
    if (action === 'preview' || action === 'print') {
      e.preventDefault();
      openPreview(eventId, itemKey, templateName);
      if (action === 'print') {
        setTimeout(() => {
          try { frame.contentWindow?.print(); } catch (_) {}
        }, 600);
      }
      return;
    }
    if (action === 'generate') {
      e.preventDefault();
      generate(eventId, itemKey, templateName);
    }
  });

  document.getElementById('ss-close-preview')?.addEventListener('click', () => dialog?.close());
  document.getElementById('ss-done-btn')?.addEventListener('click', () => dialog?.close());
  document.getElementById('ss-print-btn')?.addEventListener('click', () => {
    try { frame.contentWindow?.print(); } catch (_) {
      window.alert('Preview is still loading. Try again in a moment.');
    }
  });
})();
