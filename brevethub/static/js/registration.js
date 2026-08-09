/**
 * BrevetHub registration wizard — single + bulk + team, inline profile edit,
 * enhanced waiver (adult/minor, e-sig, SmartWaiver option).
 */
(function () {
  var SFR_WAIVER_TEXT = "I, IN CONSIDERATION of being permitted to participate in any way in the San Francisco Randonneurs\u2019 Designated Event, (\u201cActivity\u201d), I hereby acknowledge, agree, attest and represent the following:\n\n1. I FULLY UNDERSTAND that: (a) bicycle riding is dangerous and represents an extreme test of a person\u2019s physical and mental limits. I understand that participation involves risks and dangers which include, without limitation, the potential for serious bodily injury, permanent disability, paralysis, illness and death, including exposure to viral infections such as COVID-19; loss of, or damage, to equipment/property; exposure to extreme conditions and circumstances; contact or collision with other bicycle riders, people, vehicles, animals, or other natural or manmade objects; imperfect course conditions; road and surface hazards; inadequate safety measures; other riders of varying skill levels; situations beyond the immediate control of anyone; and other undefined risks and dangers which may not be readily foreseeable or are presently unknown (\u201cRisks\u201d); (b) I understand that these Risks may be caused in whole or in part by my own actions or inactions, the actions or inactions of others, or the acts, inaction or negligence of the Released Parties defined below, and (c) there may be other risks and social and economic losses, costs and damages to me, my family members and dependents either not known to me or not readily foreseeable at this time; and I FULLY ACCEPT AND ASSUME ALL SUCH RISKS AND ALL RESPONSIBILITY FOR ALL LOSSES, COSTS, AND DAMAGES I, my family members and dependents may incur as a result of my participating and riding in the Activity.\n\n2. I am qualified, in good health, and in proper physical condition to participate in the Activity. I agree and warrant that if, at any time, I believe conditions, including road hazards, to be unsafe or if I am not feeling well, I will immediately discontinue further riding of the Activity.\n\n3. TO THE FULLEST EXTENT PERMITTED BY LAW, I, ON BEHALF OF MYSELF, MY FAMILY MEMBERS AND DEPENDENTS HEREBY RELEASE, DISCHARGE, AND COVENANT NOT TO SUE Randonneurs USA (\u201cRUSA\u201d), the San Francisco Randonneurs, the Event Organizer, their respective administrators, directors, agents, officers, members, volunteers, other riders, and owners and lessors of premises on which the Activity takes place, (\u201cRELEASED PARTIES\u201d) FROM ALL LIABILITY, CLAIMS, DEMANDS, ACTIONS, LOSSES, COSTS OR DAMAGES (HEREAFTER, \u201cCLAIMS\u201d) CAUSED OR ALLEGED TO BE CAUSED IN WHOLE OR IN PART BY THE ACTS OR OMISSIONS, INCLUDING NEGLIGENCE, OF THE \u201cRELEASED PARTIES\u201d, INCLUDING, WITHOUT LIMITATION, RESCUE OPERATIONS. I further agree that if, I, or anyone on my behalf, makes a Claim against any of the Released Parties, I WILL INDEMNIFY, SAVE, AND HOLD HARMLESS EACH OF THE RELEASED PARTIES from any litigation expenses, attorney fees, losses, liability, damages, or costs which any Released Party may incur as the result of such Claim.\n\nThis agreement shall be construed broadly to provide a release and waiver to the maximum extent permissible under applicable law.\n\nI AM 18 YEARS OF AGE OR OLDER, HAVE READ AND UNDERSTAND THE TERMS OF THIS AGREEMENT, UNDERSTAND THAT I AM GIVING UP SUBSTANTIAL RIGHTS BY SIGNING THIS AGREEMENT, HAVE SIGNED IT VOLUNTARILY AND WITHOUT ANY INDUCEMENT OR ASSURANCE OF ANY NATURE AND INTEND IT TO BE A COMPLETE AND UNCONDITIONAL RELEASE OF ALL LIABILITY, I INTEND THAT THIS AGREEMENT ALSO SHALL BE BINDING UPON MY HEIRS, NEXT OF KIN, REPRESENTATIVES, SUCCESSORS AND ASSIGNS. I AGREE THAT IF ANY PORTION OF THIS AGREEMENT IS HELD TO BE INVALID, THE BALANCE, NOTWITHSTANDING, SHALL CONTINUE IN FULL FORCE AND EFFECT.\n\nI acknowledge and agree that the RELEASE AND WAIVER OF LIABILITY, ASSUMPTION OF RISK, AND INDEMNITY AGREEMENT may be executed and delivered by electronic means, and the electronic signature shall be considered an original signature for all purposes and shall have the same force and effect as an original signature. An electronic signature shall include an electronically scanned original signature or an electronically transmitted original signature (e.q. via pdf).";
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
    initials: '',
    waiver_date: '',
  };

  var teamData = {
    team_name: '',
    proof_method: 'brevet_card',
    rwgps_url: '',
    draft_route_accepted: false,
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
      initials: '',
      waiver_date: new Date().toISOString().slice(0, 10),
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

  function riderFullName() {
    var p = (profileData && profileData.profile) || {};
    return ((p.first_name || '') + ' ' + (p.last_name || '')).trim();
  }

  function signatoryNameMatches() {
    var expected = riderFullName().toLowerCase();
    var entered = (waiverData.signatory_name || '').trim().toLowerCase();
    return expected.length > 0 && entered === expected;
  }

  function isWaiverValid() {
    if (waiverData.method === 'smartwaiver') return waiverData.smartwaiver_completed;
    if (waiverData.is_minor) {
      return !!(waiverData.guardian_name && waiverData.guardian_phone && waiverData.esign_consented);
    }
    return !!(waiverData.age_certified
      && waiverData.signatory_name && waiverData.signatory_name.trim().length > 1
      && waiverData.initials && waiverData.initials.trim().length > 0
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
    teamData = { team_name: '', proof_method: 'brevet_card', rwgps_url: '', draft_route_accepted: false, notes: '',
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
      var today = new Date().toISOString().slice(0, 10);
      if (!waiverData.waiver_date) waiverData.waiver_date = today;
      var maxDate = (ev && ev.date) ? ev.date : '';
      html +=
        '<div style="margin-bottom:14px;">' +
        '<p style="font-weight:700;font-size:0.95rem;margin-bottom:6px;">RELEASE AND WAIVER OF LIABILITY, ASSUMPTION OF RISK, AND INDEMNITY AGREEMENT</p>' +
        '<p style="color:#b91c1c;font-weight:600;font-size:0.82rem;">WARNING: READ THIS AGREEMENT CAREFULLY. IT INCLUDES A RELEASE OF LIABILITY AND WAIVER OF LEGAL RIGHTS. IF YOU SIGN THIS AGREEMENT YOU ARE GIVING UP THE RIGHT TO SUE RANDONNEURS USA AND OTHER PARTIES.</p>' +
        '</div>' +
        '<div class="reg-waiver-minor-toggle">' +
        '<p class="reg-waiver-question"><strong>Please select who will be participating.</strong></p>' +
        '<label class="reg-radio-btn"><input type="radio" name="is_minor" value="adult"' +
        (!waiverData.is_minor ? ' checked' : '') + '> Adult (18+)</label>' +
        '<label class="reg-radio-btn"><input type="radio" name="is_minor" value="minor"' +
        (waiverData.is_minor ? ' checked' : '') + '> Minor (under 18)</label>' +
        '</div>' +
        ageCheck + guardianFields +
        // Red header + Initials field sit ABOVE the scrollable body
        '<p style="color:#b91c1c;font-weight:700;margin:10px 0 6px;">I understand that this agreement is a release of liability and waiver of legal rights, giving up the right to sue Randonneurs USA and other parties.</p>' +
        '<div style="display:flex;align-items:center;gap:12px;margin-bottom:8px;">' +
        '<label class="reg-form-field" style="margin:0"><span style="font-size:0.78rem;font-weight:600;text-transform:uppercase;letter-spacing:.04em;">Initial</span>' +
        '<input name="waiver_initials" type="text" maxlength="5" value="' + esc(waiverData.initials) + '" placeholder="e.g. JD" style="max-width:72px;text-transform:uppercase;font-size:1rem;font-weight:600;text-align:center;"></label>' +
        '</div>' +
        // Scrollable waiver body with Date and red footer inside
        '<div class="reg-waiver-box">' +
        waiverFormatted +
        '<p style="margin-top:12px;"><strong>Date:</strong> <input name="waiver_date" type="date" value="' + esc(waiverData.waiver_date) + '"' + (maxDate ? ' max="' + esc(maxDate) + '"' : '') + ' style="border:none;border-bottom:1px solid #999;background:transparent;font-size:0.82rem;padding:0 2px;"></p>' +
        '</div>' +
        '<p style="color:#b91c1c;font-weight:700;font-size:0.8rem;margin:6px 0 10px;">Do not sign this agreement unless you have read it in its entirety and understand the rights you are giving up.</p>' +
        '<div class="reg-esign-block">' +
        '<div class="reg-esign-notice">' +
        '<p><strong>Electronic Signature Consent</strong></p>' +
        '<p>By checking here, you are consenting to the use of your electronic signature in lieu of an original signature on paper. You have the right to request that you sign a paper copy instead. By checking here, you are waiving that right. After consent, you may, upon written request to us, obtain a paper copy of an electronic record. No fee will be charged for such copy and no special hardware or software is required to view it. Your agreement to use an electronic signature with us for any documents will continue until such time as you notify us in writing that you no longer wish to use an electronic signature. There is no penalty for withdrawing your consent. You should always make sure that we have a current email address in order to contact you regarding any changes, if necessary.</p>' +
        '</div>' +
        '<label class="reg-form-field"><span>Type your full legal name as your electronic signature</span>' +
        '<input name="signatory_name" type="text" value="' + esc(waiverData.signatory_name) + '" placeholder="' + esc(riderFullName()) + '" autocomplete="name"></label>' +
        '<p style="color:#6b7280;font-size:0.78rem;margin:-6px 0 8px;">Must match your registered name exactly: <strong>' + esc(riderFullName()) + '</strong></p>' +
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

    var initials = section.querySelector('input[name="waiver_initials"]');
    if (initials) initials.addEventListener('input', function () { waiverData.initials = initials.value.toUpperCase(); syncNextBtn(); });

    var wDate = section.querySelector('input[name="waiver_date"]');
    if (wDate) wDate.addEventListener('change', function () { waiverData.waiver_date = wDate.value; });

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
      renderWaiverSection((profileData.waiver && profileData.waiver.text) || SFR_WAIVER_TEXT, ev)
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
    els.body.querySelector('[data-reg-next]').addEventListener('click', function () {
      if (!signatoryNameMatches()) {
        var sigInput = els.body.querySelector('input[name="signatory_name"]');
        if (sigInput) {
          sigInput.style.borderColor = '#ef4444';
          sigInput.focus();
          var err = els.body.querySelector('.reg-sig-error');
          if (!err) {
            err = document.createElement('p');
            err.className = 'reg-sig-error';
            err.style.cssText = 'color:#ef4444;font-size:0.78rem;margin:4px 0 8px;';
            sigInput.parentNode.insertAdjacentElement('afterend', err);
          }
          err.textContent = 'Name must match exactly: ' + riderFullName();
        }
        return;
      }
      renderStep3();
    });
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
        '<button type="button" class="reg-secondary-btn" data-reg-next style="margin-top:6px">Individual sign-up →</button>'
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
      waiver_initials: waiverData.initials || null,
      waiver_signed_date: waiverData.waiver_date || null,
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
    document.dispatchEvent(new CustomEvent('brevethub:registered', {
      detail: { eventId: eventId, registrationStatus: data.registration_status || 'confirmed' }
    }));
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
      '<input name="member_' + idx + '_rusa" type="text" placeholder="RUSA #" value="' + esc(m.rusa_id) + '" class="reg-member-input reg-member-rusa" required>' +
      '</div>';
  }

  function renderTeamStep2() {
    setStep(2);
    var profile = profileData ? profileData.profile : {};
    var atCap = false; // no hard cap — warn instead
    var overFive = (1 + teamData.members.length) > 5; // captain + additional > 5
    var membersHtml = teamData.members.map(function (m, i) { return memberRow(i, m); }).join('');

    els.body.innerHTML =
      '<p class="reg-section-title">Team details</p>' +
      '<div class="reg-team-form">' +
      '<label class="reg-form-field"><span>Team name</span>' +
      '<input name="team_name" type="text" value="' + esc(teamData.team_name) + '" placeholder="e.g. Bay Randonneurs Express" required></label>' +

      '<div class="reg-captain-info">' +
      '<p class="reg-section-label">Captain (you)</p>' +
      '<div style="display:flex;gap:16px;flex-wrap:wrap;font-size:0.85rem;">' +
      '<span><strong>Name:</strong> ' + esc((profile.first_name || '') + ' ' + (profile.last_name || '')) + '</span>' +
      '<span><strong>Email:</strong> ' + esc(profile.email || '—') + '</span>' +
      '<span><strong>RUSA #:</strong> ' + esc(profile.rusa_id || '—') + '</span>' +
      '</div></div>' +

      '<p class="reg-section-label">Team members (2–4 additional riders) — RUSA # required for all</p>' +
      '<div class="reg-team-members">' + membersHtml + '</div>' +
      '<div id="reg-more-than-five" style="' + (overFive ? '' : 'display:none;') + 'margin:10px 0 8px;padding:10px 12px;background:#fef9c3;border:1px solid #fde047;border-radius:8px;font-size:0.8rem;">' +
        '<p style="margin:0;font-weight:600;">⚠ More than 5 team members requires tandems.</p>' +
      '</div>' +
      '<button type="button" class="reg-secondary-btn" data-reg-add-member style="margin:8px 0 14px">+ Add another member</button>' +

      '<p class="reg-section-label" style="margin-top:14px;">RidewithGPS route link</p>' +
      '<label class="reg-form-field" style="margin-bottom:4px;">' +
      '<input name="rwgps_url" type="url" placeholder="https://ridewithgps.com/routes/…" value="' + esc(teamData.rwgps_url) + '"></label>' +
      '<p style="font-size:0.78rem;color:#6b7280;margin-bottom:14px;">Draft routes are due no later than 10 days before the event. Final routes must be received no later than 7 days before the event. Mark penultimate control location (example: 22 hour control for Fleche, 11.5 hour control for DART, etc.). Send your ridewithgps link to <a href="mailto:fleche@sfrandonneurs.org">fleche@sfrandonneurs.org</a>.</p>' +

      '<p class="reg-section-label">Proof of passage</p>' +
      '<p style="font-size:0.8rem;color:#4b5563;margin-bottom:8px;">Teams must use all of one form of proof of passage, or all of the other form. If your team uses brevet cards, then your route must include <em>all</em> needed controls and <em>final</em> routes will be due no less than 10 days before the event.</p>' +
      '<div class="reg-proof-toggle">' +
      '<label class="reg-radio-btn"><input type="radio" name="proof_method" value="brevet_card"' +
      (teamData.proof_method !== 'gps_track' ? ' checked' : '') + '> Brevet card</label>' +
      '<label class="reg-radio-btn"><input type="radio" name="proof_method" value="gps_track"' +
      (teamData.proof_method === 'gps_track' ? ' checked' : '') + '> GPS track (RWGPS)</label>' +
      '</div>' +

      '<label class="reg-waiver-check" style="margin:12px 0 14px;">' +
      '<input type="checkbox" name="draft_route_accepted"' + (teamData.draft_route_accepted ? ' checked' : '') + '> ' +
      'I understand that my draft route is subject to review and approval by the organizer before the event.</label>' +

      '<label class="reg-form-field"><span>Notes for organizer (optional)</span>' +
      '<input name="notes" type="text" value="' + esc(teamData.notes) + '" placeholder="Extra team members, special notes…"></label>' +
      '</div>' +
      '<div id="reg-team-error" style="color:#ef4444;font-size:0.82rem;display:none;margin-bottom:8px;"></div>' +
      '<p style="font-size:0.82rem;color:#4b5563;margin-bottom:10px;">Once your team composition has settled, send <strong>$5.00 per team</strong> via PayPal to <a href="mailto:treasurer@sfrandonneurs.org">treasurer@sfrandonneurs.org</a> — include the team name as a note.</p>' +
      '<div class="reg-actions"><button type="button" class="reg-secondary-btn" data-reg-back>← Back</button>' +
      '<button type="button" class="reg-primary-btn" data-reg-next>Review & Confirm →</button></div>';

    els.body.querySelector('[data-reg-back]').addEventListener('click', renderStep1);
    els.body.querySelector('[data-reg-next]').addEventListener('click', function () {
      collectTeamFormData();
      var errDiv = document.getElementById('reg-team-error');

      // Completeness: RUSA # required for all named members
      var missingRusa = teamData.members.filter(function (m) {
        return (m.first_name || m.last_name) && !m.rusa_id;
      });
      if (missingRusa.length) {
        if (errDiv) { errDiv.textContent = 'RUSA # is required for all team members.'; errDiv.style.display = 'block'; }
        return;
      }

      // RUSA ID + name validation via API
      var toValidate = teamData.members
        .filter(function (m) { return m.first_name && m.last_name && m.rusa_id; })
        .map(function (m) { return { rusa_id: m.rusa_id, first_name: m.first_name, last_name: m.last_name }; });

      if (!toValidate.length) { renderTeamStep3(); return; }

      var nextBtn = els.body.querySelector('[data-reg-next]');
      if (nextBtn) { nextBtn.disabled = true; nextBtn.textContent = 'Validating RUSA IDs…'; }
      if (errDiv) errDiv.style.display = 'none';

      fetch('/rusa/validate-batch', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ members: toValidate })
      }).then(function (r) { return r.json(); }).then(function (data) {
        var failures = (data.results || []).filter(function (r) { return !r.ok; });
        if (failures.length) {
          var msgs = failures.map(function (r) { return 'RUSA ' + r.rusa_id + ': ' + (r.error || 'name/ID mismatch'); }).join(' · ');
          if (errDiv) { errDiv.textContent = msgs; errDiv.style.display = 'block'; }
          if (nextBtn) { nextBtn.disabled = false; nextBtn.textContent = 'Review & Confirm →'; }
        } else {
          renderTeamStep3();
        }
      }).catch(function () {
        // Network error — proceed and let server re-validate
        if (nextBtn) { nextBtn.disabled = false; nextBtn.textContent = 'Review & Confirm →'; }
        renderTeamStep3();
      });
    });

    var addMemberBtn = els.body.querySelector('[data-reg-add-member]');
    if (addMemberBtn) {
      addMemberBtn.addEventListener('click', function () {
        collectTeamFormData();
        teamData.members.push({ first_name: '', last_name: '', rusa_id: '' });
        renderTeamStep2();
      });
    }

    var draftCheck = els.body.querySelector('input[name="draft_route_accepted"]');
    if (draftCheck) draftCheck.addEventListener('change', function () { teamData.draft_route_accepted = draftCheck.checked; });
  }

  function collectTeamFormData() {
    var teamName = els.body.querySelector('input[name="team_name"]');
    if (teamName) teamData.team_name = teamName.value;
    var notes = els.body.querySelector('input[name="notes"]');
    if (notes) teamData.notes = notes.value;
    var rwgps = els.body.querySelector('input[name="rwgps_url"]');
    if (rwgps) teamData.rwgps_url = rwgps.value;
    var proofRadio = els.body.querySelector('input[name="proof_method"]:checked');
    if (proofRadio) teamData.proof_method = proofRadio.value;
    var draftCheck = els.body.querySelector('input[name="draft_route_accepted"]');
    if (draftCheck) teamData.draft_route_accepted = draftCheck.checked;
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
      needs_special_review: (1 + teamData.members.length) > 5,
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
    var regStatus = e.detail.registrationStatus || 'confirmed';
    var badge = document.querySelector('[data-reg-badge="' + id + '"]');
    var badgeText = regStatus === 'confirmed' ? "You're registered"
      : regStatus === 'exception' ? 'Registered · review'
      : regStatus === 'waitlist' ? 'Waitlist'
      : regStatus.charAt(0).toUpperCase() + regStatus.slice(1);
    if (badge) {
      badge.textContent = badgeText;
      badge.hidden = false;
    } else {
      var section = document.querySelector('.event-card[data-event-id="' + id + '"] .signup-section');
      if (section) {
        var span = document.createElement('span');
        span.className = 'registration-badge';
        span.setAttribute('data-reg-badge', id);
        span.textContent = badgeText;
        var actions = section.querySelector('.signup-actions');
        if (actions) section.insertBefore(span, actions);
        else section.appendChild(span);
      }
    }
    var regBtn = document.querySelector('[data-register-event="' + id + '"]');
    if (regBtn) regBtn.remove();
    var actions = document.querySelector('.signup-actions[data-event-id="' + id + '"]');
    if (actions) {
      actions.setAttribute('data-registered', '1');
      var proofUrl = actions.getAttribute('data-proof-url');
      var html = proofUrl ? '<a class="event-link-btn" href="' + proofUrl + '">Submit proof</a>' : '';
      html += '<button type="button" class="signup-intent-btn signup-intent-withdraw" data-action="withdraw" data-event-id="' + id + '">Request withdraw</button>';
      actions.innerHTML = html;
    }
    document.querySelectorAll('.bulk-event-select[value="' + id + '"]').forEach(function (cb) {
      cb.checked = false;
      cb.disabled = true;
    });
    updateBulkBar();
  });
})();
