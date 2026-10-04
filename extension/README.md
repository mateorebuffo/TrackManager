# Extensión de Chrome: Track Manager

Guarda en **Tracks → Pendiente** el tema que está sonando en [Traxsource](https://www.traxsource.com).

## Instalar (modo desarrollador)

1. En Chrome, abrí `chrome://extensions`.
2. Activá **Modo de desarrollador** (arriba a la derecha).
3. Tocá **Cargar extensión sin empaquetar** y elegí esta carpeta (`extension/`).
4. Fijala en la barra: ícono del rompecabezas → pin en "Track Manager".

## Usar

1. La primera vez, tocá el ícono y pegá el **token del agente** (en Track Manager → Config).
2. En Traxsource, poné un tema. Si te gusta, tocá el ícono: queda en Pendiente.

Si el tema ya estaba, avisa y no lo duplica. Los temas aparecen con la fuente **TX**.

Envía a `POST /api/tracks/import` (`app/api/extension.py`). Si Traxsource cambia su reproductor,
lo que hay que tocar son los selectores de `readPlayer()` en `popup.js`.
