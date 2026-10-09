// ========================================================================
// SISTEMA DE ACCESIBILIDAD - RENAVEN
// ========================================================================
document.addEventListener('DOMContentLoaded', function() {
    console.log('🔧 Iniciando sistema de accesibilidad (global)...');

    try {
        // ---- Menú desplegable ----
        const toggleBtn = document.getElementById('accessibilityToggle');
        const menu = document.getElementById('accessibilityMenu');
        const closeMenuBtn = document.getElementById('closeAccessibilityMenu');

        if (toggleBtn && menu) {
            toggleBtn.addEventListener('click', function(e) {
                e.stopPropagation();
                menu.style.display = menu.style.display === 'none' || menu.style.display === '' ? 'flex' : 'none';
            });

            closeMenuBtn.addEventListener('click', function(e) {
                e.stopPropagation();
                menu.style.display = 'none';
            });

            document.addEventListener('click', function(e) {
                if (menu && toggleBtn && !menu.contains(e.target) && !toggleBtn.contains(e.target)) {
                    menu.style.display = 'none';
                }
            });
        }

        // ---- Temas pastel ----
        function applyTheme(themeClass) {
            try {
                document.body.classList.remove('theme-rosa', 'theme-celeste', 'theme-menta', 'theme-amarillo', 'theme-lila', 'theme-durazno');
                if (themeClass) {
                    document.body.classList.add(themeClass);
                }
                localStorage.setItem('selectedTheme', themeClass || '');
                console.log('✅ Tema aplicado:', themeClass || 'Original');
            } catch (e) {
                console.error('❌ Error al aplicar tema:', e);
            }
        }

        // ---- Botones de temas ----
        const themeMap = {
            'themeOriginal': null,
            'themeRosa': 'theme-rosa',
            'themeCeleste': 'theme-celeste',
            'themeMenta': 'theme-menta',
            'themeAmarillo': 'theme-amarillo',
            'themeLila': 'theme-lila',
            'themeDurazno': 'theme-durazno'
        };

        Object.keys(themeMap).forEach(id => {
            const btn = document.getElementById(id);
            if (btn) {
                btn.addEventListener('click', function() {
                    applyTheme(themeMap[id]);
                    if (menu) menu.style.display = 'none';
                });
            }
        });

        // ---- Cargar tema guardado ----
        const savedTheme = localStorage.getItem('selectedTheme');
        if (savedTheme) {
            applyTheme(savedTheme);
        }

        // ---- Fondo de pantalla ----
        document.getElementById('bgImageBtn')?.addEventListener('click', function() {
            document.body.className = 'bg-image-default';
            localStorage.setItem('backgroundMode', 'image');
            if (menu) menu.style.display = 'none';
        });
        document.getElementById('bgBlackBtn')?.addEventListener('click', function() {
            document.body.className = 'bg-black-solid';
            localStorage.setItem('backgroundMode', 'black');
            if (menu) menu.style.display = 'none';
        });
        document.getElementById('bgWhiteBtn')?.addEventListener('click', function() {
            document.body.className = 'bg-white-solid';
            localStorage.setItem('backgroundMode', 'white');
            if (menu) menu.style.display = 'none';
        });

        const savedBg = localStorage.getItem('backgroundMode');
        if (savedBg === 'black') document.body.className = 'bg-black-solid';
        else if (savedBg === 'white') document.body.className = 'bg-white-solid';
        else document.body.className = 'bg-image-default';

        // ---- Tamaño de fuente ----
        let fontSize = parseInt(localStorage.getItem('fontSize') || '100');
        function applyFontSize() {
            document.body.style.fontSize = (13 * fontSize / 100) + 'px';
            localStorage.setItem('fontSize', fontSize);
        }
        document.getElementById('increaseFontBtn')?.addEventListener('click', function() {
            if (fontSize < 150) { fontSize += 10; applyFontSize(); }
            if (menu) menu.style.display = 'none';
        });
        document.getElementById('decreaseFontBtn')?.addEventListener('click', function() {
            if (fontSize > 70) { fontSize -= 10; applyFontSize(); }
            if (menu) menu.style.display = 'none';
        });
        document.getElementById('resetFontBtn')?.addEventListener('click', function() {
            fontSize = 100;
            applyFontSize();
            if (menu) menu.style.display = 'none';
        });
        applyFontSize();

        console.log('✅ Sistema de accesibilidad global iniciado correctamente.');

    } catch (error) {
        console.error('❌ Error en el sistema de accesibilidad global:', error);
    }
});