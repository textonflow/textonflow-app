function launchConfetti() {
      var canvas = document.createElement('canvas');
      canvas.style.cssText = 'position:fixed;top:0;left:0;width:100%;height:100%;z-index:9999999;pointer-events:none;';
      document.body.appendChild(canvas);
      var ctx = canvas.getContext('2d');
      canvas.width = window.innerWidth;
      canvas.height = window.innerHeight;
      var colors = ['#667eea','#764ba2','#f59e0b','#10b981','#f87171','#60a5fa','#a78bfa','#fbbf24','#34d399','#fb7185'];
      var pieces = [];
      for (var i = 0; i < 140; i++) {
        pieces.push({
          x: Math.random() * canvas.width,
          y: -10 - Math.random() * 200,
          r: Math.random() * 7 + 3,
          color: colors[Math.floor(Math.random() * colors.length)],
          vx: Math.random() * 5 - 2.5,
          vy: Math.random() * 3 + 1.5,
          alpha: 1,
          rot: Math.random() * 360,
          rotSpeed: Math.random() * 8 - 4,
          shape: Math.random() > 0.5 ? 'rect' : 'circle'
        });
      }
      var start = Date.now();
      function animate() {
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        var elapsed = (Date.now() - start) / 1000;
        pieces.forEach(function(p) {
          p.x += p.vx; p.y += p.vy; p.vy += 0.06; p.rot += p.rotSpeed;
          if (elapsed > 2) p.alpha = Math.max(0, p.alpha - 0.018);
          ctx.save(); ctx.globalAlpha = p.alpha; ctx.fillStyle = p.color;
          ctx.translate(p.x, p.y); ctx.rotate(p.rot * Math.PI / 180);
          if (p.shape === 'rect') { ctx.fillRect(-p.r, -p.r * 0.5, p.r * 2, p.r); }
          else { ctx.beginPath(); ctx.arc(0, 0, p.r, 0, Math.PI * 2); ctx.fill(); }
          ctx.restore();
        });
        if (elapsed < 4) { requestAnimationFrame(animate); }
        else { document.body.removeChild(canvas); }
      }
      animate();
    }
    window.launchConfetti = launchConfetti;

    function openFeedback() {
      document.getElementById('fb-overlay').style.display = 'flex';
      document.getElementById('fb-form-view').style.display = 'block';
      document.getElementById('fb-thanks-view').style.display = 'none';
      document.getElementById('fb-error').style.display = 'none';
      var btn = document.getElementById('fb-send-btn');
      btn.disabled = false; btn.style.opacity = '1'; btn.textContent = 'Enviar feedback';
    }
    function closeFeedback() {
      document.getElementById('fb-overlay').style.display = 'none';
    }
    async function sendFeedback() {
      var name = document.getElementById('fb-name').value.trim();
      var email = document.getElementById('fb-email').value.trim();
      var msg = document.getElementById('fb-msg').value.trim();
      var errEl = document.getElementById('fb-error');
      var btn = document.getElementById('fb-send-btn');
      errEl.style.display = 'none';
      if (!name) { errEl.textContent = 'Por favor escribe tu nombre.'; errEl.style.display='block'; document.getElementById('fb-name').focus(); return; }
      if (!email || !email.includes('@')) { errEl.textContent = 'Escribe un correo electrónico válido.'; errEl.style.display='block'; document.getElementById('fb-email').focus(); return; }
      if (!msg) { errEl.textContent = 'El mensaje no puede estar vacío.'; errEl.style.display='block'; document.getElementById('fb-msg').focus(); return; }
      btn.disabled = true; btn.style.opacity = '0.6'; btn.textContent = 'Enviando...';
      try {
        var res = await fetch('/api/feedback', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ name: name, email: email, message: msg })
        });
        if (!res.ok) throw new Error('server');
        var data = await res.json();
        document.getElementById('fb-name').value = '';
        document.getElementById('fb-email').value = '';
        document.getElementById('fb-msg').value = '';
        document.getElementById('fb-form-view').style.display = 'none';
        var thanksEl = document.getElementById('fb-thanks-view');
        thanksEl.style.display = 'block';
        if (!data.vars_set) {
          thanksEl.querySelector('p').innerHTML = 'Feedback guardado.<br><small style="color:#f59e0b">Email no configurado en Railway.</small>';
        } else if (data.error) {
          thanksEl.querySelector('p').innerHTML = 'Feedback guardado.<br><small style="color:#f59e0b">Error: ' + data.error + '</small>';
        }
        if (!data.error && data.vars_set) launchConfetti();
        setTimeout(closeFeedback, data.error || !data.vars_set ? 8000 : 3000);
      } catch(e) {
        errEl.textContent = 'Hubo un error al enviar. Intenta de nuevo.';
        errEl.style.display = 'block';
        btn.disabled = false; btn.style.opacity = '1'; btn.textContent = 'Enviar feedback';
      }
    }
