---
name: Track Manager
description: Herramienta de trabajo para DJs que junta likes de SoundCloud y YouTube, los ordena y los manda a descargar.
colors:
  primary: "#5558e3"
  primary-deep: "#4338ca"
  primary-soft: "#eef2ff"
  accent: "#6366f1"
  booth-night: "#0f172a"
  paper: "#f0f2f5"
  card: "#ffffff"
  surface-2: "#f8fafc"
  ink: "#1e293b"
  muted: "#56657a"
  line: "#e2e8f0"
  success-text: "#047857"
  warning-text: "#b45309"
  danger-text: "#b91c1c"
  warning: "#f59e0b"
  soundcloud: "#a84000"
  soundcloud-bg: "#fff3e0"
  spotify: "#065f46"
  spotify-bg: "#d1fae5"
  youtube: "#991b1b"
  youtube-bg: "#fee2e2"
  booth-ink: "#0b0d17"
  booth-panel: "#141727"
  booth-panel-2: "#1b1f33"
  booth-line: "#272b46"
  booth-text: "#e8e9f4"
  booth-muted: "#9296b6"
  booth-accent: "#8286f7"
  booth-button: "#4f46e5"
typography:
  display:
    fontFamily: "Unbounded, Arial Black, system-ui, sans-serif"
    fontSize: "1.45rem"
    fontWeight: 700
    lineHeight: 1.2
    letterSpacing: "-0.01em"
  title:
    fontFamily: "Instrument Sans, Segoe UI, system-ui, sans-serif"
    fontSize: "1rem"
    fontWeight: 700
    lineHeight: 1.3
  body:
    fontFamily: "Instrument Sans, Segoe UI, system-ui, sans-serif"
    fontSize: "0.9rem"
    fontWeight: 400
    lineHeight: 1.5
  table:
    fontFamily: "Instrument Sans, Segoe UI, system-ui, sans-serif"
    fontSize: "0.875rem"
    fontWeight: 400
    lineHeight: 1.5
  label:
    fontFamily: "Instrument Sans, Segoe UI, system-ui, sans-serif"
    fontSize: "0.72rem"
    fontWeight: 600
    letterSpacing: "0.07em"
  badge:
    fontFamily: "Instrument Sans, Segoe UI, system-ui, sans-serif"
    fontSize: "0.75rem"
    fontWeight: 600
  mono:
    fontFamily: "JetBrains Mono, ui-monospace, Consolas, monospace"
    fontSize: "0.8rem"
    fontWeight: 500
rounded:
  xs: "4px"
  sm: "6px"
  md: "10px"
  lg: "12px"
  pill: "999px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "16px"
  lg: "24px"
  xl: "32px"
components:
  button-primary:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.card}"
    rounded: "{rounded.sm}"
    padding: "5px 10px"
  button-primary-hover:
    backgroundColor: "{colors.primary-deep}"
  button-secondary:
    backgroundColor: "{colors.card}"
    textColor: "{colors.muted}"
    rounded: "{rounded.sm}"
    padding: "5px 10px"
  badge-status:
    rounded: "{rounded.pill}"
    typography: "{typography.badge}"
    padding: "3px 7px"
  card:
    backgroundColor: "{colors.card}"
    rounded: "{rounded.lg}"
    padding: "20px"
  nav:
    backgroundColor: "{colors.booth-night}"
    textColor: "{colors.card}"
    height: "52px"
---

# Design System: Track Manager

## Overview

**Creative North Star: "La cabina del DJ"**

Track Manager es una herramienta de trabajo, no una vidriera. El DJ entra a revisar una lista larga de temas, decidir rápido y mandar a descargar. Por eso la app es clara, densa y tranquila: fondo papel, tarjetas blancas, una sola voz de color (el índigo) y tipografía con carácter solo en los títulos. La noche de la cabina aparece en la barra de navegación y en la landing, que es la parte que vende.

La identidad vive en los detalles precisos: los títulos en Unbounded, los badges de estado que se leen de un vistazo, los logos de cada plataforma en su color. Todo lo demás retrocede para que mande la lista de tracks.

La app es para escritorio: el DJ la usa en la compu, con tablas anchas. No hay diseño móvil a propósito.

**Key Characteristics:**
- Una sola voz de color: índigo para acción y selección.
- Títulos de página en Unbounded; todo lo demás en Instrument Sans.
- Estados de track como badges con fondo tenue y texto oscuro del mismo tono.
- Íconos de Bootstrap Icons, nunca emojis.
- Densidad alta en tablas, aire generoso entre secciones.

## Colors

Fondo frío y neutro, un índigo como única voz de acción, y colores de estado que solo aparecen donde informan algo.

### Primary
- **Índigo de acción** (#5558e3): botones primarios, links, pestaña activa, barra de selección. Pasa 5.4:1 con blanco.
- **Índigo profundo** (#4338ca): hover de botones primarios y links.
- **Índigo vivo** (#6366f1): el color del logo. Solo para lo que no es texto: puntos, barras del ranking, anillo de foco, íconos de estados vacíos.
- **Índigo niebla** (#eef2ff): fondos de tips, ítem activo del menú, hover de botones secundarios índigo.

### Neutral
- **Noche de cabina** (#0f172a): barra de navegación y log de descarga.
- **Papel** (#f0f2f5): fondo de toda la app.
- **Tarjeta** (#ffffff): superficies de contenido.
- **Superficie 2** (#f8fafc): cabeceras de tabla, hover de filas.
- **Tinta** (#1e293b): texto principal.
- **Gris de apoyo** (#56657a): subtítulos, metadatos, etiquetas. Pasa 5:1 sobre papel.
- **Línea** (#e2e8f0): bordes y divisores.

### Landing (la cabina de noche)
La landing es la única pantalla oscura y tiene su propia paleta: **tinta de cabina** (#0b0d17) de fondo, **paneles** (#141727 y #1b1f33), **línea** (#272b46), **texto claro** (#e8e9f4), **gris lavanda** (#9296b6) para texto secundario, **índigo claro** (#8286f7) para acentos y **índigo botón** (#4f46e5) para el llamado a la acción. No se usa en la app.

### Estados y plataformas
- **Verde / ámbar / rojo de texto** (#047857 / #b45309 / #b91c1c): cuando un estado se escribe como texto.
- **Ámbar de aviso** (#f59e0b): solo como fondo, por ejemplo el contador de la cola.
- **SoundCloud, Spotify, YouTube**: cada uno con su par fondo tenue y texto oscuro, usado en los botones de importar, los filtros y los badges de fuente.

### Named Rules
**La regla del color que se lee.** Un color vivo (verde, ámbar, rojo, índigo vivo) nunca es texto sobre blanco. Si hay que escribirlo, se usa su variante oscura.

**La regla de una sola voz.** El índigo es la única acción. Verde, ámbar y rojo informan estados; no son botones.

## Typography

**Display Font:** Unbounded (con Arial Black)
**Body Font:** Instrument Sans (con Segoe UI)
**Label/Mono Font:** JetBrains Mono, solo en la landing: la maqueta de la app, rutas de carpeta y rótulos.

**Character:** Unbounded es ancha y gráfica, como la gráfica de un flyer; Instrument Sans es sobria y legible en tablas largas. Las mismas dos que la landing, así la app y la vidriera son el mismo producto.

### Hierarchy
- **Display** (700, 1.45rem, 1.2): un solo título por página, con su subtítulo en gris de apoyo debajo.
- **Title** (700, 1rem): títulos de estados vacíos, secciones de la Ayuda (1.2rem) y modales. Siempre como h2.
- **Body** (400, 0.9rem, 1.5): texto general.
- **Table** (400, 0.875rem): celdas; los números en cifras tabulares.
- **Label** (600, 0.72rem, mayúsculas, 0.07em): cabeceras de tabla y de tarjeta.
- **Badge** (600, 0.75rem): estados y fuentes.

### Named Rules
**La regla del piso de 11px.** Ningún texto que el usuario tenga que leer baja de 11px: ni contadores, ni badges, ni metadatos.

**La regla de un h1.** Cada página tiene un único h1 (el título); lo que viene después es h2. Nunca saltar de h1 a h5.

## Layout

Contenido centrado con ancho máximo de 1240px; Tracks usa 1440px porque es una tabla ancha. Margen lateral de 24px. Cada página abre con el mismo encabezado: título, subtítulo y, a la derecha, las acciones de la página. La tabla de Tracks usa anchos fijos de columna: Artista y Título se reparten el espacio y truncan con puntos suspensivos, para que los botones de acción nunca se corten. Paginación de 50 filas.

## Elevation & Depth

Casi plano. Las tarjetas tienen una sombra mínima para separarse del papel; las capas que flotan (modales, menú, barra de selección, reproductor) usan una sombra más difusa. Las sombras son siempre neutras, nunca de color.

### Shadow Vocabulary
- **Apoyo** (`box-shadow: 0 1px 3px rgba(0,0,0,.07), 0 1px 2px rgba(0,0,0,.04)`): tarjetas y barras de herramientas.
- **Flotante** (`box-shadow: 0 4px 16px rgba(0,0,0,.08)`): modales, barra de selección.

### Named Rules
**La regla sin halo.** Nada brilla: ninguna sombra violeta ni resplandor de color, tampoco en el login.

## Shapes

Esquinas suavemente redondeadas: 4px en fragmentos de código en línea, 6px en botones e inputs, 10px en alertas y barras, 12px en tarjetas y tablas, píldora completa en badges y contadores. Bordes de 1px en el tono línea. Sin franjas de color en el borde izquierdo: el acento va en un punto o en un fondo tenue.

## Components

### Buttons
- **Shape:** esquinas suaves (6px).
- **Primary:** índigo de acción con texto blanco; uno solo por zona.
- **Hover / Focus:** índigo profundo; anillo de foco de 2px en índigo vivo.
- **Secondary:** fondo blanco, borde línea, texto gris de apoyo.
- **Icon:** botón cuadrado con ícono de Bootstrap Icons, siempre con nombre accesible y tooltip.
- **Platform:** fondo tenue de la plataforma con su logo pintado en el color oscuro, y texto de acción ("Importar SoundCloud").

### Chips
- **Style:** badges en píldora con fondo tenue y texto oscuro del mismo tono.
- **State:** ocho estados de track (Pendiente, En cola, Descargado, No encontrado, Solo vinilo, Solo Bandcamp, Set, Descartado) más las tres fuentes.

### Cards / Containers
- **Corner Style:** 12px.
- **Background:** blanco sobre papel.
- **Shadow Strategy:** sombra de apoyo.
- **Border:** 1px línea.
- **Internal Padding:** 20px.

### Inputs / Fields
- **Style:** borde línea, fondo blanco, 6px.
- **Focus:** borde índigo y halo suave de 3px.

### Navigation
- Barra de 52px en noche de cabina, texto blanco al 72%. Ítem activo con fondo blanco al 10%. Las páginas de administración van agrupadas en un menú "Admin". A la derecha, un punto verde o rojo indica si el agente está conectado.

### Estado vacío
Tarjeta con borde punteado, ícono en índigo vivo, título h2, una frase y el botón de la acción siguiente. Nunca una alerta sola.

## Do's and Don'ts

### Do:
- **Do** usar los helpers de la base: `confirmSubmit` / `tmConfirm` para confirmar, `showToast` con Deshacer para acciones reversibles.
- **Do** escribir los estados como badges con su clase (`s-downloaded`, `s-not_found`...).
- **Do** darle a cada botón de ícono un `aria-label` y un tooltip.
- **Do** usar las variantes oscuras (#047857, #b45309, #b91c1c) cuando un estado es texto.

### Don't:
- **Don't** usar emojis como íconos.
- **Don't** usar `confirm()` del navegador.
- **Don't** poner franjas de color en el borde izquierdo de tarjetas, filas o avisos.
- **Don't** usar sombras de color ni resplandores.
- **Don't** bajar de 11px en texto que se lee.
