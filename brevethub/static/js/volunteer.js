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
  var rideModeRequired = false;

  var body = modal.querySelector('[data-vol-body]');
  var title = modal.querySelector('[data-vol-title]');
  var dismissBtn = modal.querySelector('[data-vol-dismiss]');

  function esc(v) {
    return String(v == null ? '' : v)
      .replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;');
  }

  function ridePlanLabel(mode) {
    return mode === 'worker_ride' ? 'Worker ride' : 'Event day';
  }

  function setModalDismissible(dismissible) {
    rideModeRequired = !dismissible;
    if (dismissBtn) dismissBtn.hidden = !dismissible;
  }

  function closeModal() {
    if (rideModeRequired) return;
    modal.hidden = true;
    eventId = null;
    slots = [];
    signups = [];
    selectedSlotId = null;
    setModalDismissible(true);
    title.textContent = 'Volunteer signup';
  }

  function openModal(id) {
    eventId = id;
    modal.hidden = false;
    title.textContent = 'Volunteer signup';
    setModalDismissible(true);
    body.innerHTML = '<p class="text-text-light">Loading volunteer roles…</p>';
    loadData();
  }

  function openRidePlanEditor(id, ctx, required) {
    eventId = id;
    modal.hidden = false;
    renderRideModeChoice(ctx, required ? function () { closeModal(); } : function () { loadData(); }, required);
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
      var rm = statusData.ride_mode;
      if (rm && rm.worker_ride_enabled && rm.needs_ride_mode_choice) {
        renderRideModeChoice(rm, function () { loadData(); }, true);
        return;
      }
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

    html += '<div style="margin-top:16px;display:flex;gap:10px;flex-wrap:wrap;align-items:center;">';
    html += '<button type="button" class="signup-btn secondary" data-vol-close>Close</button>';
    html += '<button type="button" class="signup-btn register-primary" data-vol-change-plan hidden>Change ride plan</button>';
    html += '</div>';
    body.innerHTML = html;

    fetch('/calendar/' + eventId + '/ride-mode', { credentials: 'same-origin' })
      .then(function (r) {
        if (r.status === 401) return null;
        return r.json();
      })
      .then(function (data) {
        if (!data || !data.ride_mode || !data.ride_mode.worker_ride_enabled) return;
        var btn = body.querySelector('[data-vol-change-plan]');
        if (!btn) return;
        var label = data.ride_mode.ride_mode
          ? 'Change ride plan (' + ridePlanLabel(data.ride_mode.ride_mode) + ')'
          : 'Change ride plan';
        btn.textContent = label;
        btn.hidden = false;
        btn.addEventListener('click', function () {
          renderRideModeChoice(data.ride_mode, function () { loadData(); }, false);
        });
      })
      .catch(function () { /* noop */ });
  }

  function renderConfirmation(result) {
    updateVolunteerStrip(eventId, result);
    if (result.ride_mode && result.ride_mode.worker_ride_enabled && result.ride_mode.needs_ride_mode_choice) {
      renderRideModeChoice(result.ride_mode, function () { closeModal(); }, true);
      return;
    }
    body.innerHTML = ''
      + '<div style="text-align:center;padding:20px 0;">'
      + '<div style="font-size:2.5rem;margin-bottom:8px;">' + (result.needs_approval ? '⏳' : '✓') + '</div>'
      + '<h3 style="color:var(--primary);margin:0 0 8px;">' + esc(result.message) + '</h3>'
      + '<p class="text-text-light text-sm">' + esc(result.rider_name) + ' · ' + esc(result.slot.role_name) + '</p>'
      + '<button type="button" class="signup-btn register-primary" style="margin-top:20px;" data-vol-done>Done</button>'
      + '</div>';
  }

  function fmtWeekRange(startIso, endIso) {
    if (!startIso || !endIso) return '';
    var fmt = function (iso) {
      var d = new Date(iso + 'T12:00:00');
      return d.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' });
    };
    return fmt(startIso) + ' – ' + fmt(endIso);
  }

  function renderRideModeChoice(ctx, onDone, required) {
    setModalDismissible(!required);
    title.textContent = 'Ride plan';
    var suggested = ctx.suggested_ride_mode || 'worker_ride';
    var current = ctx.ride_mode || suggested;
    var week = fmtWeekRange(ctx.event_week_start, ctx.event_week_end);
    body.innerHTML = ''
      + '<div class="reg-event-banner" style="margin-bottom:16px;">'
      + '<div><div class="reg-event-type">Volunteer ride plan</div>'
      + '<div class="reg-event-name">Choose how you will ride</div>'
      + '<div class="reg-event-meta">Worker ride week: ' + esc(week) + '</div></div></div>'
      + '<p class="text-sm text-text-light mb-3">Choose event day or a worker ride during the event week. You can change this later from the event menu or volunteer panel.</p>'
      + '<div class="vol-slot-list" style="border:1px solid var(--border);border-radius:10px;overflow:hidden;margin-bottom:12px;">'
      + '<label class="vol-slot-option" style="display:flex;align-items:flex-start;gap:12px;padding:12px 14px;border-bottom:1px solid var(--border);cursor:pointer;">'
      + '<input type="radio" name="ride_mode" value="event_day"' + (current === 'event_day' ? ' checked' : '') + ' style="margin-top:4px;">'
      + '<span><strong>Event day</strong><br><span class="text-xs text-text-light">Ride on the scheduled brevet date.</span></span></label>'
      + '<label class="vol-slot-option" style="display:flex;align-items:flex-start;gap:12px;padding:12px 14px;cursor:pointer;">'
      + '<input type="radio" name="ride_mode" value="worker_ride"' + (current === 'worker_ride' ? ' checked' : '') + ' style="margin-top:4px;">'
      + '<span><strong>Worker ride</strong><br><span class="text-xs text-text-light">Same route, any day ' + esc(week) + '.</span></span></label>'
      + '</div>'
      + '<label class="flex items-start gap-2 text-sm mb-3"><input type="checkbox" name="ride_mode_ack">'
      + ' I understand this choice and will follow organizer guidance.</label>'
      + '<div style="display:flex;gap:10px;justify-content:flex-end;">'
      + (required ? '' : '<button type="button" class="signup-btn secondary" data-vol-close>Cancel</button>')
      + '<button type="button" class="signup-btn register-primary" data-ride-mode-save>Save ride plan</button>'
      + '</div>';

    body.querySelector('[data-ride-mode-save]').addEventListener('click', function () {
      var picked = body.querySelector('input[name="ride_mode"]:checked');
      var ack = body.querySelector('input[name="ride_mode_ack"]');
      if (!picked) { alert('Choose event day or worker ride.'); return; }
      if (!ack || !ack.checked) { alert('Please confirm your ride plan.'); return; }
      var btn = body.querySelector('[data-ride-mode-save]');
      btn.disabled = true;
      btn.textContent = 'Saving…';
      fetch('/calendar/' + eventId + '/ride-mode', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ride_mode: picked.value, acknowledged: true }),
      }).then(function (r) { return r.json().then(function (d) { return { ok: r.ok, data: d }; }); })
        .then(function (res) {
          if (!res.ok) {
            alert(res.data.error || 'Could not save ride plan.');
            btn.disabled = false;
            btn.textContent = 'Save ride plan';
            return;
          }
          setChipRidePlan(eventId, picked.value);
          setModalDismissible(true);
          body.innerHTML = ''
            + '<div style="text-align:center;padding:20px 0;">'
            + '<div style="font-size:2.5rem;margin-bottom:8px;">✓</div>'
            + '<h3 style="color:var(--primary);margin:0 0 8px;">Ride plan saved</h3>'
            + '<p class="text-text-light text-sm">' + ridePlanLabel(picked.value) + '</p>'
            + '<button type="button" class="signup-btn register-primary" style="margin-top:20px;" data-vol-done>Done</button>'
            + '</div>';
          body.querySelector('[data-vol-done]').addEventListener('click', function () {
            if (onDone) onDone();
            else closeModal();
          });
        });
    });
  }

  function getEventCard(eid) {
    return document.querySelector('.event-card[data-event-id="' + eid + '"]');
  }

  function getVolunteerChip(eid) {
    var card = getEventCard(eid);
    return card ? card.querySelector('[data-volunteer-chip]') : null;
  }

  function setChipRidePlan(eid, mode) {
    var chip = getVolunteerChip(eid);
    if (!chip || !mode) return;
    chip.setAttribute('data-ride-plan', mode);
    var planEl = chip.querySelector('.vol-ride-plan');
    var label = ridePlanLabel(mode);
    if (planEl) {
      planEl.textContent = label;
    } else {
      chip.insertAdjacentHTML('beforeend', ' · <span class="vol-ride-plan">' + esc(label) + '</span>');
    }
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

  function renderVolunteerChip(chip, eid, activeSignups, totals, ridePlan) {
    if (!chip) return;

    if (activeSignups && activeSignups.length) {
      var first = activeSignups[0];
      chip.hidden = false;
      chip.className = 'volunteer-chip is-active' + (first.status === 'exception' ? ' is-pending' : '');
      chip.setAttribute('type', 'button');
      chip.setAttribute('data-volunteer-event', String(eid));
      chip.setAttribute('title', 'View volunteer role or change ride plan');
      var roleLabel = first.role_name + (first.status === 'exception' ? ' · pending' : '');
      if (activeSignups.length > 1) roleLabel += ' +' + (activeSignups.length - 1);
      var html = 'Volunteering · <span class="vol-role-name">' + esc(roleLabel) + '</span>';
      if (ridePlan) {
        chip.setAttribute('data-ride-plan', ridePlan);
        html += ' · <span class="vol-ride-plan">' + esc(ridePlanLabel(ridePlan)) + '</span>';
      } else {
        chip.removeAttribute('data-ride-plan');
      }
      chip.innerHTML = html;
      return;
    }

    if (totals && totals.open > 0) {
      chip.hidden = false;
      chip.className = 'volunteer-chip is-needed';
      chip.setAttribute('type', 'button');
      chip.setAttribute('data-volunteer-event', String(eid));
      chip.setAttribute('title', totals.confirmed + '/' + totals.capacity + ' volunteer slots filled');
      chip.removeAttribute('data-ride-plan');
      chip.innerHTML = '🤝 Volunteers needed · <span data-vol-summary>'
        + totals.open + ' role' + (totals.open === 1 ? '' : 's') + '</span>';
      return;
    }

    chip.hidden = true;
    chip.removeAttribute('data-volunteer-event');
    chip.removeAttribute('data-ride-plan');
  }

  function syncVolunteerStrip(eid, activeSignups) {
    var chip = getVolunteerChip(eid);
    if (!chip) return;

    var actions = document.querySelector('.signup-actions[data-event-id="' + eid + '"]');
    if (activeSignups && activeSignups.length) {
      if (actions) {
        actions.setAttribute('data-volunteering', '1');
        if (window.renderSignupActions) window.renderSignupActions(actions);
      }
      fetch('/calendar/' + eid + '/ride-mode', { credentials: 'same-origin' })
        .then(function (r) {
          if (r.status === 401) return null;
          return r.json();
        })
        .then(function (data) {
          var plan = null;
          if (data && data.ride_mode && data.ride_mode.ride_mode_ack_at) {
            plan = data.ride_mode.ride_mode;
          }
          renderVolunteerChip(chip, eid, activeSignups, null, plan);
        })
        .catch(function () {
          renderVolunteerChip(chip, eid, activeSignups, null, chip.getAttribute('data-ride-plan'));
        });
      return;
    }

    if (actions) {
      actions.removeAttribute('data-volunteering');
      if (window.renderSignupActions) window.renderSignupActions(actions);
    }

    fetch('/calendar/' + eid + '/volunteer/slots')
      .then(function (r) { return r.json(); })
      .then(function (data) {
        var totals = slotTotals(data.slots || []);
        renderVolunteerChip(chip, eid, null, totals, null);
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
    var ridePlanBtn = e.target.closest('[data-ride-plan-event]');
    if (ridePlanBtn) {
      if (window.closeAllEventActionMenus) window.closeAllEventActionMenus();
      var eid = parseInt(ridePlanBtn.getAttribute('data-ride-plan-event'), 10);
      fetch('/calendar/' + eid + '/ride-mode', { credentials: 'same-origin' })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (!data || !data.ride_mode) return;
          openRidePlanEditor(eid, data.ride_mode, !!data.ride_mode.needs_ride_mode_choice);
        });
      return;
    }

    var openBtn = e.target.closest('[data-volunteer-event]');
    if (openBtn) {
      if (window.closeAllEventActionMenus) window.closeAllEventActionMenus();
      openModal(parseInt(openBtn.getAttribute('data-volunteer-event'), 10));
      return;
    }
    if (e.target.closest('[data-vol-close]') || e.target === dismissBtn) {
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
        .then(function (res) {
          if (res.ride_mode && res.ride_mode.worker_ride_enabled && res.ride_mode.needs_ride_mode_choice) {
            renderRideModeChoice(res.ride_mode, function () { loadData(); }, true);
            return;
          }
          return loadData();
        })
        .then(function (result) {
          if (result === undefined) return;
          syncVolunteerStrip(eventId, signups);
        });
    }
  });

  modal.addEventListener('click', function (e) {
    if (e.target === modal && !rideModeRequired) closeModal();
  });
})();
