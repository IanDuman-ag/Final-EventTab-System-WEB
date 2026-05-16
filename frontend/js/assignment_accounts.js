document.addEventListener('DOMContentLoaded', function () {
  const openButton = document.getElementById('open-assignment-modal');
  const modal = document.getElementById('assignment-modal');
  const form = document.getElementById('assignment-account-form');
  const submitButton = document.getElementById('assignment-submit');
  const modalTitle = document.getElementById('assignment-modal-title');
  const modalSubtitle = document.getElementById('assignment-modal-subtitle');

  const accountId = document.getElementById('assignment-account-id');
  const username = document.getElementById('assignment-username');
  const email = document.getElementById('assignment-email');
  const password = document.getElementById('assignment-password');
  const role = document.getElementById('assignment-role');
  const statusSelect = document.getElementById('assignment-status');

  if (!openButton || !modal || !form) {
    return;
  }

  openButton.addEventListener('click', function () {
    openCreateModal();
  });

  document.querySelectorAll('.edit-account').forEach(function (button) {
    button.addEventListener('click', function () {
      const row = button.closest('tr');
      if (!row) {
        return;
      }
      openEditModal(row, button.dataset.updateUrl);
    });
  });

  document.querySelectorAll('.delete-account').forEach(function (button) {
    button.addEventListener('click', async function () {
      const row = button.closest('tr');
      const name = row ? (row.dataset.username || row.dataset.fullName || 'this account') : 'this account';
      const confirmed = window.confirm('Delete ' + name + '? This action cannot be undone.');
      if (!confirmed) {
        return;
      }

      button.disabled = true;
      const result = await postJson(button.dataset.deleteUrl, {});
      if (!result.success) {
        button.disabled = false;
        showToast(result.message || 'Unable to delete account.', 'error');
        return;
      }

      showToast(result.message || 'Account deleted.', 'success');
      setTimeout(function () {
        window.location.reload();
      }, 700);
    });
  });

  modal.addEventListener('click', function (event) {
    if (event.target.dataset.closeModal === 'true') {
      closeModal();
    }
  });

  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape' && !modal.classList.contains('hidden')) {
      closeModal();
    }
  });

  form.addEventListener('submit', async function (event) {
    event.preventDefault();

    const editing = Boolean(accountId.value);
    const isActive = statusSelect.value === 'active';
    const payload = {
      full_name: username.value.trim(),
      username: username.value.trim(),
      email: email.value.trim(),
      password: password.value,
      role: role.value,
      is_active: isActive,
    };

    if (!payload.username || !payload.email || (!editing && !payload.password)) {
      showToast('Username, email, and password are required for new accounts.', 'warning');
      return;
    }

    submitButton.disabled = true;
    submitButton.textContent = editing ? 'Saving...' : 'Creating...';

    const result = await postJson(modal.dataset.endpoint, payload);
    if (!result.success) {
      submitButton.disabled = false;
      submitButton.textContent = editing ? 'Save Account' : 'Create Account';
      showToast(result.message || 'Unable to save account.', 'error');
      return;
    }

    showToast(result.message || 'Account saved.', 'success');
    closeModal();
    setTimeout(function () {
      window.location.reload();
    }, 700);
  });

  function openCreateModal() {
    modal.dataset.endpoint = openButton.dataset.createUrl;
    accountId.value = '';
    username.value = '';
    email.value = '';
    password.value = '';
    role.value = 'Tabulator';
    statusSelect.value = 'active';
    modalTitle.textContent = 'Create Account';
    modalSubtitle.textContent = 'Create a tabulator or judge account for event scoring access.';
    submitButton.disabled = false;
    submitButton.textContent = 'Create Account';
    password.required = true;
    openModal();
  }

  function openEditModal(row, endpoint) {
    modal.dataset.endpoint = endpoint;
    accountId.value = row.dataset.accountId || '';
    username.value = row.dataset.username || '';
    email.value = row.dataset.email || '';
    password.value = '';
    role.value = row.dataset.role || 'Tabulator';
    statusSelect.value = row.dataset.status === 'active' ? 'active' : 'deactive';
    modalTitle.textContent = 'Edit Account';
    modalSubtitle.textContent = 'Update the role, login details, and status for this account.';
    submitButton.disabled = false;
    submitButton.textContent = 'Save Account';
    password.required = false;
    openModal();
  }

  function openModal() {
    modal.classList.remove('hidden');
    modal.setAttribute('aria-hidden', 'false');
    username.focus();
  }

  function closeModal() {
    modal.classList.add('hidden');
    modal.setAttribute('aria-hidden', 'true');
  }
});

async function postJson(url, payload) {
  try {
    const response = await fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': getCsrfToken(),
      },
      body: JSON.stringify(payload || {}),
    });

    const text = await response.text();
    let data;
    try {
      data = JSON.parse(text);
    } catch (parseError) {
      return { success: false, message: `Request failed (${response.status}).` };
    }

    if (!response.ok) {
      return { success: false, message: data.message || `Request failed (${response.status}).` };
    }

    return data;
  } catch (error) {
    return { success: false, message: 'Network request failed.' };
  }
}

function getCsrfToken() {
  const csrfInput = document.querySelector('input[name="csrfmiddlewaretoken"]');
  if (csrfInput && csrfInput.value) {
    return csrfInput.value;
  }

  const name = 'csrftoken=';
  const cookies = document.cookie.split(';');
  for (let index = 0; index < cookies.length; index += 1) {
    const cookie = cookies[index].trim();
    if (cookie.startsWith(name)) {
      return decodeURIComponent(cookie.slice(name.length));
    }
  }
  return '';
}

function showToast(message, type) {
  const toast = document.createElement('div');
  toast.className = `toast-message ${type || 'success'}`;
  toast.textContent = message;
  document.body.appendChild(toast);
  // Trigger reflow so the animation plays
  toast.offsetHeight;
  toast.classList.add('visible');
  setTimeout(function () {
    toast.classList.remove('visible');
    setTimeout(function () { toast.remove(); }, 350);
  }, 3500);
}
