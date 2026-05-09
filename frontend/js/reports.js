document.addEventListener('DOMContentLoaded', function () {
  const form = document.getElementById('report-generator-form');
  const generateBtn = document.getElementById('generate-report-btn');
  const statusEl = document.getElementById('generator-status');
  const formatOptions = document.querySelectorAll('.format-option');

  if (!form || !generateBtn || !statusEl) {
    return;
  }

  formatOptions.forEach((option) => {
    const input = option.querySelector('input[type="radio"]');
    if (!input) {
      return;
    }
    input.addEventListener('change', function () {
      formatOptions.forEach((item) => item.classList.remove('active'));
      option.classList.add('active');
    });
  });

  form.addEventListener('submit', function (event) {
    event.preventDefault();
    const formData = new FormData(form);
    const selectedEvent = (formData.get('event') || '').toString().trim();
    const selectedFormat = (formData.get('format') || 'pdf').toString().toUpperCase();

    if (!selectedEvent || selectedEvent === 'No events available') {
      statusEl.textContent = 'Please select a valid event before generating.';
      return;
    }

    generateBtn.disabled = true;
    generateBtn.textContent = 'Generating...';
    statusEl.textContent = 'Preparing report export...';

    setTimeout(function () {
      const fileName = `${selectedEvent.replace(/\s+/g, '_')}_report.${selectedFormat === 'EXCEL' ? 'xlsx' : 'pdf'}`;
      statusEl.textContent = `Report ready: ${fileName}. You can now download or regenerate.`;
      generateBtn.disabled = false;
      generateBtn.textContent = 'Generate Report';
    }, 1200);
  });
});
