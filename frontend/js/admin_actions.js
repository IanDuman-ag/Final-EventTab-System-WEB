document.addEventListener('DOMContentLoaded', function () {
  const createButton = document.getElementById('open-create-admin-modal');
  const adminModal = document.getElementById('admin-modal');
  const modalForm = document.getElementById('admin-modal-form');
  const modalTitle = document.getElementById('admin-modal-title');
  const modalSubtitle = document.getElementById('admin-modal-subtitle');
  const submitButton = document.getElementById('admin-modal-submit');

  const modalAdminId = document.getElementById('modal-admin-id');
  const modalUsername = document.getElementById('modal-username');
  const modalEmail = document.getElementById('modal-email');
  const modalPassword = document.getElementById('modal-password');
  const modalRole = document.getElementById('modal-role');
  const modalDepartment = document.getElementById('modal-department');
  const modalIsActive = document.getElementById('modal-is-active');

  if (!adminModal || !modalForm || !createButton) {
    return;
  }

  createButton.addEventListener('click', function () {
    openCreateModal();
  });

  adminModal.addEventListener('click', function (event) {
    if (event.target.dataset.closeModal === 'true') {
      closeModal();
    }
  });

  modalForm.addEventListener('submit', async function (event) {
    event.preventDefault();

    const adminId = modalAdminId.value.trim();
    const payload = {
      username: modalUsername.value.trim(),
      email: modalEmail.value.trim(),
      password: modalPassword.value,
      role: modalRole.value,
      department: modalDepartment.value,
      is_active: modalIsActive.checked,
    };

    if (!payload.username || !payload.email || (!adminId && !payload.password)) {
      showBanner('Username, email, and password are required for a new account.', 'warning');
      return;
    }

    submitButton.disabled = true;
    submitButton.textContent = adminId ? 'Saving...' : 'Creating...';
    const endpoint = adminId ? `/super-admin/admins/${adminId}/update/` : '/super-admin/admins/create/';
    const result = await postJson(endpoint, payload);

    if (!result.success) {
      showBanner(result.message || 'Unable to save account.', 'error');
      submitButton.disabled = false;
      submitButton.textContent = adminId ? 'Save Account' : 'Create Account';
      return;
    }

    showBanner(result.message || 'Admin account saved successfully.', 'success');
    closeModal();
    setTimeout(function () {
      window.location.reload();
    }, 700);
  });

  function openCreateModal() {
    modalTitle.textContent = 'Admin Account';
    modalSubtitle.textContent = 'Create a user that can view the dashboard system.';
    modalAdminId.value = '';
    modalUsername.value = '';
    modalEmail.value = '';
    modalPassword.value = '';
    modalRole.value = 'Admin';
    modalDepartment.value = '';
    modalIsActive.checked = true;
    submitButton.disabled = false;
    submitButton.style.display = 'inline-flex';
    submitButton.textContent = 'CREATE ACCOUNT';
    adminModal.classList.remove('hidden');
    adminModal.setAttribute('aria-hidden', 'false');
  }

  function closeModal() {
    adminModal.classList.add('hidden');
    adminModal.setAttribute('aria-hidden', 'true');
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
  for (let i = 0; i < cookies.length; i += 1) {
    const cookie = cookies[i].trim();
    if (cookie.startsWith(name)) {
      return decodeURIComponent(cookie.slice(name.length));
    }
  }
  return '';
}

function showBanner(message, type) {
  const banner = document.createElement('div');
  banner.textContent = message;
  banner.style.position = 'fixed';
  banner.style.top = '20px';
  banner.style.right = '20px';
  banner.style.padding = '12px 16px';
  banner.style.borderRadius = '8px';
  banner.style.fontSize = '13px';
  banner.style.fontWeight = '700';
  banner.style.zIndex = '9999';
  banner.style.maxWidth = '420px';

  if (type === 'success') {
    banner.style.background = '#2af385';
    banner.style.color = '#0a2340';
  } else if (type === 'warning') {
    banner.style.background = '#ffdf23';
    banner.style.color = '#0a2340';
  } else {
    banner.style.background = '#f04955';
    banner.style.color = '#ffffff';
  }

  document.body.appendChild(banner);
  setTimeout(function () {
    banner.remove();
  }, 3500);
}
