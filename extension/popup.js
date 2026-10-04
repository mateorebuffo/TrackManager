// Se ejecuta al tocar el ícono: lee el tema que suena en Traxsource y lo manda a Pendiente.
// Para probar contra la app local, cambiar SERVER y sumar el host a host_permissions.
const SERVER = 'https://trackmanager.app';

const $ = (id) => document.getElementById(id);

// textContent y no innerHTML: el nombre del tema viene de una página ajena.
function show(cls, text, track) {
  const box = $('status');
  box.replaceChildren();
  const p = document.createElement('p');
  p.className = 'msg ' + cls;
  p.textContent = text;
  box.append(p);
  if (track) {
    const t = document.createElement('p');
    t.className = 'msg track';
    t.textContent = track;
    box.append(t);
  }
}

function askToken(why) {
  if (why) show('err', why); else $('status').replaceChildren();
  $('token-form').hidden = false;
  $('footer').hidden = true;
  $('token').focus();
}

// Corre dentro de la pestaña de Traxsource: tiene que ser autosuficiente.
function readPlayer() {
  const title = document.querySelector('#plrsTitle');
  const id = title && (title.getAttribute('href') || '').match(/\/track\/(\d+)/);
  if (!id || !title.textContent.trim()) return null;
  const text = (sel) => (document.querySelector(sel)?.textContent || '').replace(/\|/g, '').trim();
  const dur = text('#plrsDur');
  const seconds = /^\d+(:\d+)+$/.test(dur)
    ? dur.split(':').reduce((acc, n) => acc * 60 + Number(n), 0)
    : null;
  return {
    source_track_id: id[1],
    url: new URL(title.getAttribute('href'), location.origin).href,
    title: title.textContent.trim(),
    artists: [...document.querySelectorAll('#plrsArtists a.com-artists')]
      .map((a) => a.textContent.trim()).filter(Boolean),
    duration_seconds: seconds,
    label: text('#plrsLabel') || null,
    genre: text('#plrsGenre') || null,
    release_date: text('#plrsRelDate') || null,
  };
}

async function save(token) {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab || !/^https:\/\/(www\.)?traxsource\.com\//.test(tab.url || '')) {
    show('warn', 'Abrí Traxsource y poné un tema.');
    return;
  }

  let track;
  try {
    const [{ result }] = await chrome.scripting.executeScript({ target: { tabId: tab.id }, func: readPlayer });
    track = result;
  } catch {
    show('err', 'No se pudo leer la página. Recargala y probá de nuevo.');
    return;
  }
  if (!track) { show('warn', 'No hay ningún tema sonando.'); return; }
  if (!track.artists.length) { show('err', 'No se pudo leer el artista del tema.'); return; }

  let resp;
  try {
    resp = await fetch(SERVER + '/api/tracks/import', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + token },
      body: JSON.stringify(track),
    });
  } catch {
    show('err', 'No se pudo conectar con Track Manager.');
    return;
  }
  if (resp.status === 401) {
    await chrome.storage.local.remove('token');
    askToken('El token no es válido. Pegá el de Config.');
    return;
  }
  if (!resp.ok) { show('err', 'Track Manager respondió con un error (' + resp.status + ').'); return; }

  const body = await resp.json();
  if (body.status === 'exists') show('warn', 'Ya estaba en tus tracks:', body.track);
  else if (body.status === 'duplicate') show('warn', 'Agregado, pero puede ser un duplicado:', body.track);
  else show('ok', '✓ Agregado a pendientes:', body.track);
}

$('token-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const token = $('token').value.trim();
  if (!token) return;
  await chrome.storage.local.set({ token });
  $('token-form').hidden = true;
  $('footer').hidden = false;
  show('', 'Guardando…');
  save(token);
});

$('change-token').addEventListener('click', (e) => {
  e.preventDefault();
  askToken();
});

(async () => {
  const { token } = await chrome.storage.local.get('token');
  if (!token) { askToken(); return; }
  $('footer').hidden = false;
  save(token);
})();
