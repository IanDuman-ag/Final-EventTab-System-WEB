(function () {
  'use strict';
  var NS = 'http://www.w3.org/2000/svg';
  var colors = ['#0878ff', '#ffd01a', '#20dc76', '#5bbaff', '#a9b8ca'];
  function node(tag, attrs, parent, text) {
    var element = document.createElementNS(NS, tag);
    Object.keys(attrs || {}).forEach(function (key) { element.setAttribute(key, attrs[key]); });
    if (text !== undefined) element.textContent = text;
    if (parent) parent.appendChild(element);
    return element;
  }
  function read(id) {
    var source = document.getElementById(id);
    try {
      var data = JSON.parse(source ? source.textContent : '[]');
      return Array.isArray(data) ? data.map(function (row) {
        var value = Number(row.value);
        return {label: String(row.label || ''), value: Number.isFinite(value) ? Math.max(0, value) : 0};
      }) : [];
    } catch (error) { return []; }
  }
  function text(svg, x, y, value, attrs) {
    return node('text', Object.assign({x: x, y: y, fill: '#627c9f', 'font-size': 9}, attrs), svg, value);
  }
  function title(element, label) { node('title', {}, element, label); }
  function axes(svg, max, width, height, left) {
    for (var i = 0; i <= 4; i++) {
      var y = height - i * (height - 14) / 4;
      node('line', {x1: left, x2: width - 8, y1: y, y2: y, stroke: '#e9f0f9', 'stroke-width': 1}, svg);
      text(svg, left - 6, y + 3, Math.round(max * i / 4), {'text-anchor': 'end', 'font-size': 8});
    }
  }
  function bars(host, data, horizontal) {
    var svg = node('svg', {viewBox: '0 0 330 135', class: 'sa-chart-svg', preserveAspectRatio: 'none'}, host);
    var max = Math.max.apply(null, data.map(function (row) { return row.value; }).concat([1]));
    max = Math.ceil(max / 4) * 4;
    if (horizontal) {
      var rowHeight = 110 / data.length;
      data.forEach(function (row, index) {
        var y = 7 + index * rowHeight;
        text(svg, 2, y + rowHeight * .6, row.label.length > 17 ? row.label.slice(0, 16) + '…' : row.label, {'font-size': 8});
        var bar = node('rect', {x: 105, y: y, width: row.value / max * 190, height: Math.max(5, rowHeight - 5), fill: colors[index % colors.length], rx: 1}, svg);
        title(bar, row.label + ': ' + row.value);
        text(svg, 110 + row.value / max * 190, y + rowHeight * .6, row.value, {fill: '#183963', 'font-weight': 700, 'font-size': 8});
      });
      node('line', {x1: 105, x2: 316, y1: 121, y2: 121, stroke: '#dce7f5'}, svg);
      [0, 1, 2, 3].forEach(function (i) { text(svg, 105 + i * 63, 132, Math.round(max * i / 3), {'font-size': 8}); });
      return;
    }
    axes(svg, max, 330, 112, 27);
    var slot = 294 / data.length;
    data.forEach(function (row, index) {
      var height = row.value / max * 96;
      var x = 31 + index * slot;
      var width = Math.max(4, slot - 14);
      var palette = host.id === 'chart-publication' ? ['#ffd01a', '#378bff', '#21d970'] : colors;
      var bar = node('rect', {x: x, y: 112 - height, width: width, height: height, fill: palette[index % palette.length], rx: 1}, svg);
      title(bar, row.label + ': ' + row.value);
      text(svg, x + width / 2, 107 - height, row.value, {'text-anchor': 'middle', fill: '#18345c', 'font-size': 9, 'font-weight': 700});
      text(svg, x + width / 2, 129, row.label.length > 12 ? row.label.slice(0, 11) + '…' : row.label, {'text-anchor': 'middle', 'font-size': 8});
    });
  }
  function line(host, data) {
    var svg = node('svg', {viewBox: '0 0 330 135', class: 'sa-chart-svg', preserveAspectRatio: 'none'}, host);
    var max = Math.ceil(Math.max.apply(null, data.map(function (row) { return row.value; }).concat([1])) / 4) * 4;
    axes(svg, max, 330, 112, 26);
    var step = data.length > 1 ? 290 / (data.length - 1) : 0;
    var points = data.map(function (row, index) { return [28 + index * step, 112 - row.value / max * 94]; });
    var path = points.map(function (point, index) { return (index ? 'L' : 'M') + point[0] + ',' + point[1]; }).join(' ');
    node('path', {d: path + ' L' + points[points.length - 1][0] + ',112 L28,112 Z', fill: '#e7f1ff'}, svg);
    node('path', {d: path, fill: 'none', stroke: '#0878ff', 'stroke-width': 2}, svg);
    data.forEach(function (row, index) {
      var point = points[index];
      var circle = node('circle', {cx: point[0], cy: point[1], r: 3, fill: '#0878ff'}, svg);
      title(circle, row.label + ': ' + row.value);
      text(svg, point[0], point[1] - 7, row.value, {'text-anchor': 'middle', fill: '#19355e', 'font-weight': 700, 'font-size': 8});
      var label = host.id === 'chart-activity' ? row.label.slice(5) : row.label.slice(0, 3);
      if (data.length < 9 || index % Math.ceil(data.length / 8) === 0) text(svg, point[0], 130, label, {'text-anchor': 'middle', 'font-size': 8});
    });
  }
  function donut(host, data) {
    host.classList.add('sa-donut-chart');
    var total = data.reduce(function (sum, row) { return sum + row.value; }, 0);
    var svg = node('svg', {viewBox: '0 0 120 120', class: 'sa-donut-svg'}, host);
    node('circle', {cx: 60, cy: 60, r: 43, fill: 'none', stroke: '#edf3fa', 'stroke-width': 18}, svg);
    var offset = 0;
    var palette = host.id === 'chart-status' ? ['#0878ff', '#20dc76', '#ffd01a', '#a9b8ca'] : colors;
    var legend = document.createElement('ul');
    legend.className = 'sa-chart-legend';
    data.forEach(function (row, index) {
      if (!row.value) return;
      var length = row.value / total * 270.18;
      var arc = node('circle', {cx: 60, cy: 60, r: 43, fill: 'none', stroke: palette[index % palette.length], 'stroke-width': 18, 'stroke-dasharray': length + ' 270.18', 'stroke-dashoffset': -offset, transform: 'rotate(-90 60 60)'}, svg);
      title(arc, row.label + ': ' + row.value);
      offset += length;
      var item = document.createElement('li');
      var dot = document.createElement('i'); dot.style.backgroundColor = palette[index % palette.length]; item.appendChild(dot);
      var label = document.createElement('span'); label.textContent = row.label; item.appendChild(label);
      var count = document.createElement('strong'); count.textContent = row.value; item.appendChild(count);
      var share = document.createElement('small'); share.textContent = Math.round(row.value / total * 100) + '%'; item.appendChild(share);
      legend.appendChild(item);
    });
    text(svg, 60, 60, total, {'text-anchor': 'middle', fill: '#16315c', 'font-size': 20, 'font-weight': 800});
    text(svg, 60, 76, host.dataset.unit || '', {'text-anchor': 'middle', 'font-size': 10});
    host.appendChild(legend);
  }
  document.querySelectorAll('[data-sa-chart]').forEach(function (host) {
    var data = read(host.dataset.saChart);
    host.replaceChildren();
    if (!data.length || data.every(function (row) { return !row.value; })) {
      var empty = document.createElement('p'); empty.className = 'sa-chart-empty'; empty.textContent = 'No data available yet.'; host.appendChild(empty); return;
    }
    if (host.dataset.kind === 'donut') donut(host, data);
    else if (host.dataset.kind === 'line') line(host, data);
    else bars(host, data, host.dataset.kind === 'horizontal');
  });
  var toggle = document.querySelector('.sa-menu-toggle');
  if (toggle) {
    toggle.addEventListener('click', function () {
      var open = toggle.getAttribute('aria-expanded') !== 'true';
      toggle.setAttribute('aria-expanded', String(open));
      document.body.classList.toggle('sa-navigation-open', open);
    });
    document.addEventListener('keydown', function (event) {
      if (event.key === 'Escape') { toggle.setAttribute('aria-expanded', 'false'); document.body.classList.remove('sa-navigation-open'); }
    });
  }
})();