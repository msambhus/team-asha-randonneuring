/**
 * BrevetHub volunteer signup — slot picker modal (SFR-style role list).
 */
(function () {
  var modal = document.getElementById('volunteer-modal');
  if (!modal) return;

  var eventId = null;
  var slots = [];
  var signups = [];
  var selectedSlotId = null;

  var body = modal.querySelector('[data-vol-body]');
  var title = modal.querySelector('[data-vol-title]');

  function esc(v) {
    return String(v == null ? '' : v)
      .replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;');
  }

  function closeModal() {
    modal.hidden = true;
    eventId = null;
    slots = [];
    signups = [];
    selectedSlotId = null;
  }

  function openModal(id) {
    eventId = id;
    modal.hidden = false;
    title.textContent = 'Volunteer signup';
    body.innerHTML = '<p class="text-text-light">Loading volunteer roles…</p>';
    loadData();
  }

  function loadData() {
    return Promise.all([
      fetch('/calendar/' + eventId + '/volunteer/slots').then(function (r) { return r.json(); }),
      fetch('/calendar/' + eventId + '/volunteer/status').then(function (r) {
        if (r.status === 401) return { signups: [] };
        return r.json();
      }),
    ]).then(function (results) {
      var slotData = results[0];
      var statusData = results[1];
      if (!slotData.event || !slotData.event.volunteer_open) {
        body.innerHTML = '<p class="text-red-700">Volunteer signup is not open for this event.</p>';
        return;
      }
      slots = slotData.slots || [];
      signups = statusData.signups || [];
      renderPicker(slotData.event);
    }).catch(function () {
      body.innerHTML = '<p class="text-red-700">Could not load volunteer roles. Try again.</p>';
    });
  }

  function renderPicker(event) {
    if (signups.length) {
      renderExisting(event);
      return;
    }
    if (!slots.length) {
      body.innerHTML = '<p class="text-text-light">No volunteer roles are set up for this event yet.</p>';
      return;
    }

    var html = '';
    html += '<div class="reg-event-banner" style="margin-bottom:16px;">';
    html += '<div><div class="reg-event-type">Volunteer</div>';
    html += '<div class="reg-event-name">' + esc(event.name) + '</div>';
    html += '<div class="reg-event-meta">' + esc(event.date) + '</div></div></div>';
    html += '<p class="text-sm text-text-light mb-3">Choose one volunteer role. Signing up for a second role requires organizer approval.</p>';
    html += '<div class="vol-slot-list" style="border:1px solid var(--border);border-radius:10px;overflow:hidden;">';

    slots.forEach(function (slot) {
      var availClass = slot.full ? 'vol-full' : 'vol-available';
      var availText = slot.full ? 'Full' : (slot.available + ' available');
      var disabled = slot.full ? ' disabled' : '';
      html += '<label class="vol-slot-option" style="display:flex;align-items:center;gap:12px;padding:12px 14px;border-bottom:1px solid var(--border);cursor:pointer;' + (slot.full ? 'opacity:.55;' : '') + '">';
      html += '<input type="radio" name="vol_slot" value="' + slot.id + '"' + disabled + ' style="flex-shrink:0;">';
      html += '<span style="flex:1;font-weight:600;color:var(--primary);">' + esc(slot.role_name) + '</span>';
      html += '<span class="' + availClass + '" style="font-weight:700;font-style:italic;font-size:.85rem;color:' + (slot.full ? '#b91c1c' : '#15803d') + ';">' + availText + '</span>';
      html += '</label>';
    });
    html += '</div>';
    html += '<div style="margin-top:16px;display:flex;gap:10px;justify-content:flex-end;">';
    html += '<button type="button" class="signup-btn secondary" data-vol-close>Cancel</button>';
    html += '<button type="button" class="signup-btn register-primary" data-vol-submit>Sign up</button>';
    html += '</div>';

    body.innerHTML = html;
  }

  function renderExisting(event) {
    var html = '';
    html += '<div class="reg-event-banner" style="margin-bottom:16px;">';
    html += '<div><div class="reg-event-type">Volunteer</div>';
    html += '<div class="reg-event-name">' + esc(event.name) + '</div></div></div>';
    html += '<p class="text-sm font-semibold text-primary mb-2">Your volunteer signups</p>';
    html += '<ul class="vol-signup-list">';
    signups.forEach(function (s) {
      var statusLabel = s.status === 'confirmed' ? 'Confirmed' : (s.status === 'exception' ? 'Pending approval' : s.status);
      var statusClass = s.status === 'exception' ? 'is-pending' : 'is-confirmed';
      var withdrawLabel = 'Withdraw from ' + s.role_name;
      html += '<li class="vol-signup-row">';
      html += '<span class="vol-signup-role">' + esc(s.role_name) + '</span>';
      html += '<span class="vol-signup-status ' + statusClass + '">' + esc(statusLabel) + '</span>';
      html += '<button type="button" class="vol-withdraw-btn" data-vol-withdraw="' + s.id + '"';
      html += ' title="' + esc(withdrawLabel) + '" aria-label="' + esc(withdrawLabel) + '">✕</button>';
      html += '</li>';
    });
    html += '</ul>';

    var signedSlotIds = {};
    signups.forEach(function (s) { signedSlotIds[s.slot_id] = true; });
    var openSlots = slots.filter(function (s) { return !signedSlotIds[s.id] && !s.full; });
    if (openSlots.length) {
      html += '<p class="text-xs text-text-light mb-2">Additional roles require organizer approval.</p>';
      html += '<div class="vol-slot-list" style="border:1px solid var(--border);border-radius:10px;overflow:hidden;margin-bottom:12px;">';
      openSlots.forEach(function (slot) {
        html += '<label class="vol-slot-option" style="display:flex;align-items:center;gap:12px;padding:12px 14px;border-bottom:1px solid var(--border);cursor:pointer;">';
        html += '<input type="radio" name="vol_slot" value="' + slot.id + '" style="flex-shrink:0;">';
        html += '<span style="flex:1;font-weight:600;color:var(--primary);">' + esc(slot.role_name) + '</span>';
        html += '<span style="font-weight:700;font-style:italic;font-size:.85rem;color:#15803d;">' + slot.available + ' available</span>';
        html += '</label>';
      });
      html += '</div>';
      html += '<button type="button" class="signup-btn register-primary" data-vol-submit>Request additional role</button>';
    }

    html += '<div style="margin-top:16px;"><button type="button" class="signup-btn secondary" data-vol-close>Close</button></div>';
    body.innerHTML = html;
  }

  function renderConfirmation(result) {
    body.innerHTML = ''
      + '<div style="text-align:center;padding:20px 0;">'
      + '<div style="font-size:2.5rem;margin-bottom:8px;">' + (result.needs_approval ? '⏳' : '✓') + '</div>'
      + '<h3 style="color:var(--primary);margin:0 0 8px;">' + esc(result.message) + '</h3>'
      + '<p class="text-text-light text-sm">' + esc(result.rider_name) + ' · ' + esc(result.slot.role_name) + '</p>'
      + '<button type="button" class="signup-btn register-primary" style="margin-top:20px;" data-vol-done>Done</button>'
      + '</div>';
    updateVolunteerStrip(eventId, result);
  }

  function getEventCard(eid) {
    return document.querySelector('.event-card[data-event-id="' + eid + '"]');
  }

  function getVolunteerChip(eid) {
    var card = getEventCard(eid);
    return card ? card.querySelector('[data-volunteer-chip]') : null;
  }

  function slotTotals(slots) {
    var capacity = 0;
    var confirmed = 0;
    var open = 0;
    slots.forEach(function (slot) {
      capacity += slot.capacity || 1;
      confirmed += slot.confirmed_count || 0;
      open += slot.available || 0;
    });
    return { capacity: capacity, confirmed: confirmed, open: open };
  }

  function renderVolunteerChip(chip, eid, activeSignups, totals) {
    if (!chip) return;

    if (activeSignups && activeSignups.length) {
      var first = activeSignups[0];
      chip.hidden = false;
      chip.className = 'volunteer-chip is-active' + (first.status === 'exception' ? ' is-pending' : '');
      chip.setAttribute('type', 'button');
      chip.setAttribute('data-volunteer-event', String(eid));
      chip.setAttribute('title', 'View or change volunteer role');
      var roleLabel = first.role_name + (first.status === 'exception' ? ' · pending' : '');
      if (activeSignups.length > 1) roleLabel += ' +' + (activeSignups.length - 1);
      chip.innerHTML = 'Volunteering · <span class="vol-role-name">' + roleLabel + '</span>';
      return;
    }

    if (totals && totals.open > 0) {
      chip.hidden = false;
      chip.className = 'volunteer-chip is-needed';
      chip.setAttribute('type', 'button');
      chip.setAttribute('data-volunteer-event', String(eid));
      chip.setAttribute('title', totals.confirmed + '/' + totals.capacity + ' volunteer slots filled');
      chip.innerHTML = '🤝 Volunteers needed · <span data-vol-summary>'
        + totals.open + ' role' + (totals.open === 1 ? '' : 's') + '</span>';
      return;
    }

    chip.hidden = true;
    chip.removeAttribute('data-volunteer-event');
  }

  function syncVolunteerStrip(eid, activeSignups) {
    var chip = getVolunteerChip(eid);
    if (!chip) return;

    var actions = document.querySelector('.signup-actions[data-event-id="' + eid + '"]');
    if (activeSignups && activeSignups.length) {
      if (actions) {
        actions.setAttribute('data-volunteering', '1');
        if (window.renderSignupActions) renderSignupActions(actions);
      }
      renderVolunteerChip(chip, eid, activeSignups, null);
      return;
    }

    if (actions) {
      actions.removeAttribute('data-volunteering');
      if (window.renderSignupActions) renderSignupActions(actions);
    }

    fetch('/calendar/' + eid + '/volunteer/slots')
      .then(function (r) { return r.json(); })
      .then(function (data) {
        var totals = slotTotals(data.slots || []);
        renderVolunteerChip(chip, eid, null, totals);
      })
      .catch(function () { /* noop */ });
  }

  function updateVolunteerStrip(eid, result) {
    var roleName = result && result.slot ? result.slot.role_name : '';
    var nextSignups = signups.slice();
    if (roleName) {
      nextSignups.push({ role_name: roleName, status: result.status || 'confirmed' });
    }
    syncVolunteerStrip(eid, nextSignups);
  }

  function submitSignup() {
    var picked = body.querySelector('input[name="vol_slot"]:checked');
    if (!picked) {
      alert('Select a volunteer role.');
      return;
    }
    var btn = body.querySelector('[data-vol-submit]');
    if (btn) { btn.disabled = true; btn.textContent = 'Signing up…'; }
    fetch('/calendar/' + eventId + '/volunteer/signup', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ slot_id: parseInt(picked.value, 10) }),
    }).then(function (r) { return r.json().then(function (d) { return { ok: r.ok, data: d }; }); })
      .then(function (res) {
        if (!res.ok) {
          alert(res.data.error || 'Signup failed.');
          if (btn) { btn.disabled = false; btn.textContent = 'Sign up'; }
          return;
        }
        renderConfirmation(res.data);
      }).catch(function () {
        alert('Signup failed. Try again.');
        if (btn) { btn.disabled = false; btn.textContent = 'Sign up'; }
      });
  }

  document.addEventListener('click', function (e) {
    var openBtn = e.target.closest('[data-volunteer-event]');
    if (openBtn) {
      if (window.closeAllEventActionMenus) closeAllEventActionMenus();
      openModal(parseInt(openBtn.getAttribute('data-volunteer-event'), 10));
      return;
    }
    if (e.target.closest('[data-vol-close]') || e.target === modal.querySelector('[data-vol-dismiss]')) {
      closeModal();
      return;
    }
    if (e.target.closest('[data-vol-submit]')) {
      submitSignup();
      return;
    }
    if (e.target.closest('[data-vol-done]')) {
      closeModal();
      return;
    }
    var withdrawBtn = e.target.closest('[data-vol-withdraw]');
    if (withdrawBtn) {
      var sid = withdrawBtn.getAttribute('data-vol-withdraw');
      if (!confirm('Withdraw from this volunteer role?')) return;
      fetch('/volunteer/signup/' + sid + '/withdraw', { method: 'POST' })
        .then(function (r) { return r.json(); })
        .then(function () {
          return loadData();
        })
        .then(function () {
          syncVolunteerStrip(eventId, signups);
        });
    }
  });

  modal.addEventListener('click', function (e) {
    if (e.target === modal) closeModal();
  });
})();
