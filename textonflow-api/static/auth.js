// ── Auth modal ────────────────────────────────────────────────────────────────
var _authTab = 'login';
var _TOF_TOKEN_KEY = 'tof_token';

function openAuthModal(tab, ctxMsg) {
  _authTab = tab || 'login';
  document.getElementById('auth-modal').style.display = 'flex';
  authSwitchTab(_authTab);
  var ctx = document.getElementById('auth-ctx-msg');
  if (ctx) { if (ctxMsg) { ctx.textContent = ctxMsg; ctx.style.display = 'block'; } else { ctx.style.display = 'none'; } }
  setTimeout(function(){ document.getElementById('auth-email').focus(); }, 100);
}
function closeAuthModal() {
  document.getElementById('auth-modal').style.display = 'none';
  document.getElementById('auth-error').style.display = 'none';
  document.getElementById('auth-success').style.display = 'none';
  document.getElementById('auth-email').value = '';
  document.getElementById('auth-password').value = '';
  var ctx = document.getElementById('auth-ctx-msg');
  if (ctx) ctx.style.display = 'none';
}
function authSwitchTab(tab) {
  _authTab = tab;
  var tl = document.getElementById('auth-tab-login');
  var tr = document.getElementById('auth-tab-register');
  var sb = document.getElementById('auth-submit-btn');
  var lf = document.getElementById('auth-login-footer');
  var rf = document.getElementById('auth-register-footer');
  var fp = document.getElementById('auth-forgot-panel');
  var mainFields = document.querySelector('#auth-modal .modal-main-fields');
  // Mostrar/ocultar panel de recuperación
  var isForgot = (tab === 'forgot');
  if (fp) fp.style.display = isForgot ? 'block' : 'none';
  // Ocultar campos principales en modo forgot
  var emailInput = document.getElementById('auth-email');
  var pwInput = document.getElementById('auth-password');
  var submitBtn = document.getElementById('auth-submit-btn');
  if (emailInput) emailInput.style.display = isForgot ? 'none' : '';
  if (pwInput) pwInput.style.display = isForgot ? 'none' : '';
  if (submitBtn) submitBtn.style.display = isForgot ? 'none' : '';
  if (isForgot) {
    tl.style.background = '#1a1a2e'; tl.style.color = '#8888aa';
    tr.style.background = '#1a1a2e'; tr.style.color = '#8888aa';
    lf.style.display = 'none'; rf.style.display = 'none';
    setTimeout(function(){ var fe=document.getElementById('auth-forgot-email'); if(fe) fe.focus(); }, 100);
  } else if (tab === 'login') {
    tl.style.background = '#252545'; tl.style.color = '#d0d0e8';
    tr.style.background = '#1a1a2e'; tr.style.color = '#8888aa';
    if(sb) sb.textContent = 'Iniciar sesión'; lf.style.display='block'; rf.style.display='none';
  } else {
    tr.style.background = '#252545'; tr.style.color = '#d0d0e8';
    tl.style.background = '#1a1a2e'; tl.style.color = '#8888aa';
    if(sb) sb.textContent = 'Crear cuenta gratuita'; lf.style.display='none'; rf.style.display='block';
  }
  document.getElementById('auth-error').style.display = 'none';
  document.getElementById('auth-success').style.display = 'none';
}
async function authForgotSubmit() {
  var email = document.getElementById('auth-forgot-email').value.trim();
  var btn = document.getElementById('auth-forgot-btn');
  if (!email) { _authShowErr('Ingresa tu email.'); return; }
  btn.disabled = true; btn.textContent = 'Enviando...';
  try {
    await fetch('/user/forgot-password', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({email: email})
    });
    _authShowOk('Si ese email existe, te enviamos un enlace de recuperación.');
    document.getElementById('auth-forgot-email').value = '';
  } catch(e) {
    _authShowErr('Error de conexión.');
  } finally {
    btn.disabled = false; btn.textContent = 'Enviar enlace';
  }
}
function _authShowErr(msg) {
  var el = document.getElementById('auth-error');
  el.textContent = msg; el.style.display = 'block';
  document.getElementById('auth-success').style.display = 'none';
}
function _authShowOk(msg) {
  var el = document.getElementById('auth-success');
  el.textContent = msg; el.style.display = 'block';
  document.getElementById('auth-error').style.display = 'none';
}
async function authSubmit() {
  var email = document.getElementById('auth-email').value.trim();
  var password = document.getElementById('auth-password').value;
  var btn = document.getElementById('auth-submit-btn');
  if (!email || !password) { _authShowErr('Completa todos los campos.'); return; }
  btn.disabled = true; btn.textContent = 'Procesando...';
  var endpoint = _authTab === 'login' ? '/user/login' : '/user/register';
  try {
    var res = await fetch(endpoint, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email: email, password: password })
    });
    var data = await res.json();
    if (!res.ok) { _authShowErr(data.detail || 'Error al procesar.'); return; }
    // Guardar token
    localStorage.setItem(_TOF_TOKEN_KEY, data.token);
    var plan = (data.user && data.user.plan) || 'trial';
    var planLabel = { trial: 'Prueba', starter: 'Starter', agency: 'Agency', admin: 'Admin' };
    if (_authTab === 'register') {
      _authShowOk('¡Cuenta creada! Trial activo — 7 días de renders ilimitados.');
    } else {
      _authShowOk('¡Sesión iniciada!');
    }
    var _wasRegister = (_authTab === 'register');
    _authUpdateBtn(email, plan);
    setTimeout(function(){
      closeAuthModal();
      // Onboarding: siempre al registrar; al iniciar sesión si no lo ocultó
      if (typeof showOnboarding === 'function' && !localStorage.getItem('tof_ob_hidden')) {
        setTimeout(showOnboarding, 400);
      }
      // Ejecutar acción pendiente (ej: copiar JSON)
      if (window._tof_pending) {
        var pending = window._tof_pending;
        window._tof_pending = null;
        setTimeout(function(){
          if (pending === 'copy_json' && typeof copyJSON === 'function') copyJSON();
          else if (pending === 'copy_result_json' && typeof resultCopyJson === 'function') resultCopyJson();
          else if (pending === 'generate' && typeof generateImage === 'function') generateImage();
        }, 300);
      }
    }, 1500);
  } catch(e) {
    _authShowErr('Error de conexión. Intenta de nuevo.');
  } finally {
    btn.disabled = false;
    btn.textContent = _authTab === 'login' ? 'Iniciar sesión' : 'Crear cuenta gratuita';
  }
}
function _authUpdateBtn(email, plan) {
  var btn = document.getElementById('auth-btn');
  var lbl = document.getElementById('auth-btn-label');
  if (email) {
    var short = email.split('@')[0].substring(0, 12);
    lbl.textContent = short;
    btn.style.borderColor = 'rgba(34,197,94,.4)';
    btn.style.color = '#4ade80';
    btn.title = 'Mi cuenta';
  } else {
    lbl.textContent = 'Iniciar sesión';
    btn.style.borderColor = 'rgba(124,110,255,.4)';
    btn.style.color = '#c4b5fd';
    btn.title = '';
  }
}
function handleAuthBtn() {
  var tok = localStorage.getItem(_TOF_TOKEN_KEY) || sessionStorage.getItem(_TOF_TOKEN_KEY);
  if (tok) { window.location.href = '/dashboard'; }
  else { openAuthModal('login'); }
}
// Cerrar modal con Escape
document.addEventListener('keydown', function(e){
  if (e.key === 'Escape') closeAuthModal();
});
// Cerrar al hacer clic en el fondo
document.getElementById('auth-modal').addEventListener('click', function(e){
  if (e.target === this) closeAuthModal();
});
// ── Sesión: si el usuario sale de TextOnFlow 30 min → cierra sesión ───────────
// Sale = empieza el reloj. Regresa = cancela y reinicia.
(function _awayTimer(){
  var TIMEOUT = 30 * 60 * 1000; // 30 min
  var _timer  = null;

  function _logout(){
    localStorage.removeItem('tof_token');
    sessionStorage.removeItem('tof_token');
    var lbl = document.getElementById('auth-btn-label');
    if (lbl) lbl.textContent = 'Iniciar sesión';
    var note = document.createElement('div');
    note.textContent = 'Sesión cerrada — estuviste 30 min fuera de TextOnFlow.';
    note.style.cssText = 'position:fixed;top:16px;left:50%;transform:translateX(-50%);background:#13132a;border:1px solid #7c6eff;color:#a5b4fc;padding:11px 20px;border-radius:10px;font-size:13px;z-index:999999;box-shadow:0 8px 32px rgba(0,0,0,.5);pointer-events:none;';
    document.body.appendChild(note);
    setTimeout(function(){ note.remove(); openAuthModal('login'); }, 2200);
  }

  document.addEventListener('visibilitychange', function(){
    var tok = localStorage.getItem('tof_token') || sessionStorage.getItem('tof_token');
    if (document.hidden) {
      // Usuario sale → arranca el reloj
      if (tok) _timer = setTimeout(_logout, TIMEOUT);
    } else {
      // Usuario regresa → cancela el reloj
      clearTimeout(_timer);
      _timer = null;
    }
  });
})();
// Cargar estado en inicio
(async function _authInit(){
  var tok = localStorage.getItem(_TOF_TOKEN_KEY) || sessionStorage.getItem(_TOF_TOKEN_KEY);
  if (!tok) return;
  try {
    var res = await fetch('/user/me', { headers: { Authorization: 'Bearer ' + tok } });
    if (res.ok) {
      var data = await res.json();
      window._tofUserProfile = data;
      _authUpdateBtn(data.email, data.plan);
    } else {
      localStorage.removeItem(_TOF_TOKEN_KEY);
    }
  } catch(e) { /* silently skip */ }
})();
// Exponer para 429 handler
window.openAuthModal = openAuthModal;
