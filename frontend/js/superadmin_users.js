(function () {
  'use strict';
  var urls = window.SA_USER_URLS || {};
  var modal = document.getElementById('sa-user-modal');
  var form = document.getElementById('sa-user-form');
  var resetModal = document.getElementById('sa-reset-modal');
  var resetForm = document.getElementById('sa-reset-form');
  var detailModal = document.getElementById('sa-detail-modal');
  var resetId = null;
  var detailUser = null;
  var previousFocus = null;
  function field(id) { return document.getElementById(id); }
  function show(element) {
    previousFocus = document.activeElement;
    element.classList.add('is-open');
    var target = element.querySelector('input:not([type=hidden]), button');
    if (target) target.focus();
  }
  function close(element) {
    element.classList.remove('is-open');
    element.querySelectorAll('input[type=password], .su-password-field input').forEach(function (input) { input.value = ''; input.type = 'password'; });
    element.querySelectorAll('.su-show-password').forEach(function (button) { button.textContent = 'Show'; button.setAttribute('aria-label', 'Show password'); });
    if (previousFocus && previousFocus.isConnected) previousFocus.focus();
  }
  function message(id, text) { field(id).textContent = text || ''; }
  async function post(url, body) {
    var response = await fetch(url, {method: 'POST', credentials: 'same-origin', cache: 'no-store',
      headers: {'Content-Type': 'application/json', 'X-CSRFToken': urls.csrf}, body: JSON.stringify(body || {})});
    if (response.redirected) throw new Error('Your session has expired. Refresh and sign in again.');
    var result;
    try { result = await response.json(); } catch (error) { throw new Error('The request could not be completed. Please try again.'); }
    if (!response.ok || !result.success) throw new Error(result.message || 'Request failed.');
    return result;
  }
  function openUser(data) {
    form.reset(); message('sa-user-message', '');
    field('sa-user-id').value = data ? data.id : '';
    field('sa-modal-title').textContent = data ? 'Edit User' : 'Create User';
    field('sa-username').value = data ? data.username : '';
    field('sa-email').value = data ? data.email : '';
    field('sa-role').value = data && data.role !== 'Super Admin' ? data.role : 'Admin';
    field('sa-active').checked = !data || data.active !== '0';
    field('sa-password').required = !data;
    field('sa-password-confirm').required = !data;
    field('sa-password').placeholder = data ? 'Leave blank to keep current password' : 'Enter a password';
    show(modal);
  }
  function openReset(id, username) {
    resetId = id; resetForm.reset(); message('sa-reset-message', '');
    field('sa-reset-account').textContent = 'Set a new sign-in password for ' + username + '.';
    resetForm.querySelector('button[type=submit]').disabled = false;
    show(resetModal);
  }
  field('sa-open-create').addEventListener('click', function () { openUser(null); });
  field('sa-modal-cancel').addEventListener('click', function () { close(modal); });
  document.querySelectorAll('[data-close]').forEach(function (button) { button.addEventListener('click', function () { close(field(button.dataset.close)); }); });
  document.querySelectorAll('.sa-modal-backdrop').forEach(function (backdrop) { backdrop.addEventListener('click', function (event) { if (event.target === backdrop) close(backdrop); }); });
  document.querySelectorAll('.su-show-password').forEach(function (button) {
    button.addEventListener('click', function () {
      var input = field(button.dataset.target); var visible = input.type === 'password';
      input.type = visible ? 'text' : 'password'; button.textContent = visible ? 'Hide' : 'Show';
      button.setAttribute('aria-label', visible ? 'Hide password' : 'Show password');
    });
  });
  document.addEventListener('keydown', function (event) {
    var open = document.querySelector('.sa-modal-backdrop.is-open');
    if (!open) return;
    if (event.key === 'Escape') close(open);
    if (event.key === 'Tab') {
      var controls = Array.from(open.querySelectorAll('input:not([type=hidden]), select, button')).filter(function (el) { return !el.disabled; });
      var first = controls[0], last = controls[controls.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    }
  });
  form.addEventListener('submit', async function (event) {
    event.preventDefault(); var id = field('sa-user-id').value;
    var password = field('sa-password').value;
    if (password !== field('sa-password-confirm').value) { message('sa-user-message', 'Passwords do not match.'); return; }
    var button = form.querySelector('button[type=submit]'); button.disabled = true; message('sa-user-message', 'Saving account…');
    try {
      await post(id ? urls.update.replace('__ID__', id) : urls.create, {
        username: field('sa-username').value, email: field('sa-email').value, role: field('sa-role').value,
        password: password, is_active: field('sa-active').checked
      });
      close(modal); location.reload();
    } catch (error) { message('sa-user-message', error.message); }
    finally { button.disabled = false; }
  });
  resetForm.addEventListener('submit', async function (event) {
    event.preventDefault(); var password = field('sa-reset-password').value;
    if (password !== field('sa-reset-confirm').value) { message('sa-reset-message', 'Passwords do not match.'); return; }
    var button = resetForm.querySelector('button[type=submit]'); button.disabled = true; message('sa-reset-message', 'Resetting password…');
    try {
      await post(urls.reset.replace('__ID__', resetId), {password: password, password_confirm: field('sa-reset-confirm').value});
      resetForm.reset(); message('sa-reset-message', 'Password reset successfully. The user can sign in with the new password.');
      resetForm.querySelectorAll('.su-show-password').forEach(function (toggle) { toggle.textContent = 'Show'; toggle.setAttribute('aria-label', 'Show password'); field(toggle.dataset.target).type = 'password'; });
    } catch (error) { message('sa-reset-message', error.message); }
    finally { button.disabled = false; }
  });
  document.querySelectorAll('.sa-edit').forEach(function (button) { button.addEventListener('click', function () { openUser(button.dataset); }); });
  document.querySelectorAll('.sa-reset').forEach(function (button) { button.addEventListener('click', function () { openReset(button.dataset.id, button.closest('tr').querySelector('td small').textContent); }); });
  field('sa-detail-reset').addEventListener('click', function () { close(detailModal); openReset(detailUser.id, detailUser.username); });
  document.querySelectorAll('.sa-view').forEach(function (button) {
    button.addEventListener('click', async function () {
      button.disabled = true;
      try {
        var response = await fetch(urls.detail.replace('__ID__', button.dataset.id), {cache: 'no-store', credentials: 'same-origin'});
        if (response.redirected) throw new Error('Your session has expired. Refresh and sign in again.');
        var data = await response.json(); if (!response.ok || !data.success) throw new Error(data.message || 'User not found.');
        detailUser = data.user; var list = field('sa-account-details'); list.replaceChildren();
        [['Name', 'name'], ['Username', 'username'], ['Email', 'email'], ['Role', 'role'], ['Status', 'status'], ['Last login', 'last_login']].forEach(function (item) {
          var wrapper = document.createElement('div'), label = document.createElement('dt'), value = document.createElement('dd');
          label.textContent = item[0]; value.textContent = detailUser[item[1]] || '—'; wrapper.append(label, value); list.appendChild(wrapper);
        });
        show(detailModal);
      } catch (error) { alert(error.message || 'Could not load this account. Please try again.'); }
      finally { button.disabled = false; }
    });
  });
  document.querySelectorAll('.sa-toggle, .sa-delete').forEach(function (button) {
    button.addEventListener('click', async function () {
      var deleting = button.classList.contains('sa-delete');
      if (deleting && !confirm('Delete this user permanently?')) return;
      button.disabled = true;
      try { await post((deleting ? urls.delete : urls.setActive).replace('__ID__', button.dataset.id), deleting ? {} : {active: button.dataset.active === '1'}); location.reload(); }
      catch (error) { alert(error.message); button.disabled = false; }
    });
  });
})();
