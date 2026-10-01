(function () {
  'use strict';

  var body = document.body;
  var eventId = body.dataset.eventId;
  var csrf = body.dataset.csrf || '';
  var pendingConfirmation = null;

  function cookie(name) {
    var match = document.cookie.match(new RegExp('(^| )' + name + '=([^;]+)'));
    return match ? decodeURIComponent(match[2]) : '';
  }

  async function postJson(url, payload) {
    var response = await fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': csrf || cookie('csrftoken'),
      },
      credentials: 'same-origin',
      body: JSON.stringify(payload),
    });
    var data;
    try {
      data = await response.json();
    } catch (parseError) {
      throw new Error('The server returned an invalid response. Please sign in again or retry.');
    }
    if (!data || typeof data !== 'object' || !response.ok || data.success !== true) {
      throw new Error((data && data.message) || 'Unable to save the result.');
    }
    return data;
  }

  function payload(form) {
    return {
      score_a: form.querySelector('[name="score_a"]').value,
      score_b: form.querySelector('[name="score_b"]').value,
      winner_id: form.querySelector('[name="winner_id"]').value,
      remarks: form.querySelector('[name="remarks"]').value,
    };
  }

  function showError(form, message) {
    var node = form.querySelector('.fac-form-error');
    if (node) node.textContent = message || '';
  }

  function setBusy(button, busy) {
    button.disabled = busy;
    button.dataset.label = button.dataset.label || button.textContent;
    button.textContent = busy ? 'Saving…' : button.dataset.label;
  }

  function validate(form, confirming) {
    var data = payload(form);
    var scoreA = Number(data.score_a);
    var scoreB = Number(data.score_b);
    if (data.score_a === '' || data.score_b === '' || !Number.isFinite(scoreA) || !Number.isFinite(scoreB) || scoreA < 0 || scoreB < 0) {
      return 'Enter valid non-negative scores for both teams.';
    }
    if (confirming && scoreA === scoreB) return 'Tied scores cannot be confirmed. Review the scores.';
    if (confirming && !data.winner_id) return 'Select and verify the winner before confirming.';
    return '';
  }

  function updateSuggestion(form) {
    var scoreAInput = form.querySelector('[name="score_a"]');
    var scoreBInput = form.querySelector('[name="score_b"]');
    var winnerSelect = form.querySelector('[name="winner_id"]');
    var suggestion = form.querySelector('[data-winner-suggestion]');
    if (!suggestion) return;
    var scoreA = Number(scoreAInput.value);
    var scoreB = Number(scoreBInput.value);
    if (scoreAInput.value === '' || scoreBInput.value === '' || scoreA === scoreB) {
      suggestion.textContent = 'Verify the winner before confirmation.';
      return;
    }
    var suggestedOption = scoreA > scoreB ? winnerSelect.options[1] : winnerSelect.options[2];
    suggestion.textContent = 'Suggested winner: ' + suggestedOption.textContent + '. Select to verify.';
  }

  var menu = document.querySelector('.fac-menu-toggle');
  function closeNav() {
    body.classList.remove('fac-nav-open');
    if (menu) menu.setAttribute('aria-expanded', 'false');
  }
  if (menu) {
    menu.addEventListener('click', function () {
      var open = body.classList.toggle('fac-nav-open');
      menu.setAttribute('aria-expanded', String(open));
    });
  }
  document.querySelectorAll('[data-nav-close]').forEach(function (node) {
    node.addEventListener('click', closeNav);
  });
  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape') closeNav();
  });

  document.querySelectorAll('[data-open-match]').forEach(function (button) {
    button.addEventListener('click', function () {
      var dialog = document.querySelector('[data-match-dialog="' + button.dataset.openMatch + '"]');
      if (dialog) dialog.showModal();
    });
  });
  document.querySelectorAll('[data-close-match]').forEach(function (button) {
    button.addEventListener('click', function () {
      var dialog = button.closest('dialog');
      if (dialog) dialog.close();
    });
  });

  document.querySelectorAll('[data-match-form]').forEach(function (form) {
    ['score_a', 'score_b'].forEach(function (name) {
      var input = form.querySelector('[name="' + name + '"]');
      if (!input) return;
      input.addEventListener('input', function () {
        updateSuggestion(form);
        showError(form, '');
      });
    });
  });

  document.querySelectorAll('[data-save-draft]').forEach(function (button) {
    button.addEventListener('click', async function () {
      var form = document.querySelector('[data-match-form="' + button.dataset.saveDraft + '"]');
      var problem = validate(form, false);
      showError(form, problem);
      if (problem) return;
      setBusy(button, true);
      try {
        await postJson(
          '/faculty/events/' + eventId + '/matches/' + button.dataset.saveDraft + '/result/',
          Object.assign(payload(form), { confirm: false })
        );
        window.location.reload();
      } catch (error) {
        showError(form, error.message);
        setBusy(button, false);
      }
    });
  });

  var confirmDialog = document.getElementById('result-confirm-dialog');
  document.querySelectorAll('[data-review-result]').forEach(function (button) {
    button.addEventListener('click', function () {
      var form = document.querySelector('[data-match-form="' + button.dataset.reviewResult + '"]');
      var problem = validate(form, true);
      showError(form, problem);
      if (problem) return;
      pendingConfirmation = { id: button.dataset.reviewResult, form: form };
      var winnerSelect = form.querySelector('[name="winner_id"]');
      var option = winnerSelect.options[winnerSelect.selectedIndex];
      var score = document.createElement('strong');
      var winner = document.createElement('span');
      score.textContent = form.querySelector('[name="score_a"]').value + ' – ' + form.querySelector('[name="score_b"]').value;
      winner.textContent = 'Winner: ' + option.textContent;
      document.getElementById('result-confirm-summary').replaceChildren(score, winner);
      confirmDialog.showModal();
    });
  });
  document.querySelectorAll('[data-close-confirm]').forEach(function (button) {
    button.addEventListener('click', function () {
      confirmDialog.close();
    });
  });

  var submit = document.getElementById('confirm-result-submit');
  if (submit) {
    submit.addEventListener('click', async function () {
      if (!pendingConfirmation) return;
      setBusy(submit, true);
      try {
        await postJson(
          '/faculty/events/' + eventId + '/matches/' + pendingConfirmation.id + '/result/',
          Object.assign(payload(pendingConfirmation.form), { confirm: true })
        );
        window.location.reload();
      } catch (error) {
        confirmDialog.close();
        showError(pendingConfirmation.form, error.message);
        setBusy(submit, false);
      }
    });
  }

  if (body.dataset.openMatchId) {
    var directDialog = document.querySelector('[data-match-dialog="' + body.dataset.openMatchId + '"]');
    if (directDialog) directDialog.showModal();
  }
})();
