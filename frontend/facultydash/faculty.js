(function () {
  'use strict';

  var body = document.body;
  var eventId = body.dataset.eventId;
  var csrf = body.dataset.csrf || '';

  function getCookie(name) {
    var match = document.cookie.match(new RegExp('(^| )' + name + '=([^;]+)'));
    return match ? decodeURIComponent(match[2]) : '';
  }

  async function postJson(url, payload) {
    var token = csrf || getCookie('csrftoken');
    var res = await fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': token,
      },
      credentials: 'same-origin',
      body: JSON.stringify(payload || {}),
    });
    var data = await res.json().catch(function () { return {}; });
    if (!res.ok || data.success === false) {
      throw new Error(data.message || 'Request failed.');
    }
    return data;
  }

  function collectMatchPayload(form) {
    return {
      score_a: (form.querySelector('[name="score_a"]') || {}).value,
      score_b: (form.querySelector('[name="score_b"]') || {}).value,
      winner_id: (form.querySelector('[name="winner_id"]') || {}).value,
      remarks: (form.querySelector('[name="remarks"]') || {}).value,
    };
  }

  document.querySelectorAll('[data-save-draft]').forEach(function (btn) {
    btn.addEventListener('click', async function () {
      var matchId = btn.dataset.saveDraft;
      var form = document.querySelector('[data-match-form="' + matchId + '"]');
      if (!form || !eventId) return;
      try {
        var data = await postJson(
          '/faculty/events/' + eventId + '/matches/' + matchId + '/result/',
          Object.assign(collectMatchPayload(form), { confirm: false })
        );
        alert(data.message || 'Draft saved.');
      } catch (err) {
        alert(err.message);
      }
    });
  });

  document.querySelectorAll('[data-confirm-result]').forEach(function (btn) {
    btn.addEventListener('click', async function () {
      if (!confirm('Confirm this match result? The bracket will update automatically.')) return;
      var matchId = btn.dataset.confirmResult;
      var form = document.querySelector('[data-match-form="' + matchId + '"]');
      if (!form || !eventId) return;
      try {
        var data = await postJson(
          '/faculty/events/' + eventId + '/matches/' + matchId + '/result/',
          Object.assign(collectMatchPayload(form), { confirm: true })
        );
        alert(data.message || 'Result confirmed.');
        window.location.reload();
      } catch (err) {
        alert(err.message);
      }
    });
  });

  async function resultsAction(action) {
    if (!eventId) return;
    try {
      var data = await postJson('/faculty/events/' + eventId + '/results/action/', { action: action });
      alert(data.message || 'Done.');
      window.location.reload();
    } catch (err) {
      alert(err.message);
    }
  }

  var btnReturn = document.getElementById('btn-return');
  if (btnReturn) {
    btnReturn.addEventListener('click', function () {
      if (!confirm('Return results to judges for correction?')) return;
      resultsAction('return');
    });
  }

  var btnApprove = document.getElementById('btn-approve');
  if (btnApprove) {
    btnApprove.addEventListener('click', function () {
      if (!confirm('Approve computed results and mark ready for publication?')) return;
      resultsAction('approve');
    });
  }

  var dialog = document.getElementById('publish-dialog');
  var btnPublish = document.getElementById('btn-publish');
  if (btnPublish && dialog) {
    btnPublish.addEventListener('click', function () { dialog.showModal(); });
  }
  document.querySelectorAll('[data-close-publish]').forEach(function (btn) {
    btn.addEventListener('click', function () { if (dialog) dialog.close(); });
  });
  var btnConfirmPublish = document.getElementById('btn-confirm-publish');
  if (btnConfirmPublish) {
    btnConfirmPublish.addEventListener('click', function () {
      resultsAction('publish');
    });
  }

  var btnStage = document.getElementById('btn-confirm-stage');
  if (btnStage && eventId) {
    btnStage.addEventListener('click', async function () {
      var ids = Array.prototype.slice.call(document.querySelectorAll('[data-qualifier-id]:checked')).map(function (el) {
        return Number(el.value);
      });
      if (!ids.length) {
        alert('Select at least one qualifier before confirming.');
        return;
      }
      var activeStage = 0;
      var openStage = document.querySelector('.fac-stage[data-stage-status="open"], .fac-stage[data-stage-status="waiting_confirmation"], .fac-stage[data-stage-status="ongoing"]');
      if (openStage) activeStage = Number(openStage.getAttribute('data-stage-index') || 0);
      try {
        var data = await postJson('/faculty/events/' + eventId + '/stages/confirm/', {
          stage_index: activeStage,
          qualifier_ids: ids,
        });
        alert(data.message || 'Qualifiers confirmed. The next stage is now open.');
        window.location.reload();
      } catch (err) {
        alert(err.message);
      }
    });
  }
})();
