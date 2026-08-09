/**
 * BrevetHub registration wizard — single + bulk + team, inline profile edit,
 * enhanced waiver (adult/minor, e-sig, SmartWaiver option).
 */
(function () {
  var modal = document.getElementById('registration-modal');
  if (!modal) return;

  var mode = 'single'; // single | bulk | team
  var step = 1;
  var eventId = null;
  var eventIds = [];
  var eventData = null;
  var bulkData = null;
  var profileData = null;
  var waiverAccepted = false;
  var profileEditing = false;

  var waiverData = {
    method: 'in_app',            // 'in_app' | 'smartwaiver'
    is_minor: false,
    signatory_name: '',
    guardian_name: '',
    guardian_phone: '',
    age_certified: false,
    esign_consented: false,
    smartwaiver_completed: false,
  };

  var teamData = {
    team_name: '',
    proof_method: 'brevet_card',
    rwgps_url: '',
    notes: '',
    members: [
      { first_name: '', last_name: '', rusa_id: '' },
      { first_name: '', last_name: '', rusa_id: '' },
    ],
  };

  function resetWaiverData() {
    waiverData = {
      method: 'in_app', is_minor: false, signatory_name: '',
      guardian_name: '', guardian_phone: '', age_certified: false,
      esign_consented: false, smartwaiver_completed: false,
    };
  }

  function esc(v) {
    return String(v == null ? '' : v)
      .replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;');
  }

  function buildSmartWaiverUrl(profile, ev) {
    var base = 'https://waiver.smartwaiver.com/w/61c4b84e2bc70/web/?';
    var parts = [];
    function add(k, v) {
      if (v) parts.push(encodeURIComponent(k) + '=' + encodeURIComponent(String(v)));
    }
    add('firstname', profile.first_name);
    add('lastname', profile.last_name);
    add('email', profile.email);
    add('phone', profile.phone);
    add('field[RUSA Member Number]', profile.rusa_id);
    if (ev) { add('field[Event Date]', ev.date); add('field[Event Name]', ev.name); }
    return parts.length ? base + parts.join('&') : base.slice(0, -1);
  }

  function isWaiverValid() {
    if (waiverData.method === 'smartwaiver') return waiverData.smartwaiver_completed;
    if (waiverData.is_minor) {
      return !!(waiverData.guardian_name && waiverData.guardian_phone && waiverData.esign_consented);
    }
    return !!(waiverData.age_certified
      && waiverData.signatory_name && waiverData.signatory_name.trim().length > 1
      && waiverData.esign_consented);
  }

  var els = {
    backdrop: modal,
    stepLabel: modal.querySelector('[data-reg-step-label]'),
    body: modal.querySelector('[data-reg-body]'),
    close: modal.querySelector('[data-reg-close]'),
    bulkBar: document.getElementById('bulk-register-bar'),
    bulkCount: document.querySelector('[data-bulk-count]'),
  };

  function fmtDate(iso) {
    if (!iso) return '—';
    var d = new Date(iso + 'T12:00:00');
    return d.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' });
  }

  function fmtMoney(cents) {
    if (cents == null) return '—';
    return '$' + (cents / 100).toFixed(0);
  }

  function fmtDate(iso) {
    if (!iso) return '—';
    var d = new Date(iso + 'T12:00:00');
    return d.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' });
  }

  function fmtMoney(cents) {
    if (cents == null) return '—';
    return '$' + (cents / 100).toFixed(0);
  }

  function setStep(n) {
    step = n;
    var labels = mode === 'bulk'
      ? ['Selected events', 'Profile & Waiver', 'Confirm all']
      : mode === 'team'
        ? ['Event Details', 'Team & Members', 'Confirm']
        : ['Event Details', 'Profile & Waiver', 'Confirm'];
    els.stepLabel.textContent = 'Step ' + step + ' of 3 · ' + labels[step - 1];
  }

  function closeModal() {
    modal.hidden = true;
    document.body.style.overflow = '';
    mode = 'single';
    eventId = null;
    eventIds = [];
    eventData = null;
    bulkData = null;
    profileData = null;
    waiverAccepted = false;
    profileEditing = false;
    step = 1;
    resetWaiverData();
    teamData = { team_name: '', proof_method: 'brevet_card', rwgps_url: '', notes: '',
      members: [{ first_name: '', last_name: '', rusa_id: '' }, { first_name: '', last_name: '', rusa_id: '' }] };
  }

  function renderWaiverSection(waiverText, ev) {
    var profile = (profileData && profileData.profile) || {};
    var isInApp = waiverData.method !== 'smartwaiver';
    var html = '<div class="reg-waiver-section">' +
      '<div class="reg-waiver-header">' +
      '<p class="reg-section-title">Event waiver</p>' +
      '<div class="reg-waiver-tabs">' +
      '<button type="button" class="reg-tab' + (isInApp ? ' active' : '') + '" data-waiver-method="in_app">Sign in-app</button>' +
      '<button type="button" class="reg-tab' + (!isInApp ? ' active' : '') + '" data-waiver-method="smartwaiver">Use SmartWaiver site</button>' +
      '</div></div>';

    if (isInApp) {
      var guardianFields = waiverData.is_minor
        ? '<div class="reg-guardian-fields">' +
          '<label class="reg-form-field"><span>Parent / guardian full name</span>' +
          '<input name="guardian_name" type="text" value="' + esc(waiverData.guardian_name) + '" placeholder="Guardian full name"></label>' +
          '<label class="reg-form-field"><span>Guardian phone</span>' +
          '<input name="guardian_phone" type="tel" value="' + esc(waiverData.guardian_phone) + '" placeholder="415-555-0142"></label>' +
          '</div>'
        : '';
      var ageCheck = waiverData.is_minor ? '' :
        '<label class="reg-waiver-check"><input type="checkbox" name="age_certified"' +
        (waiverData.age_certified ? ' checked' : '') + '> I, the participant, confirm that I am 18 years of age or older.</label>';
      var waiverFormatted = (waiverText || '').replace(/\n\n/g, '</p><p>').replace(/^/, '<p>').replace(/$/, '</p>');
      html +=
        '<div class="reg-waiver-minor-toggle">' +
        '<p class="reg-waiver-question"><strong>Please select who will be participating.</strong></p>' +
        '<label class="reg-radio-btn"><input type="radio" name="is_minor" value="adult"' +
        (!waiverData.is_minor ? ' checked' : '') + '> Adult (18+)</label>' +
        '<label class="reg-radio-btn"><input type="radio" name="is_minor" value="minor"' +
        (waiverData.is_minor ? ' checked' : '') + '> Minor (under 18)</label>' +
        '</div>' +
        ageCheck + guardianFields +
        '<div class="reg-waiver-box">' + waiverFormatted + '</div>' +
        '<div class="reg-esign-block">' +
        '<div class="reg-esign-notice">' +
        '<p><strong>Electronic Signature Consent</strong></p>' +
        '<p>By checking the box and typing your name below, you agree that your electronic signature is the legally binding equivalent of your handwritten signature. You consent to be legally bound by this waiver\'s terms. You agree that no certification authority or other third-party verification is necessary to validate your electronic signature, and that the lack of such certification or third-party verification will not in any way affect the enforceability of your electronic signature or the resulting contract between you and San Francisco Randonneurs.</p>' +
        '<p>You understand that you may request a paper copy of this document by contacting San Francisco Randonneurs. Your electronic signature on this Agreement is as valid as if you signed the Agreement in writing. You are also confirming that you are authorized to enter into this Agreement.</p>' +
        '</div>' +
        '<label class="reg-form-field"><span>Type your full legal name as your electronic signature</span>' +
        '<input name="signatory_name" type="text" value="' + esc(waiverData.signatory_name) + '" placeholder="Full legal name" autocomplete="name"></label>' +
        '<label class="reg-waiver-check"><input type="checkbox" name="esign_consented"' +
        (waiverData.esign_consented ? ' checked' : '') + '> I have read and agree to the terms of this waiver and I consent to the use of my electronic signature in lieu of a handwritten signature.</label>' +
        '</div>';
    } else {
      var swUrl = buildSmartWaiverUrl(profile, ev);
      html +=
        '<div class="reg-smartwaiver-block">' +
        '<p>Click below — your name, email, and phone are pre-filled. Complete the waiver, then return here to confirm.</p>' +
        '<a href="' + swUrl + '" target="_blank" rel="noopener noreferrer" class="reg-sw-btn">Open SmartWaiver ↗</a>' +
        '<label class="reg-waiver-check" style="margin-top:14px">' +
        '<input type="checkbox" name="smartwaiver_completed"' + (waiverData.smartwaiver_completed ? ' checked' : '') + '> ' +
        'I have completed and submitted the SmartWaiver form.</label>' +
        '</div>';
    }
    return html + '</div>';
  }

  function bindWaiverSection(onRerender) {
    var section = els.body.querySelector('.reg-waiver-section');
    if (!section) return;

    function syncNextBtn() {
      waiverAccepted = isWaiverValid();
      var next = els.body.querySelector('[data-reg-next]');
      if (!next) return;
      var evaln = (profileData && profileData.evaluation) || {};
      next.disabled = !waiverAccepted || !!(evaln.blockers && evaln.blockers.length);
    }

    section.querySelectorAll('[data-waiver-method]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        waiverData.method = btn.getAttribute('data-waiver-method');
        onRerender();
      });
    });

    section.querySelectorAll('input[name="is_minor"]').forEach(function (radio) {
      radio.addEventListener('change', function () {
        waiverData.is_minor = (radio.value === 'minor');
        onRerender();
      });
    });

    var ageCert = section.querySelector('input[name="age_certified"]');
    if (ageCert) ageCert.addEventListener('change', function () { waiverData.age_certified = ageCert.checked; syncNextBtn(); });

    var gName = section.querySelector('input[name="guardian_name"]');
    if (gName) gName.addEventListener('input', function () { waiverData.guardian_name = gName.value; syncNextBtn(); });
    var gPhone = section.querySelector('input[name="guardian_phone"]');
    if (gPhone) gPhone.addEventListener('input', function () { waiverData.guardian_phone = gPhone.value; syncNextBtn(); });

    var sigName = section.querySelector('input[name="signatory_name"]');
    if (sigName) sigName.addEventListener('input', function () { waiverData.signatory_name = sigName.value; syncNextBtn(); });

    var esign = section.querySelector('input[name="esign_consented"]');
    if (esign) esign.addEventListener('change', function () { waiverData.esign_consented = esign.checked; syncNextBtn(); });

    var swDone = section.querySelector('input[name="smartwaiver_completed"]');
    if (swDone) swDone.addEventListener('change', function () { waiverData.smartwaiver_completed = swDone.checked; syncNextBtn(); });

    syncNextBtn();
  }



  function stat(label, value) {
    return '<div class="reg-stat"><div class="reg-stat-label">' + label + '</div><div class="reg-stat-value">' + value + '</div></div>';
  }

  function selectedBulkIds() {
    var seen = {};
    var ids = [];
    document.querySelectorAll('.bulk-event-select:checked').forEach(function (cb) {
      var id = parseInt(cb.value, 10);
      if (!seen[id]) {
        seen[id] = true;
        ids.push(id);
      }
    });
    return ids;
  }

  function syncBulkCheckbox(source) {
    document.querySelectorAll('.bulk-event-select[value="' + source.value + '"]').forEach(function (cb) {
      if (cb !== source) cb.checked = source.checked;
    });
  }

  function updateBulkBar() {
    var ids = selectedBulkIds();
    if (!els.bulkBar) return;
    els.bulkBar.hidden = ids.length === 0;
    if (els.bulkCount) els.bulkCount.textContent = ids.length + ' event' + (ids.length === 1 ? '' : 's') + ' selected';
  }

  function profileReadonlyHtml(p, complete) {
    return (
      (complete ? '<p class="reg-profile-note">Using your saved profile — no need to re-enter details each time.</p>' : '') +
      '<div class="reg-profile-grid">' +
      field('Name', p.name) +
      field('Email', p.email) +
      field('Phone', p.phone || '—') +
      field('City', p.city || '—') +
      field('RUSA #', p.rusa_id || '—') +
      field('Emergency', (p.emergency_name || '—') + ' · ' + (p.emergency_phone || '—')) +
      field('SFR member', p.sfr_member_year || '—') +
      '</div>'
    );
  }

  function field(label, value) {
    return '<div><div class="reg-field-label">' + label + '</div><div class="reg-field-value">' + value + '</div></div>';
  }

  function profileEditHtml(p) {
    return (
      '<div class="reg-profile-form">' +
      input('first_name', 'First name', p.first_name) +
      input('last_name', 'Last name', p.last_name) +
      input('phone', 'Phone', p.phone) +
      input('city', 'City', p.city) +
      input('rusa_id', 'RUSA #', p.rusa_id) +
      input('sfr_member_year', 'SFR membership year', p.sfr_member_year, 'number') +
      input('emergency_name', 'Emergency contact', p.emergency_name) +
      input('emergency_phone', 'Emergency phone', p.emergency_phone) +
      '<button type="button" class="reg-secondary-btn" data-reg-save-profile>Save profile</button>' +
      '<p class="reg-save-msg" data-reg-save-msg hidden></p></div>'
    );
  }

  function validUsPhone(value) {
    var digits = String(value || '').replace(/\D/g, '');
    if (digits.length === 11 && digits.charAt(0) === '1') digits = digits.slice(1);
    if (digits.length !== 10) return false;
    if ('01'.indexOf(digits.charAt(0)) >= 0 || '01'.indexOf(digits.charAt(3)) >= 0) return false;
    return true;
  }

  function clearFieldErrors(form) {
    if (!form) return;
    form.querySelectorAll('[data-field-error]').forEach(function (el) {
      el.hidden = true;
      el.textContent = '';
    });
    form.querySelectorAll('input, select').forEach(function (inp) {
      inp.classList.remove('reg-input-error');
      inp.removeAttribute('aria-invalid');
    });
  }

  function showFieldErrors(form, fieldErrors) {
    clearFieldErrors(form);
    if (!fieldErrors) return;
    Object.keys(fieldErrors).forEach(function (name) {
      var inp = form.querySelector('[name="' + name + '"]');
      if (!inp) return;
      inp.classList.add('reg-input-error');
      inp.setAttribute('aria-invalid', 'true');
      var wrap = inp.closest('.reg-form-field');
      var err = wrap ? wrap.querySelector('[data-field-error]') : null;
      if (err) {
        err.hidden = false;
        err.textContent = fieldErrors[name];
      }
    });
  }

  function clientPhoneFieldErrors(payload) {
    var fieldErrors = {};
    if (!validUsPhone(payload.phone)) {
      fieldErrors.phone = payload.phone
        ? 'Enter a valid US phone number (10 digits).'
        : 'Phone is required.';
    }
    if (!validUsPhone(payload.emergency_phone)) {
      fieldErrors.emergency_phone = payload.emergency_phone
        ? 'Enter a valid US phone number (10 digits).'
        : 'Emergency phone is required.';
    }
    return fieldErrors;
  }

  function input(name, label, value, type) {
    type = type || 'text';
    var extra = '';
    if (name === 'phone' || name === 'emergency_phone') {
      type = 'tel';
      extra = ' placeholder="415-555-0142" title="US phone number, 10 digits"';
    }
    return '<label class="reg-form-field"><span>' + label + '</span>' +
      '<input name="' + name + '" type="' + type + '" value="' +
      (value == null ? '' : String(value).replace(/"/g, '&quot;')) + '"' + extra + '>' +
      '<span class="reg-field-error" data-field-error hidden></span></label>';
  }

  function renderProfileSection() {
    var p = profileData.profile;
    var fs = profileData.field_status || {};
    var evaln = profileData.evaluation || {};
    var blockers = (evaln.blockers || []).map(function (b) { return '<li>' + b + '</li>'; }).join('');
    var incomplete = !fs.complete;
    profileEditing = incomplete || profileEditing;

    var profileBlock =
      '<div class="reg-profile-head">' +
      '<span class="reg-section-title">Your profile</span>' +
      (profileEditing
        ? '<button type="button" class="reg-edit-link" data-reg-view-profile>Use saved profile</button>'
        : '<button type="button" class="reg-edit-link" data-reg-edit-profile>Edit details</button>') +
      '</div>' +
      (profileEditing ? profileEditHtml(p) : profileReadonlyHtml(p, fs.complete));

    var ev = (mode === 'single' && eventData) ? eventData.event : null;
    return (
      (blockers ? '<ul class="reg-blockers">' + blockers + '</ul>' : '') +
      profileBlock +
      renderWaiverSection((profileData.waiver && profileData.waiver.text) || '', ev)
    );
  }

  function bindProfileSection() {
    var evaln = profileData.evaluation || {};

    function onWaiverRerender() { rerenderStep2(); }

    bindWaiverSection(onWaiverRerender);

    var editBtn = els.body.querySelector('[data-reg-edit-profile]');
    if (editBtn) {
      editBtn.addEventListener('click', function () {
        profileEditing = true;
        rerenderStep2();
      });
    }
    var viewBtn = els.body.querySelector('[data-reg-view-profile]');
    if (viewBtn) {
      viewBtn.addEventListener('click', function () {
        profileEditing = false;
        rerenderStep2();
      });
    }
    var saveBtn = els.body.querySelector('[data-reg-save-profile]');
    if (saveBtn) {
      saveBtn.addEventListener('click', function () {
        var form = els.body.querySelector('.reg-profile-form');
        var payload = {};
        form.querySelectorAll('input[name]').forEach(function (inp) {
          payload[inp.name] = inp.value;
        });
        var clientErrors = clientPhoneFieldErrors(payload);
        if (Object.keys(clientErrors).length) {
          showFieldErrors(form, clientErrors);
          return;
        }
        clearFieldErrors(form);
        saveBtn.disabled = true;
        fetch('/register/profile/quick-save', {
          method: 'POST',
          credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        }).then(function (r) { return r.json().then(function (d) { return { ok: r.ok, data: d }; }); })
          .then(function (res) {
            saveBtn.disabled = false;
            var msg = els.body.querySelector('[data-reg-save-msg]');
            if (!res.ok) {
              if (res.data.field_errors) {
                showFieldErrors(form, res.data.field_errors);
              }
              if (msg) {
                msg.hidden = false;
                msg.textContent = res.data.error || 'Save failed';
                msg.className = 'reg-save-msg reg-error';
              }
              return;
            }
            clearFieldErrors(form);
            profileData.profile = res.data.profile;
            profileData.field_status = res.data.field_status;
            profileData.evaluation = profileData.evaluation || {};
            if (mode === 'single' && eventId) {
              return fetch('/calendar/' + eventId + '/register/profile', { credentials: 'same-origin' })
                .then(function (r) { return r.json(); })
                .then(function (d) { profileData = d; profileEditing = false; rerenderStep2(); });
            }
            profileEditing = false;
            rerenderStep2();
            if (msg) { msg.hidden = false; msg.textContent = 'Profile saved.'; msg.className = 'reg-save-msg reg-ok'; }
          });
      });
    }
  }

  function rerenderStep2() {
    els.body.innerHTML = renderProfileSection() +
      '<div class="reg-actions"><button type="button" class="reg-secondary-btn" data-reg-back>← Back</button>' +
      '<button type="button" class="reg-primary-btn" data-reg-next disabled>Proceed to Confirm →</button></div>';
    els.body.querySelector('[data-reg-back]').addEventListener('click', function () {
      mode === 'bulk' ? renderBulkStep1() : renderStep1();
    });
    els.body.querySelector('[data-reg-next]').addEventListener('click', renderStep3);
    bindProfileSection();
  }

  function renderStep1() {
    setStep(1);
    var ev = eventData.event;
    var controls = (ev.controls || []).map(function (c) { return '<li>' + c + '</li>'; }).join('');
    var teamNote = ev.is_team_event
      ? '<div class="reg-team-note"><strong>Team event</strong> — register yourself as a team captain and add your team members in the next step.</div>'
      : '';
    var teamBtn = ev.is_team_event
      ? '<button type="button" class="reg-primary-btn" data-reg-team>Register my team →</button>' +
        '<button type="button" class="reg-secondary-btn" data-reg-next style="margin-top:6px">Individual sign-up (GOING) →</button>'
      : '<button type="button" class="reg-primary-btn" data-reg-next>Continue →</button>';
    els.body.innerHTML =
      '<div class="reg-event-banner">' +
      '<div><div class="reg-event-type">' + (ev.ride_type || 'Brevet') + '</div>' +
      '<h3 class="reg-event-name">' + ev.name + '</h3>' +
      '<p class="reg-event-meta">' + fmtDate(ev.date) + ' · ' + (ev.start_time || '—') + ' · ' + (ev.start_location || '—') + '</p></div>' +
      '<div class="reg-event-km">' + ev.distance_km + '<span>km</span></div></div>' +
      '<div class="reg-stats-grid">' +
      stat('Time limit', ev.time_limit_hours ? ev.time_limit_hours + ' hrs' : '—') +
      stat('Entry fee', fmtMoney(ev.fee_cents)) +
      stat('Deadline', ev.registration_deadline ? fmtDate(ev.registration_deadline) : '—') +
      stat('Spots', ev.capacity != null ? (ev.spots_open != null ? ev.spots_open + ' of ' + ev.capacity : ev.capacity) : 'Open') +
      '</div>' +
      (controls ? '<div class="reg-controls"><p class="reg-section-label">Controls</p><ul>' + controls + '</ul></div>' : '') +
      teamNote +
      teamBtn;
    if (ev.is_team_event) {
      var teamBtn2 = els.body.querySelector('[data-reg-team]');
      if (teamBtn2) teamBtn2.addEventListener('click', loadTeamStep2);
    }
    els.body.querySelector('[data-reg-next]').addEventListener('click', loadStep2);
  }

  function renderBulkStep1() {
    setStep(1);
    var items = (bulkData.events || []).map(function (ev) {
      var flag = ev.already_registered ? ' (already registered)' : '';
      var blocked = ev.evaluation && !ev.evaluation.ok ? ' — blocked' : '';
      return '<li>' + fmtDate(ev.date) + ' · <strong>' + ev.name + '</strong> · ' + ev.distance_km + 'km' + flag + blocked + '</li>';
    }).join('');
    var total = (bulkData.events || []).reduce(function (sum, ev) {
      return sum + (ev.fee_cents || 0);
    }, 0);
    els.body.innerHTML =
      '<p class="reg-section-title">' + eventIds.length + ' events selected</p>' +
      '<ul class="reg-bulk-list">' + items + '</ul>' +
      (total ? '<p>Total fees: <strong>' + fmtMoney(total) + '</strong> (not charged today)</p>' : '') +
      '<button type="button" class="reg-primary-btn" data-reg-next>Continue with saved profile →</button>';
    els.body.querySelector('[data-reg-next]').addEventListener('click', loadBulkStep2);
  }

  function loadStep2() {
    fetch('/calendar/' + eventId + '/register/profile', { credentials: 'same-origin' })
      .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, data: d }; }); })
      .then(function (res) {
        if (!res.ok) {
          if (res.data && res.data.login_url) window.location = res.data.login_url;
          return;
        }
        profileData = res.data;
        profileEditing = !(profileData.field_status && profileData.field_status.complete);
        setStep(2);
        rerenderStep2();
      });
  }

  function loadBulkStep2() {
    fetch('/register/bulk/preview', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ event_ids: eventIds }),
    }).then(function (r) { return r.json().then(function (d) { return { ok: r.ok, data: d }; }); })
      .then(function (res) {
        if (!res.ok) return;
        bulkData = res.data;
        profileData = {
          profile: res.data.profile,
          field_status: res.data.field_status,
          waiver: res.data.waiver,
          evaluation: { blockers: res.data.blockers || [] },
        };
        profileEditing = !(profileData.field_status && profileData.field_status.complete);
        setStep(2);
        rerenderStep2();
      });
  }

  function renderStep3() {
    setStep(3);
    if (mode === 'bulk') {
      els.body.innerHTML =
        '<div class="reg-summary card"><p class="reg-section-title">Confirm bulk registration</p>' +
        '<p>Register for <strong>' + eventIds.length + ' events</strong> using your saved profile.</p>' +
        '<p class="text-text-light text-sm">Payment is not enabled yet. This records waiver acceptance and roster placement for each event.</p></div>' +
        '<div class="reg-actions"><button type="button" class="reg-secondary-btn" data-reg-back>← Back</button>' +
        '<button type="button" class="reg-primary-btn reg-confirm-btn" data-reg-confirm>Confirm all</button></div>';
    } else {
      var ev = eventData.event;
      els.body.innerHTML =
        '<div class="reg-summary card"><p class="reg-section-title">Ready to confirm</p>' +
        '<p>Register for <strong>' + ev.name + '</strong> on <strong>' + fmtDate(ev.date) + '</strong>.</p>' +
        (ev.fee_cents != null ? '<p>Entry fee: <strong>' + fmtMoney(ev.fee_cents) + '</strong> (no charge today).</p>' : '') +
        '</div>' +
        '<div class="reg-actions"><button type="button" class="reg-secondary-btn" data-reg-back>← Back</button>' +
        '<button type="button" class="reg-primary-btn reg-confirm-btn" data-reg-confirm>Confirm registration</button></div>';
    }
    els.body.querySelector('[data-reg-back]').addEventListener('click', rerenderStep2);
    els.body.querySelector('[data-reg-confirm]').addEventListener('click', confirmRegistration);
  }

  function confirmRegistration() {
    var btn = els.body.querySelector('[data-reg-confirm]');
    btn.disabled = true;
    var url = mode === 'bulk' ? '/register/bulk/confirm' : '/calendar/' + eventId + '/register/confirm';
    var body = {
      waiver_accepted: waiverAccepted,
      waiver_version_id: profileData.waiver.version_id,
      waiver_method: waiverData.method,
      is_minor: waiverData.is_minor,
      signatory_name: waiverData.signatory_name || null,
      guardian_name: waiverData.guardian_name || null,
      guardian_phone: waiverData.guardian_phone || null,
      age_certified: waiverData.age_certified,
      esign_consented: waiverData.esign_consented,
    };
    if (mode === 'bulk') body.event_ids = eventIds;

    fetch(url, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }).then(function (r) { return r.json().then(function (d) { return { ok: r.ok, data: d }; }); })
      .then(function (res) {
        if (!res.ok) {
          alert(res.data.error || 'Registration failed');
          btn.disabled = false;
          return;
        }
        if (mode === 'bulk') renderBulkConfirmation(res.data);
        else renderConfirmation(res.data);
      });
  }

  function renderConfirmation(data) {
    setStep(3);
    var ev = data.event;
    els.body.innerHTML =
      '<div class="reg-success"><div class="reg-success-icon">✓</div><h3>You\'re on the roster!</h3></div>' +
      '<div class="reg-brevet-card"><div class="reg-card-title">' + ev.name + '</div>' +
      '<div class="reg-card-meta">' + fmtDate(ev.date) + ' · ' + ev.distance_km + 'km</div>' +
      '<div class="reg-card-grid"><div><span>Confirmation</span><strong>' + data.confirmation_code + '</strong></div></div></div>' +
      '<button type="button" class="reg-primary-btn" data-reg-done>Done</button>';
    els.body.querySelector('[data-reg-done]').addEventListener('click', closeModal);
    document.dispatchEvent(new CustomEvent('brevethub:registered', { detail: { eventId: eventId } }));
  }

  function renderBulkConfirmation(data) {
    setStep(3);
    var list = (data.confirmed || []).map(function (row) {
      return '<li>' + row.event.name + ' · ' + row.confirmation_code + '</li>';
    }).join('');
    els.body.innerHTML =
      '<div class="reg-success"><div class="reg-success-icon">✓</div>' +
      '<h3>Registered for ' + (data.confirmed || []).length + ' events</h3></div>' +
      '<ul class="reg-bulk-list">' + list + '</ul>' +
      (data.failed && data.failed.length ? '<p class="reg-error">' + data.failed.length + ' could not be registered.</p>' : '') +
      '<button type="button" class="reg-primary-btn" data-reg-done>Done</button>';
    els.body.querySelector('[data-reg-done]').addEventListener('click', function () {
      document.querySelectorAll('.bulk-event-select:checked').forEach(function (cb) { cb.checked = false; });
      updateBulkBar();
      closeModal();
      (data.confirmed || []).forEach(function (row) {
        document.dispatchEvent(new CustomEvent('brevethub:registered', { detail: { eventId: row.event_id } }));
      });
    });
  }

  // ── Team event wizard ──────────────────────────────────────────────────────

  function memberRow(idx, m) {
    return '<div class="reg-team-member-row">' +
      '<span class="reg-member-label">Member ' + (idx + 2) + '</span>' +
      '<input name="member_' + idx + '_first" type="text" placeholder="First name" value="' + esc(m.first_name) + '" class="reg-member-input">' +
      '<input name="member_' + idx + '_last" type="text" placeholder="Last name" value="' + esc(m.last_name) + '" class="reg-member-input">' +
      '<input name="member_' + idx + '_rusa" type="text" placeholder="RUSA # (opt.)" value="' + esc(m.rusa_id) + '" class="reg-member-input reg-member-rusa">' +
      '</div>';
  }

  function renderTeamStep2() {
    setStep(2);
    var profile = profileData ? profileData.profile : {};
    var membersHtml = teamData.members.map(function (m, i) { return memberRow(i, m); }).join('');
    var rwgpsField = teamData.proof_method === 'gps_track'
      ? '<label class="reg-form-field"><span>RWGPS route URL</span>' +
        '<input name="rwgps_url" type="url" placeholder="https://ridewithgps.com/routes/…" value="' + esc(teamData.rwgps_url) + '"></label>'
      : '';
    els.body.innerHTML =
      '<p class="reg-section-title">Team details</p>' +
      '<div class="reg-team-form">' +
      '<label class="reg-form-field"><span>Team name</span>' +
      '<input name="team_name" type="text" value="' + esc(teamData.team_name) + '" placeholder="e.g. Bay Randonneurs Express" required></label>' +
      '<div class="reg-captain-info">' +
      '<p class="reg-section-label">Captain (you)</p>' +
      '<div class="reg-profile-grid">' +
      field('Name', (profile.first_name || '') + ' ' + (profile.last_name || '')) +
      field('RUSA #', profile.rusa_id || '—') +
      '</div></div>' +
      '<p class="reg-section-label">Team members (2–4 additional riders)</p>' +
      '<div class="reg-team-members">' + membersHtml + '</div>' +
      '<button type="button" class="reg-secondary-btn" data-reg-add-member style="margin-bottom:14px">+ Add another member</button>' +
      '<p class="reg-section-label">Proof method</p>' +
      '<div class="reg-proof-toggle">' +
      '<label class="reg-radio-btn"><input type="radio" name="proof_method" value="brevet_card"' +
      (teamData.proof_method !== 'gps_track' ? ' checked' : '') + '> Brevet card</label>' +
      '<label class="reg-radio-btn"><input type="radio" name="proof_method" value="gps_track"' +
      (teamData.proof_method === 'gps_track' ? ' checked' : '') + '> GPS track (RWGPS)</label>' +
      '</div>' +
      '<div id="rwgps-field">' + rwgpsField + '</div>' +
      '<label class="reg-form-field"><span>Notes for organizer (optional)</span>' +
      '<input name="notes" type="text" value="' + esc(teamData.notes) + '" placeholder="Any special notes…"></label>' +
      '</div>' +
      '<div class="reg-actions"><button type="button" class="reg-secondary-btn" data-reg-back>← Back</button>' +
      '<button type="button" class="reg-primary-btn" data-reg-next>Review & Confirm →</button></div>';

    els.body.querySelector('[data-reg-back]').addEventListener('click', renderStep1);
    els.body.querySelector('[data-reg-next]').addEventListener('click', function () {
      collectTeamFormData();
      renderTeamStep3();
    });

    var addMemberBtn = els.body.querySelector('[data-reg-add-member]');
    if (addMemberBtn) {
      addMemberBtn.addEventListener('click', function () {
        if (teamData.members.length >= 4) { addMemberBtn.disabled = true; return; }
        teamData.members.push({ first_name: '', last_name: '', rusa_id: '' });
        collectTeamFormData();
        renderTeamStep2();
      });
    }

    els.body.querySelectorAll('input[name="proof_method"]').forEach(function (radio) {
      radio.addEventListener('change', function () {
        teamData.proof_method = radio.value;
        var rwgpsDiv = document.getElementById('rwgps-field');
        if (rwgpsDiv) {
          rwgpsDiv.innerHTML = teamData.proof_method === 'gps_track'
            ? '<label class="reg-form-field"><span>RWGPS route URL</span>' +
              '<input name="rwgps_url" type="url" placeholder="https://ridewithgps.com/routes/…" value="' + esc(teamData.rwgps_url) + '"></label>'
            : '';
        }
      });
    });
  }

  function collectTeamFormData() {
    var teamName = els.body.querySelector('input[name="team_name"]');
    if (teamName) teamData.team_name = teamName.value;
    var notes = els.body.querySelector('input[name="notes"]');
    if (notes) teamData.notes = notes.value;
    var rwgps = els.body.querySelector('input[name="rwgps_url"]');
    if (rwgps) teamData.rwgps_url = rwgps.value;
    teamData.members.forEach(function (m, i) {
      var f = els.body.querySelector('input[name="member_' + i + '_first"]');
      var l = els.body.querySelector('input[name="member_' + i + '_last"]');
      var r = els.body.querySelector('input[name="member_' + i + '_rusa"]');
      if (f) m.first_name = f.value;
      if (l) m.last_name = l.value;
      if (r) m.rusa_id = r.value;
    });
  }

  function renderTeamStep3() {
    setStep(3);
    var profile = profileData ? profileData.profile : {};
    var membersList = teamData.members.filter(function (m) {
      return m.first_name || m.last_name;
    }).map(function (m) {
      return '<li>' + esc(m.first_name) + ' ' + esc(m.last_name) + (m.rusa_id ? ' — RUSA ' + esc(m.rusa_id) : '') + '</li>';
    }).join('');
    els.body.innerHTML =
      '<div class="reg-summary card">' +
      '<p class="reg-section-title">Team registration summary</p>' +
      '<p><strong>' + esc(teamData.team_name || '(no name)') + '</strong></p>' +
      '<p>Event: <strong>' + eventData.event.name + '</strong> · ' + fmtDate(eventData.event.date) + '</p>' +
      '<p>Captain: ' + esc((profile.first_name || '') + ' ' + (profile.last_name || '')) + (profile.rusa_id ? ' (RUSA ' + esc(profile.rusa_id) + ')' : '') + '</p>' +
      (membersList ? '<ul>' + membersList + '</ul>' : '') +
      '<p>Proof: ' + (teamData.proof_method === 'gps_track' ? 'GPS track' : 'Brevet card') + (teamData.rwgps_url ? ' · <a href="' + esc(teamData.rwgps_url) + '" target="_blank">RWGPS ↗</a>' : '') + '</p>' +
      '</div>' +
      '<div class="reg-actions"><button type="button" class="reg-secondary-btn" data-reg-back>← Back</button>' +
      '<button type="button" class="reg-primary-btn" data-reg-confirm>Submit team registration</button></div>';
    els.body.querySelector('[data-reg-back]').addEventListener('click', function () { renderTeamStep2(); });
    els.body.querySelector('[data-reg-confirm]').addEventListener('click', confirmTeamRegistration);
  }

  function confirmTeamRegistration() {
    var btn = els.body.querySelector('[data-reg-confirm]');
    btn.disabled = true;
    var payload = {
      team_name: teamData.team_name,
      proof_method: teamData.proof_method,
      rwgps_url: teamData.rwgps_url || null,
      notes: teamData.notes || null,
      members: teamData.members.filter(function (m) { return m.first_name || m.last_name || m.rusa_id; }),
    };
    fetch('/calendar/' + eventId + '/register/team', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }).then(function (r) { return r.json().then(function (d) { return { ok: r.ok, data: d }; }); })
      .then(function (res) {
        if (!res.ok) {
          alert(res.data.error || 'Team registration failed');
          btn.disabled = false;
          return;
        }
        els.body.innerHTML =
          '<div class="reg-success"><div class="reg-success-icon">✓</div>' +
          '<h3>Team registered!</h3>' +
          '<p><strong>' + esc(teamData.team_name) + '</strong> is on the roster for ' +
          eventData.event.name + '.</p>' +
          (res.data.confirmation_code ? '<p>Code: <strong>' + res.data.confirmation_code + '</strong></p>' : '') +
          '</div>' +
          '<button type="button" class="reg-primary-btn" data-reg-done>Done</button>';
        els.body.querySelector('[data-reg-done]').addEventListener('click', closeModal);
        document.dispatchEvent(new CustomEvent('brevethub:registered', { detail: { eventId: eventId } }));
      });
  }

  function loadTeamStep2() {
    mode = 'team';
    fetch('/calendar/' + eventId + '/register/profile', { credentials: 'same-origin' })
      .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, data: d }; }); })
      .then(function (res) {
        if (!res.ok) {
          if (res.data && res.data.login_url) window.location = res.data.login_url;
          return;
        }
        profileData = res.data;
        renderTeamStep2();
      });
  }

  function openRegistration(id) {
    mode = 'single';
    eventId = id;
    modal.hidden = false;
    document.body.style.overflow = 'hidden';
    els.body.innerHTML = '<p class="text-text-light">Loading…</p>';
    fetch('/calendar/' + id + '/register/details')
      .then(function (r) { return r.json(); })
      .then(function (data) { eventData = data; renderStep1(); });
  }

  function openBulkRegistration(ids) {
    if (!ids.length) return;
    mode = 'bulk';
    eventIds = ids;
    bulkData = { events: ids.map(function (id) { return { id: id }; }) };
    modal.hidden = false;
    document.body.style.overflow = 'hidden';
    fetch('/register/bulk/preview', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ event_ids: ids }),
    }).then(function (r) { return r.json(); })
      .then(function (data) {
        bulkData = data;
        eventIds = (data.events || []).filter(function (ev) { return !ev.already_registered; }).map(function (ev) { return ev.id; });
        renderBulkStep1();
      });
  }

  document.querySelectorAll('[data-register-event]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      openRegistration(parseInt(btn.getAttribute('data-register-event'), 10));
    });
  });

  document.querySelectorAll('.bulk-event-select').forEach(function (cb) {
    cb.addEventListener('change', function () {
      syncBulkCheckbox(cb);
      updateBulkBar();
    });
  });

  var bulkOpen = document.querySelector('[data-bulk-open]');
  if (bulkOpen) {
    bulkOpen.addEventListener('click', function () { openBulkRegistration(selectedBulkIds()); });
  }
  var bulkClear = document.querySelector('[data-bulk-clear]');
  if (bulkClear) {
    bulkClear.addEventListener('click', function () {
      document.querySelectorAll('.bulk-event-select:checked').forEach(function (cb) { cb.checked = false; });
      updateBulkBar();
    });
  }

  els.close.addEventListener('click', closeModal);
  modal.addEventListener('click', function (e) { if (e.target === modal) closeModal(); });

  document.addEventListener('brevethub:registered', function (e) {
    var id = e.detail.eventId;
    var badge = document.querySelector('[data-reg-badge="' + id + '"]');
    if (badge) {
      badge.textContent = "You're registered";
      badge.hidden = false;
    } else {
      var section = document.querySelector('.event-card[data-event-id="' + id + '"] .signup-section');
      if (section) {
        var span = document.createElement('span');
        span.className = 'registration-badge';
        span.setAttribute('data-reg-badge', id);
        span.textContent = "You're registered";
        var actions = section.querySelector('.signup-actions');
        if (actions) section.insertBefore(span, actions);
        else section.appendChild(span);
      }
    }
    var status = document.querySelector('.signup-status[data-event-id="' + id + '"]');
    if (status) status.textContent = 'Going';
    var regBtn = document.querySelector('[data-register-event="' + id + '"]');
    if (regBtn) regBtn.remove();
    document.querySelectorAll('.bulk-event-select[value="' + id + '"]').forEach(function (cb) {
      cb.checked = false;
      cb.disabled = true;
    });
    updateBulkBar();
  });
})();
