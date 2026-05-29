// ─── Mesas de trabajo / Pestañas (Artboards) ──────────────────────────────────
// Permite tener varios diseños dentro del mismo archivo, cambiar entre ellos sin
// perder nada, cada uno con su propio JSON. Aislado: no modifica app.js.
(function(){
  'use strict';

  var LS_KEY = 'tof_artboards';
  var FILE_VERSION = 'tof-artboards-1';

  function _copy(o){ try{ return JSON.parse(JSON.stringify(o||null)); }catch(e){ return null; } }
  function _uid(){ return 'b_' + Date.now().toString(36) + '_' + Math.random().toString(36).slice(2,7); }

  // Estado del sistema de pestañas
  window._tofBoards = window._tofBoards || [];
  window._tofActiveBoard = window._tofActiveBoard || 0;

  // ── Captura el estado vivo del editor en un objeto de diseño ─────────────────
  // Usa el puente oficial de app.js (estado léxico real). Fallback al input de URL.
  function snapLive(){
    try{
      if(typeof window.tofGetEditorState === 'function'){
        var st = window.tofGetEditorState();
        return {
          texts: _copy(st.texts) || [],
          imageOverlays: _copy(st.imageOverlays) || [],
          shapes: _copy(st.shapes) || [],
          imgUrl: st.imgUrl || null
        };
      }
    }catch(e){}
    // Fallback mínimo (no debería ocurrir)
    var imgUrl = null;
    try{ var ui = document.getElementById('image-url-input'); if(ui && ui.value) imgUrl = ui.value.trim(); }catch(e2){}
    return { texts:[], imageOverlays:[], shapes:[], imgUrl: imgUrl || null };
  }

  // ── Limpia el lienzo (estado sin imagen) ─────────────────────────────────────
  function clearCanvas(){
    try{
      var c = document.getElementById('preview-canvas');
      if(c){ var x = c.getContext('2d'); if(x) x.clearRect(0,0,c.width,c.height); }
      // Resetear el estado léxico real (imageData/loadedImg) vía puente de app.js
      if(typeof window.tofResetImageState === 'function') window.tofResetImageState();
      var uz = document.getElementById('upload-zone');
      var cw = document.getElementById('canvas-wrapper');
      if(uz) uz.classList.remove('has-image');
      if(cw) cw.classList.remove('active');
      var ui = document.getElementById('image-url-input');
      if(ui) ui.value = '';
    }catch(e){}
  }

  function _refreshPanels(){
    try{ if(typeof updateControls==='function') updateControls(); }catch(e){}
    try{ if(typeof updatePreview==='function') updatePreview(); }catch(e){}
    try{ if(typeof updateJSON==='function') updateJSON(); }catch(e){}
    try{ if(typeof renderStickerThumbnails==='function') renderStickerThumbnails(); }catch(e){}
    try{ if(typeof renderShapesOnCanvas==='function') renderShapesOnCanvas(); }catch(e){}
    try{ if(typeof renderFormasPanel==='function') renderFormasPanel(); }catch(e){}
  }

  // ── Restaura un diseño en el editor (mismo patrón que openProject) ───────────
  function restoreDesign(d){
    if(!d) d = { texts:[], imageOverlays:[], shapes:[], imgUrl:null };
    // Setear los arrays ANTES de cargar la imagen, para que updatePreview los pinte.
    // Usa el puente oficial (estado léxico real de app.js).
    if(typeof window.tofApplyEditorArrays === 'function'){
      window.tofApplyEditorArrays({
        texts: _copy(d.texts) || [],
        imageOverlays: _copy(d.imageOverlays) || [],
        shapes: _copy(d.shapes) || []
      });
    }

    var imgUrl = d.imgUrl || null;
    if(imgUrl){
      var urlInput = document.getElementById('image-url-input');
      if(urlInput) urlInput.value = imgUrl;
      if(typeof loadImageFromURL === 'function'){
        loadImageFromURL(); // async: applyImageToCanvas pinta textos/overlays/shapes + updateJSON
      } else {
        if(window.imageData) window.imageData.filename = imgUrl;
        _refreshPanels();
      }
    } else {
      clearCanvas();
      _refreshPanels();
    }
  }

  // ── Persistencia local (sobrevive recargas) ──────────────────────────────────
  function persist(){
    try{
      if(window._tofBoards[window._tofActiveBoard]){
        window._tofBoards[window._tofActiveBoard].data = snapLive();
      }
      localStorage.setItem(LS_KEY, JSON.stringify({
        boards: window._tofBoards,
        active: window._tofActiveBoard
      }));
    }catch(e){}
  }

  function loadStored(){
    try{
      var raw = localStorage.getItem(LS_KEY);
      if(!raw) return null;
      var obj = JSON.parse(raw);
      if(obj && Array.isArray(obj.boards) && obj.boards.length){
        return obj;
      }
    }catch(e){}
    return null;
  }

  // ── Operaciones de pestañas ──────────────────────────────────────────────────
  function switchBoard(i){
    if(i === window._tofActiveBoard) return;
    if(i < 0 || i >= window._tofBoards.length) return;
    // Guardar lo que hay en la pestaña activa
    if(window._tofBoards[window._tofActiveBoard]){
      window._tofBoards[window._tofActiveBoard].data = snapLive();
    }
    window._tofActiveBoard = i;
    restoreDesign(window._tofBoards[i].data);
    renderTabs();
    persist();
  }

  function _nextName(){
    var n = window._tofBoards.length + 1;
    return 'Diseño ' + n;
  }

  function addBoard(){
    // Guardar pestaña actual
    if(window._tofBoards[window._tofActiveBoard]){
      window._tofBoards[window._tofActiveBoard].data = snapLive();
    }
    var b = { id:_uid(), name:_nextName(), data:{ texts:[], imageOverlays:[], shapes:[], imgUrl:null } };
    window._tofBoards.push(b);
    window._tofActiveBoard = window._tofBoards.length - 1;
    restoreDesign(b.data);
    renderTabs();
    persist();
    _toast('➕ Pestaña nueva creada');
  }

  function duplicateBoard(){
    if(window._tofBoards[window._tofActiveBoard]){
      window._tofBoards[window._tofActiveBoard].data = snapLive();
    }
    var src = window._tofBoards[window._tofActiveBoard];
    var copy = { id:_uid(), name:(src.name||'Diseño') + ' (copia)', data:_copy(src.data) };
    window._tofBoards.splice(window._tofActiveBoard + 1, 0, copy);
    window._tofActiveBoard = window._tofActiveBoard + 1;
    restoreDesign(copy.data);
    renderTabs();
    persist();
    _toast('⧉ Pestaña duplicada');
  }

  function deleteBoard(i){
    if(window._tofBoards.length <= 1){
      _toast('⚠️ Debe quedar al menos una pestaña', 'warning');
      return;
    }
    var name = (window._tofBoards[i] && window._tofBoards[i].name) || 'esta pestaña';
    _confirm({
      title: 'Eliminar pestaña',
      message: '¿Eliminar "' + name + '"? Esta acción no se puede deshacer.',
      okText: 'Eliminar', cancelText: 'Cancelar', danger: true
    }).then(function(ok){ if(ok) _doDeleteBoard(i); });
  }

  function _doDeleteBoard(i){
    window._tofBoards.splice(i, 1);
    if(window._tofActiveBoard >= window._tofBoards.length){
      window._tofActiveBoard = window._tofBoards.length - 1;
    } else if(i < window._tofActiveBoard){
      window._tofActiveBoard--;
    } else if(i === window._tofActiveBoard){
      // se borró la activa: cargar la que quedó en su lugar
    }
    restoreDesign(window._tofBoards[window._tofActiveBoard].data);
    renderTabs();
    persist();
  }

  function renameBoard(i){
    var b = window._tofBoards[i];
    if(!b) return;
    _prompt({
      title: 'Renombrar pestaña',
      message: 'Escribe un nombre para esta pestaña:',
      inputValue: b.name || '', placeholder: 'Ej. Lunes',
      okText: 'Guardar', cancelText: 'Cancelar'
    }).then(function(nuevo){
      if(nuevo === null) return;
      nuevo = nuevo.trim();
      if(!nuevo) return;
      b.name = nuevo.slice(0, 40);
      renderTabs();
      persist();
    });
  }

  // ── Guardar / Abrir como archivo ─────────────────────────────────────────────
  function exportFile(){
    if(window._tofBoards[window._tofActiveBoard]){
      window._tofBoards[window._tofActiveBoard].data = snapLive();
    }
    var payload = { __tof: FILE_VERSION, savedAt:new Date().toISOString(), active:window._tofActiveBoard, boards:window._tofBoards };
    var blob = new Blob([JSON.stringify(payload, null, 2)], { type:'application/json' });
    var url = URL.createObjectURL(blob);
    var a = document.createElement('a');
    var d = new Date();
    var stamp = d.getFullYear() + ('0'+(d.getMonth()+1)).slice(-2) + ('0'+d.getDate()).slice(-2);
    a.href = url;
    a.download = 'textonflow-disenos-' + stamp + '.json';
    document.body.appendChild(a); a.click();
    setTimeout(function(){ document.body.removeChild(a); URL.revokeObjectURL(url); }, 200);
    _toast('💾 Archivo descargado (' + window._tofBoards.length + ' pestañas)');
  }

  function importFile(){
    var inp = document.getElementById('tof-artboard-file');
    if(inp) inp.click();
  }

  function _onFilePicked(ev){
    var file = ev.target && ev.target.files && ev.target.files[0];
    if(!file) return;
    var reader = new FileReader();
    reader.onload = function(e){
      try{
        var obj = JSON.parse(e.target.result);
        if(!obj || !Array.isArray(obj.boards) || !obj.boards.length){
          throw new Error('formato');
        }
        _confirm({
          title: 'Abrir archivo',
          message: 'Abrir este archivo reemplazará las pestañas actuales (' + window._tofBoards.length + '). ¿Continuar?',
          okText: 'Abrir', cancelText: 'Cancelar', danger: true
        }).then(function(ok){
          ev.target.value = '';
          if(!ok) return;
          window._tofBoards = obj.boards.map(function(b){
            return { id:b.id || _uid(), name:b.name || 'Diseño', data:b.data || { texts:[], imageOverlays:[], shapes:[], imgUrl:null } };
          });
          window._tofActiveBoard = (typeof obj.active === 'number' && obj.active < window._tofBoards.length) ? obj.active : 0;
          restoreDesign(window._tofBoards[window._tofActiveBoard].data);
          renderTabs();
          persist();
          _toast('📂 Archivo abierto (' + window._tofBoards.length + ' pestañas)');
        });
      }catch(err){
        ev.target.value = '';
        _toast('❌ Archivo no válido', 'error');
      }
    };
    reader.readAsText(file);
  }

  function _toast(msg, type){
    try{ if(typeof showToast === 'function'){ showToast(msg, type || 'success'); return; } }catch(e){}
    try{ if(typeof showNotif === 'function'){ showNotif(msg, type || 'success'); return; } }catch(e){}
  }

  // ── Modales propios (reemplazan confirm()/prompt() nativos del navegador) ─────
  function _esc(s){ return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }

  // Modal genérico. opts: {title, message, okText, cancelText, danger, withInput, inputValue, placeholder}
  // Resuelve a: false (cancelar), true (aceptar sin input), o el string del input.
  function _modal(opts){
    opts = opts || {};
    return new Promise(function(resolve){
      var ov = document.createElement('div');
      ov.className = 'tof-modal-ov';
      var inputHtml = opts.withInput
        ? '<input id="tof-modal-input" class="tof-modal-input" type="text" maxlength="40" value="' + _esc(opts.inputValue||'') + '" placeholder="' + _esc(opts.placeholder||'') + '">'
        : '';
      ov.innerHTML =
        '<div class="tof-modal" role="dialog" aria-modal="true">' +
          '<div class="tof-modal-title">' + _esc(opts.title||'') + '</div>' +
          (opts.message ? '<div class="tof-modal-msg">' + _esc(opts.message) + '</div>' : '') +
          inputHtml +
          '<div class="tof-modal-actions">' +
            '<button class="tof-modal-btn tof-modal-cancel" type="button">' + _esc(opts.cancelText||'Cancelar') + '</button>' +
            '<button class="tof-modal-btn ' + (opts.danger?'tof-modal-danger':'tof-modal-ok') + '" type="button">' + _esc(opts.okText||'Aceptar') + '</button>' +
          '</div>' +
        '</div>';
      document.body.appendChild(ov);
      var inp = ov.querySelector('#tof-modal-input');
      var btnCancel = ov.querySelector('.tof-modal-cancel');
      var btnOk = ov.querySelector('.tof-modal-cancel') && ov.querySelector('.tof-modal-actions').lastElementChild;
      function cleanup(){ document.removeEventListener('keydown', onKey, true); if(ov.parentNode) ov.parentNode.removeChild(ov); }
      function done(val){ cleanup(); resolve(val); }
      function accept(){ if(opts.withInput){ var v=(inp.value||'').trim(); done(v); } else { done(true); } }
      function cancel(){ done(false); }
      function onKey(e){
        if(e.key==='Escape'){ e.preventDefault(); e.stopPropagation(); cancel(); }
        else if(e.key==='Enter' && (opts.withInput || document.activeElement!==btnCancel)){ e.preventDefault(); e.stopPropagation(); accept(); }
      }
      btnCancel.addEventListener('click', cancel);
      btnOk.addEventListener('click', accept);
      ov.addEventListener('mousedown', function(e){ if(e.target===ov) cancel(); });
      document.addEventListener('keydown', onKey, true);
      requestAnimationFrame(function(){
        ov.classList.add('tof-modal-show');
        if(inp){ inp.focus(); inp.select(); } else { btnOk.focus(); }
      });
    });
  }
  function _confirm(opts){ opts = opts||{}; opts.withInput=false; return _modal(opts).then(function(r){ return r===true; }); }
  function _prompt(opts){ opts = opts||{}; opts.withInput=true; return _modal(opts).then(function(r){ return (r===false)?null:r; }); }

  // ── Render de la barra de pestañas ───────────────────────────────────────────
  function renderTabs(){
    var bar = document.getElementById('tof-artboard-bar');
    if(!bar) return;
    var tabs = window._tofBoards.map(function(b, i){
      var active = (i === window._tofActiveBoard);
      var nm = String(b.name || ('Diseño ' + (i+1)))
        .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
      return '' +
        '<div class="tof-tab' + (active ? ' tof-tab-active' : '') + '" ' +
             'onclick="tofSwitchBoard(' + i + ')" ' +
             'ondblclick="tofRenameBoard(' + i + ')" ' +
             'title="Clic: abrir · Doble clic: renombrar">' +
          '<span class="tof-tab-name">' + nm + '</span>' +
          '<span class="tof-tab-x" onclick="event.stopPropagation();tofDeleteBoard(' + i + ')" title="Eliminar pestaña">&times;</span>' +
        '</div>';
    }).join('');

    bar.innerHTML =
      '<div class="tof-tabs-scroll">' + tabs + '</div>' +
      '<div class="tof-tabs-actions">' +
        '<button class="tof-tab-btn" onclick="tofAddBoard()" title="Nueva pestaña en blanco">+ Nuevo</button>' +
        '<button class="tof-tab-btn" onclick="tofDuplicateBoard()" title="Duplicar la pestaña actual">⧉ Duplicar</button>' +
        '<span class="tof-tab-sep"></span>' +
        '<button class="tof-tab-btn" onclick="tofExportFile()" title="Guardar todas las pestañas como archivo">💾 Guardar archivo</button>' +
        '<button class="tof-tab-btn" onclick="tofImportFile()" title="Abrir un archivo de pestañas">📂 Abrir archivo</button>' +
      '</div>';
  }

  function injectStyles(){
    if(document.getElementById('tof-artboard-styles')) return;
    var css = '' +
      '#tof-artboard-bar{display:flex;align-items:center;gap:10px;width:100%;box-sizing:border-box;' +
        'padding:6px 10px;background:rgba(15,23,42,.92);border-bottom:1px solid rgba(255,255,255,.08);' +
        'position:sticky;top:0;z-index:120;backdrop-filter:blur(6px);}' +
      '.tof-tabs-scroll{display:flex;align-items:center;gap:6px;flex:1;min-width:0;overflow-x:auto;padding-bottom:2px;}' +
      '.tof-tabs-scroll::-webkit-scrollbar{height:6px;}' +
      '.tof-tabs-scroll::-webkit-scrollbar-thumb{background:rgba(255,255,255,.15);border-radius:6px;}' +
      '.tof-tab{display:flex;align-items:center;gap:6px;flex-shrink:0;max-width:170px;cursor:pointer;' +
        'padding:6px 10px;border-radius:8px 8px 0 0;border:1px solid rgba(255,255,255,.08);border-bottom:none;' +
        'background:rgba(255,255,255,.04);color:#94a3b8;font-size:12px;font-weight:600;user-select:none;' +
        'transition:background .15s,color .15s;}' +
      '.tof-tab:hover{background:rgba(255,255,255,.08);color:#cbd5e1;}' +
      '.tof-tab-active{background:linear-gradient(135deg,#7c6eff,#a78bfa);color:#fff;border-color:transparent;}' +
      '.tof-tab-active:hover{color:#fff;}' +
      '.tof-tab-name{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:130px;}' +
      '.tof-tab-x{opacity:.55;font-size:15px;line-height:1;padding:0 2px;border-radius:4px;}' +
      '.tof-tab-x:hover{opacity:1;background:rgba(0,0,0,.25);color:#fff;}' +
      '.tof-tabs-actions{display:flex;align-items:center;gap:6px;flex-shrink:0;}' +
      '.tof-tab-btn{cursor:pointer;padding:6px 10px;border-radius:8px;border:1px solid rgba(124,110,255,.35);' +
        'background:rgba(124,110,255,.12);color:#c4b5fd;font-size:11px;font-weight:700;white-space:nowrap;' +
        'transition:background .15s;}' +
      '.tof-tab-btn:hover{background:rgba(124,110,255,.25);color:#fff;}' +
      '.tof-tab-sep{width:1px;height:18px;background:rgba(255,255,255,.12);margin:0 2px;}' +
      '@media(max-width:768px){.tof-tab-btn{font-size:10px;padding:5px 7px;}.tof-tab{max-width:120px;}}' +
      // ── Modales propios ──
      '.tof-modal-ov{position:fixed;inset:0;z-index:99999;display:flex;align-items:center;justify-content:center;' +
        'background:rgba(8,12,24,.62);backdrop-filter:blur(3px);opacity:0;transition:opacity .15s;padding:20px;}' +
      '.tof-modal-ov.tof-modal-show{opacity:1;}' +
      '.tof-modal{width:100%;max-width:380px;box-sizing:border-box;background:#1a2235;color:#e2e8f0;' +
        'border:1px solid rgba(255,255,255,.10);border-radius:14px;padding:22px 22px 18px;' +
        'box-shadow:0 24px 60px rgba(0,0,0,.5);transform:translateY(8px) scale(.98);transition:transform .15s;' +
        'font-family:inherit;}' +
      '.tof-modal-ov.tof-modal-show .tof-modal{transform:none;}' +
      '.tof-modal-title{font-size:16px;font-weight:800;margin-bottom:8px;color:#fff;}' +
      '.tof-modal-msg{font-size:13px;line-height:1.5;color:#94a3b8;margin-bottom:16px;}' +
      '.tof-modal-input{width:100%;box-sizing:border-box;padding:10px 12px;margin-bottom:18px;font-size:14px;' +
        'background:rgba(255,255,255,.05);border:1px solid rgba(124,110,255,.4);border-radius:9px;color:#fff;outline:none;}' +
      '.tof-modal-input:focus{border-color:#a78bfa;box-shadow:0 0 0 3px rgba(124,110,255,.2);}' +
      '.tof-modal-actions{display:flex;justify-content:flex-end;gap:10px;}' +
      '.tof-modal-btn{cursor:pointer;padding:9px 16px;border-radius:9px;font-size:13px;font-weight:700;' +
        'border:1px solid transparent;transition:background .15s,opacity .15s;}' +
      '.tof-modal-cancel{background:rgba(255,255,255,.06);color:#cbd5e1;border-color:rgba(255,255,255,.12);}' +
      '.tof-modal-cancel:hover{background:rgba(255,255,255,.12);color:#fff;}' +
      '.tof-modal-ok{background:linear-gradient(135deg,#7c6eff,#a78bfa);color:#fff;}' +
      '.tof-modal-ok:hover{opacity:.9;}' +
      '.tof-modal-danger{background:linear-gradient(135deg,#ef4444,#dc2626);color:#fff;}' +
      '.tof-modal-danger:hover{opacity:.9;}';
    var st = document.createElement('style');
    st.id = 'tof-artboard-styles';
    st.textContent = css;
    document.head.appendChild(st);
  }

  // ── Inicialización ───────────────────────────────────────────────────────────
  function init(){
    injectStyles();

    // Asegurar el input file oculto
    if(!document.getElementById('tof-artboard-file')){
      var inp = document.createElement('input');
      inp.type = 'file'; inp.accept = 'application/json,.json';
      inp.id = 'tof-artboard-file'; inp.style.display = 'none';
      inp.addEventListener('change', _onFilePicked);
      document.body.appendChild(inp);
    }

    var stored = loadStored();
    if(stored){
      window._tofBoards = stored.boards.map(function(b){
        return { id:b.id || _uid(), name:b.name || 'Diseño', data:b.data || { texts:[], imageOverlays:[], shapes:[], imgUrl:null } };
      });
      window._tofActiveBoard = (typeof stored.active === 'number' && stored.active < window._tofBoards.length) ? stored.active : 0;
      restoreDesign(window._tofBoards[window._tofActiveBoard].data);
    } else {
      // Envolver el diseño actual (lo que app.js ya restauró) como primera pestaña
      window._tofBoards = [{ id:_uid(), name:'Diseño 1', data:snapLive() }];
      window._tofActiveBoard = 0;
    }
    renderTabs();

    // Autoguardado periódico (sobrevive recargas/cierres)
    setInterval(persist, 4000);
    window.addEventListener('beforeunload', persist);
    document.addEventListener('visibilitychange', function(){ if(document.hidden) persist(); });
  }

  // Esperar a que app.js termine de inicializar (loadPreferences, imageData, etc.)
  function boot(){
    var tries = 0;
    (function wait(){
      tries++;
      var ready = (typeof updatePreview === 'function') && (typeof updateJSON === 'function') && (window.imageData !== undefined);
      if(ready || tries > 40){
        init();
      } else {
        setTimeout(wait, 150);
      }
    })();
  }

  if(document.readyState === 'complete' || document.readyState === 'interactive'){
    setTimeout(boot, 400);
  } else {
    window.addEventListener('load', function(){ setTimeout(boot, 400); });
  }

  // Exponer handlers para los onclick
  window.tofSwitchBoard    = switchBoard;
  window.tofAddBoard       = addBoard;
  window.tofDuplicateBoard = duplicateBoard;
  window.tofDeleteBoard    = deleteBoard;
  window.tofRenameBoard    = renameBoard;
  window.tofExportFile     = exportFile;
  window.tofImportFile     = importFile;
  window._tofPersistBoards = persist;
  window._tofRenderTabs    = renderTabs;
})();
