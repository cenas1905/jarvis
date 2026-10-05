"""UI regression and draw-cost check; no API calls or microphone access."""
import time
from types import SimpleNamespace
from unittest.mock import patch
from app_config import load_app_config
from ui import JarvisUI


before = load_app_config()
with patch('ui.SoundManager'), patch.object(JarvisUI, '_kick_brief_refresh'):
    ui = JarvisUI()
    try:
        ui.root.attributes('-fullscreen', False)
        assert ui.voice_only_mode
        for width, height in [(1000, 700), (1600, 900)]:
            ui.root.geometry(f'{width}x{height}')
            ui.root.update()
            ui._resize_surface(width, height)
            for state in ('LISTENING', 'SPEAKING', 'THINKING', 'ERROR'):
                ui.set_state(state)
                ui._draw()
                ui.root.update_idletasks()
                assert not any(ui.bg.type(item) == 'text' for item in ui.bg.find_all())
                for name in ('log_frame', '_social_bar', '_phone_btn_canvas', '_settings_btn_canvas'):
                    assert not getattr(ui, name).winfo_ismapped(), name
        ui._toggle_simple_view()
        ui.root.update_idletasks()
        assert ui.log_frame.winfo_ismapped()
        assert ui._settings_panel.winfo_ismapped()
        ui._toggle_simple_view()
        with patch.object(ui, '_toggle_pause') as pause:
            ui._on_canvas_click(SimpleNamespace(x=ui.W // 2, y=ui.H // 2))
            pause.assert_called_once()
        start = time.perf_counter()
        for i in range(150):
            ui.tick += 1
            ui._draw()
        print(f'SIMPLE_DRAW_MEAN_MS={(time.perf_counter()-start)*1000/150:.2f}')
        assert load_app_config() == before, 'View toggle changed saved audio configuration'
        print('SIMPLE_UI_RESIZE_F2_CENTER_CLICK_AND_AUDIO_CONFIG_OK')
    finally:
        for timer in ui.root.tk.splitlist(ui.root.tk.call('after', 'info')):
            ui.root.after_cancel(timer)
        ui.root.destroy()
