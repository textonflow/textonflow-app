/* ══════════════════════════════════════════════════════════════
   TextOnFlow — Sistema de internacionalización (i18n)
   v4 — Paso 6: Modales IA y resultado — 8 nuevas claves.
   ══════════════════════════════════════════════════════════════ */

(function () {
  'use strict';

  /* ── Diccionario de traducciones ── */
  var TRANSLATIONS = {

    /* ─── ESPAÑOL (base) ─── */
    es: {

      /* Carga de imagen */
      load_image:         'Cargar Imagen',
      replace_image:      'Sustituir Imagen',
      load_btn:           'Cargar',
      upload_btn:         'Subir',
      paste_url:          'Pega URL de imagen o usa los botones →',
      loading_image:      'Cargando imagen…',
      image_size:         'Tamaño',
      guides:             'Guías',
      filter_label:       'Filtro:',
      no_filter:          'Sin filtro',
      background:         'Fondo',

      /* Botones generales */
      add:                'Agregar',
      cancel:             'Cancelar',
      copy:               'Copiar',
      save:               'Guardar',
      delete:             'Eliminar',
      close:              'Cerrar',
      apply:              'Aplicar',
      reset:              'Restablecer',
      confirm:            'Confirmar',
      generate:           'Generar',
      download:           'Descargar',
      share:              'Compartir',
      undo:               'Deshacer',
      redo:               'Rehacer',

      /* Panel de textos */
      texts_section:      'Textos',
      font:               'Fuente',
      font_size:          'Tamaño',
      color:              'Color',
      align:              'Alineación',
      bold:               'Negrita',
      italic:             'Cursiva',
      shadow:             'Sombra',
      shadow_color:       'Color sombra',
      shadow_offset:      'Desplazamiento',
      shadow_blur:        'Difuminado',
      bg_label:           'Fondo del texto',
      bg_color:           'Color fondo',
      bg_padding:         'Relleno',
      bg_radius:          'Radio',
      opacity:            'Opacidad',
      letter_spacing:     'Espaciado',
      line_height:        'Interlineado',
      rotation:           'Rotación',
      width_label:        'Ancho',
      uppercase:          'Mayúsculas',
      position_x:        'Posición X',
      position_y:        'Posición Y',
      text_placeholder:   'Escribe aquí… usa {{variable}} para personalizar',

      /* Distorsión */
      distortion:         'Distorsión',
      warp:               'Curvatura',
      skew_x:             'Sesgo X',
      skew_y:             'Sesgo Y',
      no_warp:            'Sin curva',
      warp_arc:           'Arco',
      warp_arc_lower:     'Arco inferior',
      warp_arc_upper:     'Arco superior',
      warp_arch:          'Aro',
      warp_bulge:         'Bulto',
      warp_shell_lower:   'Concha inferior',
      warp_shell_upper:   'Concha superior',
      warp_flag:          'Bandera',
      warp_wave:          'Ola',
      warp_fish:          'Pez',
      warp_rise:          'Ascenso',
      warp_fisheye:       'Ojo de pez',
      warp_inflate:       'Inflar',
      warp_squeeze:       'Apretar',
      warp_twist:         'Torsión',

      /* Stickers */
      stickers_section:   'Stickers',
      upload_sticker:     'Subir sticker',
      emoji_picker:       'Emojis',

      /* Marcos */
      frames_section:     'Marcos',
      add_frame:          'Agregar marco',
      frame_color:        'Color de marco',
      frame_border:       'Borde',
      frame_opacity:      'Opacidad',

      /* Viñeta */
      vignette_section:   'Viñeta',
      vignette_intensity: 'Intensidad',
      vignette_radius:    'Radio',

      /* Contador */
      countdown_section:  'Contador',
      countdown_event:    'Evento',
      countdown_urgency:  'Urgencia',
      countdown_date:     'Fecha',
      countdown_timezone: 'Zona horaria',
      countdown_style:    'Estilo',
      countdown_help_event:   'Todos los usuarios ven la misma cuenta regresiva.',
      countdown_help_urgency: 'Cada usuario tiene su propio reloj.',

      /* Código QR */
      qr_section:         'Código QR',
      qr_url:             'URL o texto del QR',
      qr_add:             'Agregar QR al canvas',
      qr_fg_color:        'Color QR',
      qr_bg_color:        'Fondo QR',
      qr_border_color:    'Color marco',
      qr_padding:         'Padding',
      qr_format:          'Formato',

      /* Google Drive */
      drive_connect:      'Conectar',
      drive_disconnect:   'Desconectar',
      drive_connected:    'Conectado',
      drive_folder:       'Carpeta: TextOnFlow',
      drive_connect_btn:  'Conectar',

      /* JSON ManyChat */
      json_section:       'JSON para ManyChat',
      json_copy:          'Copiar JSON',
      json_copied:        '✓ Copiado',
      json_panel_title:   'JSON para ManyChat',
      json_copy_btn:      'Copiar',

      /* Modal de resultado */
      result_title:       'Imagen generada',
      result_download:    'Descargar',
      result_copy_url:    'Copiar URL',
      result_drive:       'Drive',
      result_share:       'Compartir',
      result_close:       'Cerrar',
      result_json_copy:   'Copiar JSON',
      url_copied:         'URL copiada',
      json_copied_ok:     'JSON copiado',
      img_downloaded:     'Imagen descargada',
      wa_desktop_hint:    'Imagen descargada — adjúntala en WhatsApp',

      /* IA */
      ai_title:           'Crear imagen con IA',
      ai_prompt_ph:       'Describe la imagen que quieres generar…',
      ai_generate_btn:    '✦ Generar imagen',
      ai_retry:           'Volver a intentar',
      ai_use:             'Usar esta imagen',
      ai_edit_prompt:     'Editar prompt',
      ai_enhance:         'Mejorar prompt',
      ai_aspect:          'Aspecto de imagen',
      ai_style:           'Estilo visual',
      ai_generating:      'Generando imagen…',
      ai_edit_title:      'Editar imagen con IA',
      ai_edit_ph:         'Describe qué quieres cambiar…',

      /* Visualizar / Generación */
      visualize_btn:          '▶ Visualizar imagen',
      visualize_btn_text:     'Visualizar imagen',
      visualize_btn_hint:     'Genera la imagen final lista para usar en ManyChat',
      generating:             'Generando imagen personalizada…',
      generating_wait:        'Aguarda un momento',
      generating_short:       'Generando...',
      connecting_server:      'Conectando con el servidor...',
      generating_status_html: 'Generando imagen personalizada… <br><span style="opacity:0.7;font-size:10px;">Puede tomar 3–8 segundos</span>',
      error_connection_server:'No se pudo conectar con el servidor. Verifica tu conexión o intenta de nuevo.',
      error_template_url_404: 'La URL de la imagen plantilla ya no es válida (404). Recarga la imagen desde ManyChat y vuelve a intentarlo.',
      image_loaded:           'Imagen cargada',
      url_timer_generated:    '¡URL generada! Lista para usar en ManyChat',
      copy_url:               'Copiar URL',
      qr_generating:          'Generando QR…',

      /* Menú contextual */
      ctx_group:          'Agrupar',
      ctx_ungroup:        'Desagrupar',
      ctx_copy_style:     'Copiar estilo',
      ctx_paste_style:    'Pegar estilo',
      ctx_center_canvas:  'Centrar en canvas',
      ctx_duplicate:      'Duplicar',
      ctx_delete:         'Eliminar',

      /* Acordeones */
      section_templates:  'Templates de bienvenida',
      section_texts:      'Textos',
      section_stickers:   'Stickers',
      section_frames:     'Marcos',
      section_countdown:  'Contador',
      section_qr:         'Código QR',
      section_writer:     'Redactor IA',

      /* Redactor IA */
      writer_tone:        'Tono',
      writer_input_label: 'Tu texto o idea',
      writer_generate:    'Generar',
      writer_result:      'Resultado',
      writer_insert:      '＋ Insertar en capa',
      writer_retry:       '↺ Regenerar',

      /* Watermark */
      watermark_include:  'Incluir sello',

      /* Footer */
      privacy_link:       'Privacidad',
      terms_link:         'Términos',

      /* Recursos */
      rec_resources:      'Recursos',
      rec_doc_group:      'Documentación',
      rec_manual:         'Manual de usuario',
      rec_faq:            'Preguntas frecuentes (FAQ)',
      rec_prices:         'Precios y planes',
      rec_cases:          'Casos de éxito',

      /* ManyChat */
      manychat_section:   'Variables de ManyChat',
      add_manychat_field: 'Agregar campo de ManyChat',
      field_name_label:   'Escribe el nombre exacto del campo (sin llaves):',
      system_fields:      'Campos del sistema:',
      add_to_text:        'Agregar texto a la selección',

      /* Asistente */
      assistant_title:    'Asistente TextOnFlow',
      assistant_ph:       'Escribe tu pregunta…',
      assistant_send:     'Enviar',

      /* Feedback */
      feedback_title:     'Enviar feedback',
      feedback_ph:        '¿Qué mejorarías o qué problema encontraste?',
      feedback_send:      'Enviar feedback',
      feedback_thanks:    '¡Gracias por tu feedback!',

      /* Paso 10 — Modal, Custom Style, Tone Pills, Botones */
      confirm_action_title: 'Confirmar acción',
      cancel:               'Cancelar',
      confirm_btn:          'Confirmar',
      add_field_btn:        'Agregar',
      cs_form_title:        'Crear estilo personalizado',
      cs_label_name:        'Nombre del estilo',
      cs_label_prompt:      'Prompt del estilo',
      cs_label_desc:        '(describe el look visual)',
      cs_label_palette:     'Paleta de colores',
      drive_connect_btn:    'Conectar con Drive',
      result_drive_btn:     'Drive',
      cai_edit_btn:         'Ajustar con IA',
      add_date_btn:         'Fecha',
      error_prefix:         'Error: ',
      tone_profesional:     'Profesional',
      tone_amigable:        'Amigable',
      tone_divertido:       'Divertido',
      tone_serio:           'Serio',
      tone_inspirador:      'Inspirador',
      tone_urgente:         'Urgente',

      /* Paso 9 — Placeholders y Tooltips */
      ph_cai_edit:      'Ej: Que sostenga una botella de agua, cambia el fondo a playa, quítale el sombrero...',
      ph_qr_wa_msg:     'Hola, me interesa tu promoción...',
      ph_rdtr_text:     'Escribe tu texto o pide algo: «Escribe el Salmo 23»',
      ph_fb_name:       'Tu nombre *',
      ph_fb_email:      'Tu correo electrónico *',
      ph_fb_msg:        'Cuéntanos qué piensas, qué mejorarías o qué te falta... *',
      ph_cs_name:       'Ej: Mi estilo corporativo',
      ph_cs_prompt:     'Ej: ilustración plana con colores corporativos, estilo minimalista, formas geométricas limpias, sin sombras...',
      ph_fb_chat:       'Escribe tu pregunta…',

      title_enhance:      'Mejorar automáticamente tu descripción con IA',
      title_no_text:      'Mejorar prompt garantizando que NO aparezca texto ni tipografía generada en la imagen',
      title_revert:       'Volver al texto original',
      title_pos_tl:       'Arriba izquierda',
      title_pos_tc:       'Arriba centro',
      title_pos_tr:       'Arriba derecha',
      title_pos_ml:       'Centro izquierda',
      title_pos_c:        'Centro',
      title_pos_mr:       'Centro derecha',
      title_pos_bl:       'Abajo izquierda',
      title_pos_bc:       'Abajo centro',
      title_pos_br:       'Abajo derecha',
      title_cycle_bg:     'Cambiar fondo del canvas (clic para ciclar)',
      title_ui_size:      'Tamaño de la interfaz',
      title_zoom_out:     'Texto más pequeño',
      title_zoom_in:      'Texto más grande',
      title_filter_eye:   'Mostrar/ocultar filtro en preview',
      title_group_action: 'Agrupar / Desagrupar textos seleccionados',

      /* Paso 8 — Legal, AI panel, Timer, Canvas */
      terms_title:          'Política de Uso Aceptable',
      terms_subtitle:       'Términos de Uso',
      terms_intro:          'Por favor lee y acepta estos términos antes de continuar.',
      terms_checkbox:       'He leído y acepto la Política de Uso Aceptable. Entiendo que soy el único responsable del contenido que genere con esta herramienta.',
      terms_accept_btn:     'Acepto — Ingresar a TextOnFlow →',
      cookie_title:         '🍪 Usamos cookies y almacenamiento local',

      ai_edit_title:        'Ajustar esta imagen',
      ai_edit_subtitle:     'Describe qué quieres cambiar',
      ai_edit_btn_lbl:      'Ajustar',
      ai_versions_label:    'Versiones:',
      ai_regen_btn:         'Volver a crear',
      ai_download_btn:      'Descargar',
      ai_use_btn:           'Usar imagen',
      ai_dimension:         'Dimensión',
      ai_what_create:       '¿Qué quieres crear?',
      ai_enhance_btn:       'Mejorar prompt',
      ai_no_text_btn:       'Sin textos',
      ai_generate_btn_lbl:  'Generar imagen',
      ai_ref_photos:        'Fotos de referencia',
      ai_optional:          '(opcional)',
      ai_ref_desc:          'Sube hasta 5 fotos de tu producto. La IA generará escenas con ese producto exacto.',
      ai_style_section:     'Estilo',

      timer_event_tab:      'Contador de Evento',
      timer_urgency_tab:    'Contador de Urgencia',
      timer_base_img:       'Imagen base',
      timer_expiry_date:    'Fecha de vencimiento',
      timer_timezone_lbl:   'Zona horaria:',
      timer_duration:       'Duración del contador',
      timer_unit_hours:     'horas',
      timer_from_msg:       'desde que el usuario recibe el mensaje',
      timer_template_name:  'Nombre del template',
      timer_position:       'Posición del contador en la imagen',
      timer_style_details:  'Estilo del contador',
      timer_font_lbl:       'Fuente',
      timer_format_lbl:     'Formato',
      timer_color_lbl:      'Color',
      timer_size_lbl:       'Tamaño (px)',
      timer_expired_text:   'Texto cuando expira',
      timer_expired_img:    'Imagen al expirar',
      timer_expired_note:   '(opcional — reemplaza la imagen base)',

      upload_section_title: 'Cargar Imagen',
      bg_white:             'Blanco',
      bg_grey:              'Gris',
      bg_dark:              'Oscuro',
      guides_label:         'Guías',
      my_styles:            'Mis estilos',

      /* Paso 7 — Errores, botones y atajos */
      error_connection:       'Error de conexión',
      error_enter_base_url:   'Ingresa la URL de la imagen base',
      error_complete_date:    'Completa la fecha: DD / MM / AAAA',
      error_gen_first:        'Primero genera una imagen para poder ajustarla.',
      error_load_canvas_img:  'Primero carga una imagen en el canvas.',
      error_canvas_img_fail:  'No se pudo obtener la imagen del canvas.',
      img_saved_canvas:       'Imagen guardada. Puede tardar en verse en el canvas.',
      img_updated_ai:         '¡Imagen actualizada con IA!',
      error_upload_connect:   'Error de conexión al subir imagen',
      error_duration_zero:    'La duración debe ser mayor a 0',
      error_start_gen:        'Error al iniciar generación',
      error_gen_timeout:      'La generación tardó demasiado. Intenta de nuevo.',
      error_max_refs:         'Máximo {n} imágenes de referencia.',
      error_max_5_refs:       'Máximo 5 fotos de referencia',
      error_image_format:     'Solo se permiten imágenes JPG, PNG o WebP',
      error_adjust_img:       'Error al ajustar la imagen',
      error_use_img:          'Error al usar la imagen',
      gen_url_timer_btn:      'Generar URL del contador',
      save_style_btn:         'Guardar estilo',
      shortcut_add_text:      'Agregar texto a la selección',
      shortcut_rubber_band:   'Selección múltiple por área',
      shortcut_delete_layer:  'Eliminar capa seleccionada',
      drive_not_configured:   'Google Drive no configurado.\nAgrega tu GDRIVE_CLIENT_ID en app.js para activar esta función.',
      wa_write_message:       'Escribe el mensaje que se enviará por WhatsApp.',

      /* Modal IA — Paso 6 */
      ai_creating:            'Creando tu imagen',
      ai_step_interpreting:   'Interpretando prompt',
      ai_step_generating:     'Generando',
      ai_step_finalizing:     'Finalizando',

      /* Modal resultado — Paso 6 */
      json_template_lbl:      'JSON del template',
      drive_upload_lbl:       'Subir a Drive',
      drive_section_lbl:      'Drive',
      drive_connect_drive_lbl:'Conectar Drive',

      /* Panel derecho dinámico — Paso 5 */
      offset_x_lbl:       'Desp. X:',
      offset_y_lbl:       'Desp. Y:',
      opacity_pct_lbl:    'Opac. %:',
      blur_lbl:           'Blur:',
      bg_box_lbl:         'Caja de fondo',
      corners_lbl:        'Esquinas:',
      angle_lbl:          'Ángulo:',
      px_all_lbl:         'px (todos)',
      box_border_lbl:     'Borde de la caja',
      thickness_lbl:      'Grosor:',
      line_lbl:           'Línea:',
      curvature_lbl:      'Curvatura:',
      warp_realtime_hint: '✔ Vista previa en tiempo real · también se aplica al generar',
      my_fields_lbl:      'Mis campos:',
      text_style_lbl:     'Estilo del texto',
      center_btn:         '⊕ Centrar',
      masks_lbl:          'Máscaras:',
      corner_radius_lbl:  'Radio esquinas:',
      add_countdown_btn:  '⏱  Agregar Contador Regresivo',
      styles_tof_sep:     'Estilos TextOnFlow',
      color_colon:        'Color:',
      padding_colon:      'Padding:',
      opacity_colon:      'Opacidad:',

      /* Estados y mensajes */
      error_no_image:     'Carga una imagen primero',
      error_generate:     'Error al generar. Intenta de nuevo.',
      error_upload:       'Error al subir la imagen',
      copied_ok:          'Copiado',
      saved_ok:           'Guardado',
      loading:            'Cargando…',
      done:               'Listo',

      /* Tooltips / ayudas */
      tip_double_click:   'Doble clic: centrar en X e Y',
      tip_shift_dblclick: 'Shift + doble clic: centrar solo en X',
      tip_arrows:         'Flechas: mover 1 px · Shift+Flechas: 10 px',
      tip_right_click:    'Clic derecho: menú de opciones',
      tip_eyedropper:     'Tomar color del canvas',
      tip_warp_bend:      'Intensidad de la curvatura',

      /* Manual */
      manual_link:        'Manual',
      docs_link:          'Documentación',

      /* Atajos de teclado */
      shortcut_center:    'Centrar en canvas (X e Y)',
      shortcut_center_x:  'Centrar solo en X',

    },

    /* ─── ENGLISH ─── */
    en: {

      /* Image loading */
      load_image:         'Load Image',
      replace_image:      'Replace Image',
      load_btn:           'Load',
      upload_btn:         'Upload',
      paste_url:          'Paste image URL or use the buttons →',
      loading_image:      'Loading image…',
      image_size:         'Size',
      guides:             'Guides',
      filter_label:       'Filter:',
      no_filter:          'No filter',
      background:         'Background',

      /* General buttons */
      add:                'Add',
      cancel:             'Cancel',
      copy:               'Copy',
      save:               'Save',
      delete:             'Delete',
      close:              'Close',
      apply:              'Apply',
      reset:              'Reset',
      confirm:            'Confirm',
      generate:           'Generate',
      download:           'Download',
      share:              'Share',
      undo:               'Undo',
      redo:               'Redo',

      /* Text panel */
      texts_section:      'Texts',
      font:               'Font',
      font_size:          'Size',
      color:              'Color',
      align:              'Alignment',
      bold:               'Bold',
      italic:             'Italic',
      shadow:             'Shadow',
      shadow_color:       'Shadow color',
      shadow_offset:      'Offset',
      shadow_blur:        'Blur',
      bg_label:           'Text background',
      bg_color:           'Background color',
      bg_padding:         'Padding',
      bg_radius:          'Radius',
      opacity:            'Opacity',
      letter_spacing:     'Letter spacing',
      line_height:        'Line height',
      rotation:           'Rotation',
      width_label:        'Width',
      uppercase:          'Uppercase',
      position_x:        'Position X',
      position_y:        'Position Y',
      text_placeholder:   'Type here… use {{variable}} to personalize',

      /* Distortion */
      distortion:         'Distortion',
      warp:               'Warp',
      skew_x:             'Skew X',
      skew_y:             'Skew Y',
      no_warp:            'No warp',
      warp_arc:           'Arc',
      warp_arc_lower:     'Arc Lower',
      warp_arc_upper:     'Arc Upper',
      warp_arch:          'Arch',
      warp_bulge:         'Bulge',
      warp_shell_lower:   'Shell Lower',
      warp_shell_upper:   'Shell Upper',
      warp_flag:          'Flag',
      warp_wave:          'Wave',
      warp_fish:          'Fish',
      warp_rise:          'Rise',
      warp_fisheye:       'Fisheye',
      warp_inflate:       'Inflate',
      warp_squeeze:       'Squeeze',
      warp_twist:         'Twist',

      /* Stickers */
      stickers_section:   'Stickers',
      upload_sticker:     'Upload sticker',
      emoji_picker:       'Emojis',

      /* Frames */
      frames_section:     'Frames',
      add_frame:          'Add frame',
      frame_color:        'Frame color',
      frame_border:       'Border',
      frame_opacity:      'Opacity',

      /* Vignette */
      vignette_section:   'Vignette',
      vignette_intensity: 'Intensity',
      vignette_radius:    'Radius',

      /* Countdown */
      countdown_section:  'Countdown',
      countdown_event:    'Event',
      countdown_urgency:  'Urgency',
      countdown_date:     'Date',
      countdown_timezone: 'Timezone',
      countdown_style:    'Style',
      countdown_help_event:   'All users see the same countdown.',
      countdown_help_urgency: 'Each user has their own timer.',

      /* QR code */
      qr_section:         'QR Code',
      qr_url:             'QR URL or text',
      qr_add:             'Add QR to canvas',
      qr_fg_color:        'QR color',
      qr_bg_color:        'QR background',
      qr_border_color:    'Border color',
      qr_padding:         'Padding',
      qr_format:          'Format',

      /* Google Drive */
      drive_connect:      'Connect',
      drive_disconnect:   'Disconnect',
      drive_connected:    'Connected',
      drive_folder:       'Folder: TextOnFlow',
      drive_connect_btn:  'Connect',

      /* JSON ManyChat */
      json_section:       'JSON for ManyChat',
      json_copy:          'Copy JSON',
      json_copied:        '✓ Copied',
      json_panel_title:   'JSON for ManyChat',
      json_copy_btn:      'Copy',

      /* Result modal */
      result_title:       'Generated image',
      result_download:    'Download',
      result_copy_url:    'Copy URL',
      result_drive:       'Drive',
      result_share:       'Share',
      result_close:       'Close',
      result_json_copy:   'Copy JSON',
      url_copied:         'URL copied',
      json_copied_ok:     'JSON copied',
      img_downloaded:     'Image downloaded',
      wa_desktop_hint:    'Image downloaded — attach it in WhatsApp',

      /* AI */
      ai_title:           'Create image with AI',
      ai_prompt_ph:       'Describe the image you want to generate…',
      ai_generate_btn:    '✦ Generate image',
      ai_retry:           'Try again',
      ai_use:             'Use this image',
      ai_edit_prompt:     'Edit prompt',
      ai_enhance:         'Enhance prompt',
      ai_aspect:          'Image aspect',
      ai_style:           'Visual style',
      ai_generating:      'Generating image…',
      ai_edit_title:      'Edit image with AI',
      ai_edit_ph:         'Describe what you want to change…',

      /* Visualize / Generation */
      visualize_btn:          '▶ Preview image',
      visualize_btn_text:     'Preview image',
      visualize_btn_hint:     'Generates the final image ready to use in ManyChat',
      generating:             'Generating personalized image…',
      generating_wait:        'Please wait',
      generating_short:       'Generating...',
      connecting_server:      'Connecting to server...',
      generating_status_html: 'Generating personalized image… <br><span style="opacity:0.7;font-size:10px;">May take 3–8 seconds</span>',
      error_connection_server:'Could not connect to the server. Check your connection or try again.',
      error_template_url_404: 'The template image URL is no longer valid (404). Reload the image from ManyChat and try again.',
      image_loaded:           'Image loaded',
      url_timer_generated:    'URL generated! Ready to use in ManyChat',
      copy_url:               'Copy URL',
      qr_generating:          'Generating QR…',

      /* Context menu */
      ctx_group:          'Group',
      ctx_ungroup:        'Ungroup',
      ctx_copy_style:     'Copy style',
      ctx_paste_style:    'Paste style',
      ctx_center_canvas:  'Center on canvas',
      ctx_duplicate:      'Duplicate',
      ctx_delete:         'Delete',

      /* Accordions */
      section_templates:  'Welcome templates',
      section_texts:      'Texts',
      section_stickers:   'Stickers',
      section_frames:     'Frames',
      section_countdown:  'Countdown',
      section_qr:         'QR Code',
      section_writer:     'AI Writer',

      /* AI Writer */
      writer_tone:        'Tone',
      writer_input_label: 'Your text or idea',
      writer_generate:    'Generate',
      writer_result:      'Result',
      writer_insert:      '＋ Insert in layer',
      writer_retry:       '↺ Regenerate',

      /* Watermark */
      watermark_include:  'Include watermark',

      /* Footer */
      privacy_link:       'Privacy',
      terms_link:         'Terms',

      /* Resources */
      rec_resources:      'Resources',
      rec_doc_group:      'Documentation',
      rec_manual:         'User Manual',
      rec_faq:            'FAQ',
      rec_prices:         'Pricing & plans',
      rec_cases:          'Success cases',

      /* ManyChat */
      manychat_section:   'ManyChat Variables',
      add_manychat_field: 'Add ManyChat field',
      field_name_label:   'Enter the exact field name (without braces):',
      system_fields:      'System fields:',
      add_to_text:        'Add text to selection',

      /* Assistant */
      assistant_title:    'TextOnFlow Assistant',
      assistant_ph:       'Type your question…',
      assistant_send:     'Send',

      /* Feedback */
      feedback_title:     'Send feedback',
      feedback_ph:        "What would you improve or what issue did you find?",
      feedback_send:      'Send feedback',
      feedback_thanks:    'Thanks for your feedback!',

      /* Step 10 — Modal, Custom Style, Tone Pills, Buttons */
      confirm_action_title: 'Confirm action',
      cancel:               'Cancel',
      confirm_btn:          'Confirm',
      add_field_btn:        'Add',
      cs_form_title:        'Create custom style',
      cs_label_name:        'Style name',
      cs_label_prompt:      'Style prompt',
      cs_label_desc:        '(describe the visual look)',
      cs_label_palette:     'Color palette',
      drive_connect_btn:    'Connect with Drive',
      result_drive_btn:     'Drive',
      cai_edit_btn:         'Adjust with AI',
      add_date_btn:         'Date',
      error_prefix:         'Error: ',
      tone_profesional:     'Professional',
      tone_amigable:        'Friendly',
      tone_divertido:       'Fun',
      tone_serio:           'Serious',
      tone_inspirador:      'Inspiring',
      tone_urgente:         'Urgent',

      /* Step 9 — Placeholders and Tooltips */
      ph_cai_edit:      'E.g.: Hold a water bottle, change background to beach, remove the hat...',
      ph_qr_wa_msg:     'Hi, I\'m interested in your promotion...',
      ph_rdtr_text:     'Write your text or request something: «Write Psalm 23»',
      ph_fb_name:       'Your name *',
      ph_fb_email:      'Your email address *',
      ph_fb_msg:        'Tell us what you think, what you\'d improve, or what\'s missing... *',
      ph_cs_name:       'E.g.: My corporate style',
      ph_cs_prompt:     'E.g.: flat illustration with corporate colors, minimalist style, clean geometric shapes, no shadows...',
      ph_fb_chat:       'Write your question…',

      title_enhance:      'Automatically improve your description with AI',
      title_no_text:      'Enhance prompt ensuring NO generated text or typography appears in the image',
      title_revert:       'Revert to original text',
      title_pos_tl:       'Top left',
      title_pos_tc:       'Top center',
      title_pos_tr:       'Top right',
      title_pos_ml:       'Center left',
      title_pos_c:        'Center',
      title_pos_mr:       'Center right',
      title_pos_bl:       'Bottom left',
      title_pos_bc:       'Bottom center',
      title_pos_br:       'Bottom right',
      title_cycle_bg:     'Change canvas background (click to cycle)',
      title_ui_size:      'Interface size',
      title_zoom_out:     'Smaller text',
      title_zoom_in:      'Larger text',
      title_filter_eye:   'Show/hide filter in preview',
      title_group_action: 'Group / Ungroup selected texts',

      /* Step 8 — Legal, AI panel, Timer, Canvas */
      terms_title:          'Acceptable Use Policy',
      terms_subtitle:       'Terms of Use',
      terms_intro:          'Please read and accept these terms before continuing.',
      terms_checkbox:       'I have read and accept the Acceptable Use Policy. I understand that I am solely responsible for the content I generate with this tool.',
      terms_accept_btn:     'I Accept — Enter TextOnFlow →',
      cookie_title:         '🍪 We use cookies and local storage',

      ai_edit_title:        'Adjust this image',
      ai_edit_subtitle:     'Describe what you want to change',
      ai_edit_btn_lbl:      'Adjust',
      ai_versions_label:    'Versions:',
      ai_regen_btn:         'Recreate',
      ai_download_btn:      'Download',
      ai_use_btn:           'Use image',
      ai_dimension:         'Dimension',
      ai_what_create:       'What do you want to create?',
      ai_enhance_btn:       'Enhance prompt',
      ai_no_text_btn:       'No text',
      ai_generate_btn_lbl:  'Generate image',
      ai_ref_photos:        'Reference photos',
      ai_optional:          '(optional)',
      ai_ref_desc:          'Upload up to 5 product photos. AI will generate scenes with that exact product.',
      ai_style_section:     'Style',

      timer_event_tab:      'Event Timer',
      timer_urgency_tab:    'Urgency Timer',
      timer_base_img:       'Base image',
      timer_expiry_date:    'Expiry date',
      timer_timezone_lbl:   'Timezone:',
      timer_duration:       'Timer duration',
      timer_unit_hours:     'hours',
      timer_from_msg:       'from when the user receives the message',
      timer_template_name:  'Template name',
      timer_position:       'Counter position on image',
      timer_style_details:  'Timer style',
      timer_font_lbl:       'Font',
      timer_format_lbl:     'Format',
      timer_color_lbl:      'Color',
      timer_size_lbl:       'Size (px)',
      timer_expired_text:   'Text when expired',
      timer_expired_img:    'Image on expiry',
      timer_expired_note:   '(optional — replaces the base image)',

      upload_section_title: 'Load Image',
      bg_white:             'White',
      bg_grey:              'Grey',
      bg_dark:              'Dark',
      guides_label:         'Guides',
      my_styles:            'My styles',

      /* Step 7 — Errors, buttons and shortcuts */
      error_connection:       'Connection error',
      error_enter_base_url:   'Enter the base image URL',
      error_complete_date:    'Complete the date: DD / MM / YYYY',
      error_gen_first:        'First generate an image to be able to adjust it.',
      error_load_canvas_img:  'First load an image onto the canvas.',
      error_canvas_img_fail:  'Could not get the image from canvas.',
      img_saved_canvas:       'Image saved. It may take a moment to appear on the canvas.',
      img_updated_ai:         'Image updated with AI!',
      error_upload_connect:   'Connection error while uploading image',
      error_duration_zero:    'Duration must be greater than 0',
      error_start_gen:        'Error starting generation',
      error_gen_timeout:      'Generation took too long. Try again.',
      error_max_refs:         'Maximum {n} reference images.',
      error_max_5_refs:       'Maximum 5 reference photos',
      error_image_format:     'Only JPG, PNG or WebP images are allowed',
      error_adjust_img:       'Error adjusting image',
      error_use_img:          'Error using image',
      gen_url_timer_btn:      'Generate Timer URL',
      save_style_btn:         'Save style',
      shortcut_add_text:      'Add text to selection',
      shortcut_rubber_band:   'Multi-selection by area',
      shortcut_delete_layer:  'Delete selected layer',
      drive_not_configured:   'Google Drive not configured.\nAdd your GDRIVE_CLIENT_ID in app.js to enable this feature.',
      wa_write_message:       'Write the message to be sent via WhatsApp.',

      /* AI modal — Step 6 */
      ai_creating:            'Creating your image',
      ai_step_interpreting:   'Interpreting prompt',
      ai_step_generating:     'Generating',
      ai_step_finalizing:     'Finalizing',

      /* Result modal — Step 6 */
      json_template_lbl:      'Template JSON',
      drive_upload_lbl:       'Upload to Drive',
      drive_section_lbl:      'Drive',
      drive_connect_drive_lbl:'Connect Drive',

      /* Right panel dynamic — Step 5 */
      offset_x_lbl:       'Offset X:',
      offset_y_lbl:       'Offset Y:',
      opacity_pct_lbl:    'Opac. %:',
      blur_lbl:           'Blur:',
      bg_box_lbl:         'Background box',
      corners_lbl:        'Corners:',
      angle_lbl:          'Angle:',
      px_all_lbl:         'px (all)',
      box_border_lbl:     'Box border',
      thickness_lbl:      'Thickness:',
      line_lbl:           'Line:',
      curvature_lbl:      'Curvature:',
      warp_realtime_hint: '✔ Real-time preview · also applied when generating',
      my_fields_lbl:      'My fields:',
      text_style_lbl:     'Text style',
      center_btn:         '⊕ Center',
      masks_lbl:          'Masks:',
      corner_radius_lbl:  'Corner radius:',
      add_countdown_btn:  '⏱  Add Countdown',
      styles_tof_sep:     'TextOnFlow Styles',
      color_colon:        'Color:',
      padding_colon:      'Padding:',
      opacity_colon:      'Opacity:',

      /* States and messages */
      error_no_image:     'Load an image first',
      error_generate:     'Error generating. Please try again.',
      error_upload:       'Error uploading image',
      copied_ok:          'Copied',
      saved_ok:           'Saved',
      loading:            'Loading…',
      done:               'Done',

      /* Tooltips */
      tip_double_click:   'Double click: center on X and Y',
      tip_shift_dblclick: 'Shift + double click: center on X only',
      tip_arrows:         'Arrows: move 1 px · Shift+Arrows: 10 px',
      tip_right_click:    'Right click: options menu',
      tip_eyedropper:     'Pick color from canvas',
      tip_warp_bend:      'Warp intensity',

      /* Manual */
      manual_link:        'Manual',
      docs_link:          'Documentation',

      /* Keyboard shortcuts */
      shortcut_center:    'Center on canvas (X and Y)',
      shortcut_center_x:  'Center on X only',

    }
  };

  /* ── Estado actual del idioma ── */
  var _lang = 'es';

  /* ── Función de traducción ── */
  function t(key) {
    var dict = TRANSLATIONS[_lang] || TRANSLATIONS['es'];
    return dict[key] !== undefined ? dict[key] : (TRANSLATIONS['es'][key] || key);
  }

  /* ── Aplicar idioma al DOM (data-i18n) ── */
  function _tofApplyLang(lang) {
    /* 1 — Todos los elementos con data-i18n → reemplazar textContent */
    document.querySelectorAll('[data-i18n]').forEach(function (el) {
      var key = el.getAttribute('data-i18n');
      var val = t(key);
      if (val) el.textContent = val;
    });

    /* 2 — Todos los elementos con data-i18n-ph → reemplazar placeholder */
    document.querySelectorAll('[data-i18n-ph]').forEach(function (el) {
      var key = el.getAttribute('data-i18n-ph');
      var val = t(key);
      if (val) el.placeholder = val;
    });

    /* 3 — Todos los elementos con data-i18n-title → reemplazar title */
    document.querySelectorAll('[data-i18n-title]').forEach(function (el) {
      var key = el.getAttribute('data-i18n-title');
      var val = t(key);
      if (val) el.title = val;
    });
  }

  /* ── Cambiar idioma ── */
  function setLang(lang) {
    if (!TRANSLATIONS[lang]) return;
    _lang = lang;
    try { localStorage.setItem('tof_lang', lang); } catch (_) {}
    window.TOF_LANG = lang;
    document.documentElement.lang = lang;
    _updateToggleUI(lang);
    _tofApplyLang(lang);
  }

  /* ── Actualizar etiqueta del botón de idioma ── */
  function _updateToggleUI(lang) {
    var lbl = document.getElementById('lang-label');
    if (lbl) lbl.textContent = lang.toUpperCase();
  }

  /* ── Alternar entre ES ↔ EN con un solo clic ── */
  function toggleLang() {
    setLang(_lang === 'es' ? 'en' : 'es');
  }

  /* ── Detectar idioma al cargar ── */
  function initLang() {
    var saved = null;
    try { saved = localStorage.getItem('tof_lang'); } catch (_) {}
    var detected = saved || (navigator.language || '').slice(0, 2).toLowerCase();
    var lang = TRANSLATIONS[detected] ? detected : 'es';
    _lang = lang;
    window.TOF_LANG = lang;
    document.documentElement.lang = lang;
  }

  /* ── Exportar al scope global ── */
  window.TOF_T        = TRANSLATIONS;
  window.TOF_LANG     = _lang;
  window.t            = t;
  window.setLang      = setLang;
  window.toggleLang   = toggleLang;
  window._tofApplyLang = _tofApplyLang;

  /* ── Inicializar ── */
  initLang();

  document.addEventListener('DOMContentLoaded', function () {
    _updateToggleUI(window.TOF_LANG);
    _tofApplyLang(window.TOF_LANG);
  });

})();
