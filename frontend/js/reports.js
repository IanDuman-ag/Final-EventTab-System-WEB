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
      statusEl.textContent = 'Report generated successfully! Starting download...';
      form.submit();
      
      // Reload after download starts to refresh history table and counts
      setTimeout(function () {
        window.location.reload();
      }, 1800);
    }, 1200);
  });
});
