(function () {
  function parseData(id) {
    var el = document.getElementById(id);
    if (!el) return [];
    try { return JSON.parse(el.textContent); } catch (e) { return []; }
  }

  function drawBars(containerId, data, color) {
    var host = document.getElementById(containerId);
    if (!host) return;
    var max = Math.max.apply(null, data.map(function (d) { return d.value || 0; }).concat([1]));
    var w = host.clientWidth || 320;
    var h = 160;
    var pad = 24;
    var barW = data.length ? (w - pad * 2) / data.length : 0;
    var svg = ['<svg class="sa-chart-svg" viewBox="0 0 ' + w + ' ' + h + '" preserveAspectRatio="none">'];
    data.forEach(function (d, i) {
      var bh = ((d.value || 0) / max) * (h - 40);
      var x = pad + i * barW + 4;
      var y = h - 20 - bh;
      svg.push('<rect x="' + x + '" y="' + y + '" width="' + Math.max(barW - 8, 4) + '" height="' + Math.max(bh, 2) + '" fill="' + color + '" rx="3"/>');
      svg.push('<text x="' + (x + Math.max(barW - 8, 4) / 2) + '" y="' + (h - 6) + '" text-anchor="middle" font-size="9" fill="#7f8da0">' + String(d.label || '').slice(0, 8) + '</text>');
    });
    if (!data.length) {
      svg.push('<text x="' + (w / 2) + '" y="' + (h / 2) + '" text-anchor="middle" fill="#7f8da0" font-size="12">No data</text>');
    }
    svg.push('</svg>');
    host.innerHTML = svg.join('');
  }

  drawBars('chart-category', parseData('data-chart-category'), '#1d65c7');
  drawBars('chart-month', parseData('data-chart-month'), '#2bf28a');
  drawBars('chart-points', parseData('data-chart-points'), '#ffdf25');
  drawBars('chart-activity', parseData('data-chart-activity'), '#4da4ff');
})();
