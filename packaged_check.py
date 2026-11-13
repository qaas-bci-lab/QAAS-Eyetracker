"""Diagnostic mode for the installed build. Never opens a physical camera."""
import os,json,traceback,time
from pathlib import Path
from app_paths import DATA_ROOT,RESOURCE_ROOT

def release_diagnostic_windows(app):
    """Dispose media/threads while Qt is alive, before interpreter shutdown."""
    from PyQt6.QtCore import QCoreApplication,QEvent,QUrl
    windows=list(app.topLevelWidgets())
    for window in windows:
        task=getattr(window,'task',None)
        if task is not None and task.isRunning() and not task.wait(5000):
            raise RuntimeError('Diagnostic analysis thread did not finish')
    # Deliver done/finished signals before disconnecting players and deleting UI.
    app.processEvents()
    for window in windows:
        player=getattr(window,'player',None)
        if player is not None:
            player.stop()
            player.setVideoOutput(None)
            player.setAudioOutput(None)
            player.setSource(QUrl())
    app.processEvents()
    for window in windows:
        window.close()
        window.deleteLater()
    QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)
    app.processEvents()
    QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)

def run(output):
    result={'ok':False,'data_root':str(DATA_ROOT),'resources':str(RESOURCE_ROOT),
            'portable_mode':(RESOURCE_ROOT/'portable_mode.json').is_file(),
            'installer_mode':(RESOURCE_ROOT/'installer_mode.json').is_file(),'checks':[]}
    app=None
    try:
        os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
        import numpy as np,cv2,torch,csv
        from PyQt6.QtCore import QTimer
        from PyQt6.QtWidgets import QApplication,QPushButton,QComboBox,QTabBar
        from PyQt6.QtGui import QFontMetrics
        from main_window import Main
        from scheme_store import load_settings,default_scheme,processing_for_mode
        from model_runtime import get_tracker
        from reporting import report
        from offline import infer_session
        from ui_components import SettingsDialog,SchemeDialog,TestStartDialog
        app=QApplication([]);app.setQuitOnLastWindowClosed(False);window=Main();window.show();app.processEvents()
        assert {'开始实验','编辑','实验','测试','新增','历史记录','设置'} <= {w.text() for w in window.findChildren(QPushButton) if w.isVisibleTo(window)}
        assert len([w for w in window.findChildren(QComboBox) if w.isVisibleTo(window)])==2
        assert not window.history_dialog.isVisible()
        assert QFontMetrics(app.font()).inFontUcs4(ord('眼'))
        assert window.participant_info.values()==dict(name='',age=None,sex='',participant_id='')
        settings=load_settings();assert settings['geometry']['camera_top_center']
        window.grab().save(str(output.with_suffix('.png')))
        person=window.catalog.upsert_person(dict(name='安装验证人员', participant_id='001',age=25,sex='男'))
        window.people_panel.select_person(person['key'])
        assert window.participant_info.values()['participant_id']=='001'
        assert window.participant_info.values()['person_key']==person['key']
        assert window.participant_combo.currentData()==person['key']
        window.open_history()
        app.processEvents()
        assert window.history_dialog.isVisible()
        window.history_dialog.back_button.click()
        assert not window.history_dialog.isVisible()
        window.switch_workspace_mode('test')
        assert window.start_button.text()=='开始测试'
        assert window.selected_scheme()['mode']=='test'
        window.switch_workspace_mode('experiment')
        assert window.selected_scheme()['mode']=='experiment'

        opened_settings = []

        def close_active_settings():
            dialog = QApplication.activeModalWidget()
            if isinstance(dialog, SettingsDialog):
                opened_settings.append(window.workspace_mode)
            if dialog is not None:
                dialog.close()

        for mode in ('experiment', 'test'):
            window.switch_workspace_mode(mode)
            QTimer.singleShot(0, close_active_settings)
            window.settings_button.click()
        assert opened_settings == ['experiment', 'test'], 'Settings must open from both main window modes'
        window.switch_workspace_mode('experiment')
        result['checks'].append('Main window Settings button opens in experiment and test modes')

        window.catalog.upsert_person(dict(person, participant_id='002'))
        reused=window.catalog.upsert_person(dict(name='另一位安装验证人员',participant_id='001'))
        assert reused['key']!=person['key']
        window.close()
        reopened=Main();reopened.show();app.processEvents()
        assert reopened.catalog.people(), 'Saved people should remain available'
        assert reopened.current_person is None
        assert reopened.participant_combo.currentText()==''
        assert reopened.people_panel.selected_person() is None
        reopened.close()
        result['checks'].append('Minimal home, empty person selection on restart, separate history, mode switching, Chinese font')
        settings_dialog=SettingsDialog(settings)
        assert settings_dialog.distance.suffix().strip()=='mm'
        assert abs(settings_dialog.distance.value()-settings['geometry']['view_distance_cm']*10)<.051
        assert not settings_dialog.advanced.isHidden()
        settings_dialog.show();app.processEvents()
        assert settings_dialog.isModal()
        assert not any(tab.isVisible() and tab.tabText(i) == '测试'
                       for tab in settings_dialog.findChildren(QTabBar) for i in range(tab.count()))
        assert settings_dialog.distance_mode.currentData()=='estimated'
        assert [control.text() for control in settings_dialog.settings_nav_buttons]==['数据处理','相机与屏幕']
        settings_dialog.settings_nav_buttons[1].click();app.processEvents()
        assert settings_dialog.content_tabs.currentWidget() is not settings_dialog.processing_page
        settings_dialog.settings_nav_buttons[0].click();app.processEvents()
        assert settings_dialog.content_tabs.currentWidget() is settings_dialog.processing_page
        assert settings_dialog.processing_mode.currentData()=='immediate'
        assert settings_dialog.retain_raw_rgb.isVisible()
        assert settings_dialog.processing_device_card.isVisible()
        settings_dialog.processing_device.setCurrentIndex(settings_dialog.processing_device.findData('cpu'))
        requested_threads = min(3, settings_dialog.processing_cpu_threads.maximum())
        settings_dialog.processing_cpu_threads.setValue(requested_threads)
        settings_dialog.processing_mode.setCurrentIndex(settings_dialog.processing_mode.findData('deferred'))
        assert settings_dialog.processing_output_dir.isVisible()
        assert not settings_dialog.retain_raw_rgb.isVisible()
        assert not settings_dialog.processing_device_card.isVisible()
        deferred_root=DATA_ROOT/'packaged_deferred_rgb'
        deferred_root.mkdir(exist_ok=True)
        settings_dialog.processing_output_dir.setText(str(deferred_root))
        settings_dialog.save()
        stored=load_settings()
        assert processing_for_mode(stored,'experiment')['output_dir']==str(deferred_root)
        assert processing_for_mode(stored,'experiment')['device']=='cpu'
        assert processing_for_mode(stored,'experiment').get('cpu_threads', requested_threads)==requested_threads
        assert processing_for_mode(stored,'test')['mode']=='immediate'
        settings_dialog.close()
        schemes_dialog=SchemeDialog(settings)
        schemes_dialog.show();app.processEvents()
        assert schemes_dialog.isModal()
        assert not schemes_dialog.scheme_sections['calibration'].isHidden()
        assert [schemes_dialog.mode_tabs.tabText(i) for i in range(2)]==['实验','测试']
        selected=next(s for s in settings['schemes'] if s['id']==settings['selected_scheme'])
        assert selected['model_id']=='affnet_mixed'
        assert schemes_dialog.model.currentData()=='affnet_mixed'
        assert schemes_dialog.width()<=760 and schemes_dialog.height()<=540
        assert schemes_dialog.calibration.currentData()==selected['calibration_points']
        assert schemes_dialog.calibration_countdown.isEnabled()==(selected['calibration_points']!=0)
        draft=default_scheme()
        assert draft['calibration_points']==9 and draft['fixation_enabled']
        assert draft['experiment_protocol']=='two_part' and draft['duration_minutes']==8.
        assert draft['rest_minutes']==15. and schemes_dialog.rest_minutes.value()==selected['rest_minutes']
        assert draft['fixation_style']=='cross' and draft['target_style']=='cross'
        assert draft['background_mode']=='solid' and draft['background_color']=='#f0f0f0'
        assert schemes_dialog.background_mode.currentData()==selected['background_mode']
        from PyQt6.QtCore import Qt
        assert schemes_dialog.windowFlags() & Qt.WindowType.FramelessWindowHint
        assert (RESOURCE_ROOT/'assets/logo.png').is_file()
        schemes_dialog.mode_tabs.setCurrentIndex(1)
        assert schemes_dialog.target_style.count()==3
        schemes_dialog.target_style.setCurrentIndex(schemes_dialog.target_style.findData('countdown'))
        assert schemes_dialog.target_preview.style=='countdown'
        selected_test=next(s for s in settings['test_schemes'] if s['id']==settings['selected_test_scheme'])
        assert schemes_dialog.calibration_countdown.value()==selected_test['calibration_countdown_s']
        assert schemes_dialog.transition_exclusion.value()==selected_test['transition_exclusion_s']
        assert schemes_dialog.calibration_dwell.value()==selected_test['calibration_dwell_s']
        assert schemes_dialog.calibration_exclude_start.value()==selected_test['calibration_exclude_start_ms']
        assert schemes_dialog.calibration_exclude_end.value()==selected_test['calibration_exclude_end_ms']
        assert draft['transition_exclusion_s']==1. and draft['calibration_dwell_s']==2.
        assert draft['calibration_exclude_start_ms']==500 and draft['calibration_exclude_end_ms']==0
        assert draft['fixation_color']=='#000000'
        assert draft['direction_visible_s']==.3
        schemes_dialog.direction_visible.setValue(.75)
        schemes_dialog.flush()
        assert schemes_dialog.data['test_schemes'][schemes_dialog.index]['direction_visible_s']==.75
        from PyQt6.QtTest import QTest
        for field,text,expected in [(schemes_dialog.minutes,'0.75',.75),
                                    (schemes_dialog.calibration_countdown,'7',7.),
                                    (schemes_dialog.transition_exclusion,'1.75',1.75)]:
            assert field.lineEdit().width()>100
            field.lineEdit().selectAll();QTest.keyClicks(field.lineEdit(),text);field.interpretText()
            assert field.value()==expected
        schemes_dialog.flush()
        assert schemes_dialog.data['test_schemes'][schemes_dialog.index]['transition_exclusion_s']==1.75
        from capture import calibration_timing,calibration_sample_eligible,recording_target_state
        assert calibration_timing(draft)==(2.,.5,2.)
        snap=dict(target_id=1,target_since=10.,calibration_window_s=[.5,2.])
        assert not calibration_sample_eligible(10499000000,snap,1)
        assert calibration_sample_eligible(10500000000,snap,1)
        assert calibration_sample_eligible(11999000000,snap,1)
        assert not calibration_sample_eligible(12000000000,snap,1)
        point_meta=dict(mode='test',transition_exclusion_s=1.75,points=[dict(x=.5,y=.5,seconds=3.)])
        assert not recording_target_state(point_meta,dict(physical_width=100,physical_height=100),1.74)['eligible']
        assert recording_target_state(point_meta,dict(physical_width=100,physical_height=100),1.75)['eligible']
        result['checks'].append('Decimal-minute duration, integer countdown, 2-second calibration and default test points')
        assert QFontMetrics(app.font()).inFontUcs4(ord('3'))
        schemes_dialog.close()
        test_dialog=TestStartDialog(settings)
        assert test_dialog.selected_scheme_id() in {s['id'] for s in settings['test_schemes']}
        assert not ({s['id'] for s in settings['schemes']} & {s['id'] for s in settings['test_schemes']})
        test_dialog.close()
        result['checks'].append('Separate mode banks, test launch dialog, fully expanded geometry')
        result['checks'].append('Millimetre geometry input, colour and point/cross/countdown targets')
        from types import SimpleNamespace
        import copy
        from main_window import Experiment
        screen=app.primaryScreen();area=screen.geometry();dpr=float(screen.devicePixelRatio())
        flow_cfg=dict(physical_width=round(area.width()*dpr),physical_height=round(area.height()*dpr),
                      dpr=dpr,screen_name=screen.name())
        flow_worker=SimpleNamespace(begin_calibration=lambda:None,set_target=lambda *_:None,
                                    request_calibration_fit=lambda:None,stop=lambda:None)
        flow_main=SimpleNamespace(cfg=flow_cfg,worker=flow_worker,calibration=None)
        flow_scheme=copy.deepcopy(settings['schemes'][0]);flow_scheme.update(calibration_points=5,calibration_countdown_s=3,
                                                                          experiment_protocol='single')
        surface=Experiment(flow_main,flow_scheme)
        try:
            surface.open_flow();surface.timer.stop();app.processEvents()
            assert surface.counting_down and surface.active is None
            assert surface.flow_progress.isHidden() and surface.message==''
            surface.countdown_started-=3.1;surface.tick()
            assert surface.calibrating and surface.flow_progress.isHidden() and surface.flow_progress_label.isHidden()
            assert surface.message==''
            surface.cal_index=len(surface.calpoints)-1;surface.cal_start-=3.1;surface.tick()
            assert surface.fitting and surface.active is None
            assert surface.flow_progress_label.text()=='正在更新参数'
            assert not surface.flow_progress.isHidden()
            surface.ready_now(dict(target_count=5));assert surface.message=='按 S 开始'
            assert surface.flow_progress.isHidden() and surface.flow_progress_label.isHidden()
        finally:
            surface._finishing=True;surface.timer.stop();surface.close()
        result['checks'].append('Configurable preparation countdown, target-only calibration, post-calibration parameter progress')
        from experiment_flow_check import check_two_part_experiment
        check_two_part_experiment(app, flow_cfg)
        from lsl_check import check_lsl_roundtrip
        check_lsl_roundtrip()
        result['checks'].append('Real LSL marker outlet and inlet round trip')
        result['checks'].append('Two formal RGB segments with four calibrations and a rest interval')
        result['checks'].append('Exact 13 LSL triggers for the paired capture timeline')
        from camera_calibration_ui import CameraCalibrationDialog
        from calibration_board import board_svg
        from camera_calibration import BoardSpec
        from PyQt6.QtSvg import QSvgRenderer
        from PyQt6.QtCore import QByteArray
        wizard=CameraCalibrationDialog(data_root=DATA_ROOT)
        assert wizard.camera_thread is None and not wizard.capture_button.isEnabled()
        assert tuple(wizard.resolution.currentData())==(1280,720)
        assert QSvgRenderer(QByteArray(board_svg(BoardSpec()).encode('utf8'))).isValid()
        from PyQt6.QtPrintSupport import QPrinter
        wizard.close()
        result['checks'].append('Camera intrinsics wizard, SVG/print support, lazy camera access')
        # Exercise the permission/preview window with a synthetic device. The
        # user's actual camera is accessed only after clicking the button.
        import camera_check
        class FakeCheckCamera:
            def read(self):
                return True, np.zeros((64,96,3), dtype=np.uint8)
            def release(self):
                self.released=True
        fake_camera=FakeCheckCamera()
        previous_open=camera_check.open_working_camera
        camera_check.open_working_camera=lambda _: (fake_camera,np.zeros((64,96,3),dtype=np.uint8))
        try:
            camera_dialog=camera_check.CameraCheckDialog(dict(camera_index=0))
            camera_dialog.show()
            deadline=time.monotonic()+20
            while '画面读取成功' not in camera_dialog.status.text() and time.monotonic()<deadline:
                app.processEvents();time.sleep(.02)
            assert '画面读取成功' in camera_dialog.status.text(),camera_dialog.status.text()
            camera_dialog.reject();app.processEvents()
            assert not camera_dialog.worker.isRunning() and fake_camera.released
            result['checks'].append('Camera access check shows a decoded frame and releases the device')
        finally:
            camera_check.open_working_camera=previous_open
        import camera_discovery
        previous_scan_open=camera_discovery.open_working_camera
        scan_cameras=[]
        def fake_scan(config):
            if config['camera_index'] not in (0, 2):raise RuntimeError('no device')
            camera=FakeCheckCamera();scan_cameras.append(camera)
            return camera,np.zeros((64,96,3),dtype=np.uint8)
        camera_discovery.open_working_camera=fake_scan
        try:
            discovery=camera_discovery.CameraDiscoveryDialog(dict(camera_index=0))
            discovery.show();deadline=time.monotonic()+20
            while discovery.worker.isRunning() and time.monotonic()<deadline:
                app.processEvents();time.sleep(.01)
            app.processEvents()
            assert not discovery.worker.isRunning() and discovery.cameras.count()==2
            discovery.cameras.setCurrentRow(1);discovery.select_camera()
            assert discovery.selected_index==2 and all(getattr(c,'released',False) for c in scan_cameras)
            discovery.close()
            result['checks'].append('Multiple camera scan, selection and device release')
        finally:
            camera_discovery.open_working_camera=previous_scan_open
        cfg=dict(physical_width=1200,physical_height=800,dpr=1.5,screen_width_mm=260.3,screen_height_mm=173.6,
            camera_x_mm=0,camera_y_mm=10,fov_degrees=60,calibration_points=0)
        hashes={}
        for aff_id in ['affnet_mixed']:
            tracker=get_tracker(aff_id,cfg)
            hashes[aff_id]=tracker.model_hash
            with torch.no_grad():
                aff_output=tracker.net(torch.zeros(1,3,112,112),torch.zeros(1,3,112,112),torch.zeros(1,3,224,224),torch.zeros(1,12))
                assert aff_output.shape==(1,2) and torch.isfinite(aff_output).all()
            try:tracker.infer(np.zeros((240,320,3),np.uint8))
            except ValueError:pass
            else:raise AssertionError('Blank AFF-Net frame must be rejected')
            tracker.close();del tracker;result['checks'].append('AFF-Net real TorchScript + MediaPipe load and forward: '+aff_id)
        from processing_hardware import resolve_device
        device = resolve_device('auto')
        if device.startswith('cuda:'):
            tracker = get_tracker('affnet_mixed', dict(cfg, inference_device=device))
            try:
                with torch.inference_mode():
                    prediction = tracker.net(torch.zeros(1, 3, 112, 112, device=device),
                                             torch.zeros(1, 3, 112, 112, device=device),
                                             torch.zeros(1, 3, 224, 224, device=device),
                                             torch.zeros(1, 12, device=device))
                assert prediction.is_cuda and torch.isfinite(prediction).all().item()
            finally:
                tracker.close()
            result['checks'].append('Capture automatic analysis has a working CUDA model')
        else:
            result['checks'].append('Capture automatic analysis selects CPU when CUDA is unavailable')
        folder=DATA_ROOT/'sessions'/'packaged_diagnostic';folder.mkdir(exist_ok=True)
        cfg['use_maml']=False
        metadata=dict(config=cfg,model_id='affnet_mixed',calibration=dict(target_count=0,affine=None,head_adapted=False,model_sha256=hashes['affnet_mixed']),image_path='',assume_fixation=True,actual_duration_s=.2)
        (folder/'session.json').write_text(json.dumps(metadata),encoding='utf8')
        writer=cv2.VideoWriter(str(folder/'camera.mp4'),cv2.VideoWriter_fourcc(*'mp4v'),20,(320,240));assert writer.isOpened()
        for _ in range(4):writer.write(np.zeros((240,320,3),np.uint8))
        writer.release()
        with (folder/'capture.csv').open('w',encoding='utf-8-sig',newline='') as f:
            w=csv.writer(f);w.writerow(['capture_id','timestamp_unix_ns','monotonic_ns','elapsed_s','video_index','target_id','origin_x_physical','origin_y_physical','label_valid'])
            for i in range(4):w.writerow([i,1700000000000000000+i*50000000,1000000000+i*50000000,i*.05,i,0,600,400,1])
        infer_session(folder,progress=None,force=True)
        stats=report(folder);assert stats['video_created'] and stats['valid_predictions']==0
        from analysis_ui import AnalysisWindow
        analysis=AnalysisWindow(initial=folder);analysis.show();app.processEvents();assert analysis.open_video.isEnabled();analysis.toggle_play()
        limit=time.monotonic()+2
        while time.monotonic()<limit:app.processEvents();time.sleep(.02)
        assert analysis.player.error().value==0,analysis.player.errorString()
        analysis.close();result['checks'].append('Offline MP4 -> timestamp CSV -> PNG/HTML/MP4 -> Qt video playback')
        # A new experiment produces coordinates, while historical labelled tests
        # above retain their report and playback. Reuse deterministic blank video.
        import shutil
        experiment_folder=DATA_ROOT/'sessions'/'packaged_coordinate_experiment';experiment_folder.mkdir(exist_ok=True)
        experiment_metadata=dict(metadata,mode='experiment',assume_fixation=False,
            origin=dict(x=.5,y=.5),scheme=dict(mode='experiment'),delete_raw_after_analysis=False,
            participant=dict(name='测试人员',age=23,sex='女',participant_id='001'))
        (experiment_folder/'session.json').write_text(json.dumps(experiment_metadata),encoding='utf8')
        shutil.copy2(folder/'camera.mp4',experiment_folder/'camera.mp4')
        shutil.copy2(folder/'capture.csv',experiment_folder/'capture.csv')
        infer_session(experiment_folder,progress=None,force=True)
        experiment_stats=report(experiment_folder,compact_experiment=True)
        assert Path(experiment_stats['txt_path']).is_file()
        assert experiment_stats['export_rows']==4 and experiment_stats['sample_hz']==20
        with Path(experiment_stats['txt_path']).open(encoding='utf8') as file:
            exported_lines=list(csv.reader(file,delimiter='\t'))
        assert exported_lines[0]==['time_s','timestamp_utc','x_cm','y_cm','distance_cm']
        assert [row[0] for row in exported_lines[1:]]==['0.00','0.05','0.10','0.15']
        assert [row[1] for row in exported_lines[1:]]==[
            '2023-11-14T22:13:20.000000000Z', '2023-11-14T22:13:20.050000000Z',
            '2023-11-14T22:13:20.100000000Z', '2023-11-14T22:13:20.150000000Z']
        compact_metadata=json.loads((experiment_folder/'session.json').read_text(encoding='utf8'))
        assert compact_metadata['files']['data']==Path(experiment_stats['txt_path']).name
        assert compact_metadata['participant']['name']=='测试人员' and compact_metadata['participant']['participant_id']=='001'
        assert compact_metadata['config']==cfg
        assert experiment_stats.get('mean_error_cm') is None
        assert (experiment_folder/'distribution.png').is_file()
        assert (experiment_folder/'gaze_replay.mp4').is_file()
        assert not (experiment_folder/'replay_poster.png').exists()
        assert {p.name for p in experiment_folder.iterdir()} == {
            'session.json',Path(experiment_stats['txt_path']).name,'distribution.png','gaze_replay.mp4','camera.mp4'}
        assert not (experiment_folder/'errors.png').exists()
        assert not (experiment_folder/'report.html').exists()
        analysis=AnalysisWindow(initial=experiment_folder)
        analysis.show();app.processEvents()
        analysis.start_automatic(experiment_folder)
        deadline=time.monotonic()+90
        while analysis.busy and time.monotonic()<deadline:
            app.processEvents();time.sleep(.01)
        app.processEvents();assert not analysis.busy
        assert analysis.open_txt.isEnabled()
        assert analysis.open_participant.isEnabled()
        assert '20 Hz' in analysis.coordinate_status.text()
        analysis.close()
        result['checks'].append('Experiment 20 Hz TXT with original frame UTC, person/device JSON, plot and replay')
        deferred_folder=deferred_root/'packaged_unprocessed'
        deferred_folder.mkdir(exist_ok=True)
        deferred_metadata=dict(experiment_metadata, processing=dict(mode='deferred',retain_raw_rgb=True,
                                output_dir=str(deferred_root)), delete_raw_after_analysis=False)
        (deferred_folder/'session.json').write_text(json.dumps(deferred_metadata),encoding='utf8')
        for filename in ['camera.mp4','capture.csv']:
            shutil.copy2(folder/filename,deferred_folder/filename)
        from record_layout import compact_pending
        compact_pending(deferred_folder)
        assert {p.name for p in deferred_folder.iterdir()}=={'session.json','camera.mp4'}
        deferred_window=AnalysisWindow(initial=deferred_folder,record_roots=[deferred_root])
        deferred_window.show();app.processEvents()
        assert deferred_window.records.findData(str(deferred_folder))>=0
        assert deferred_window.record_state.text()=='待处理'
        assert deferred_window.generate.isEnabled() and deferred_window.open_folder.isEnabled()
        assert not (deferred_folder/'inference_complete.json').exists()
        deferred_window.run_analysis()
        deadline=time.monotonic()+90
        while deferred_window.busy and time.monotonic()<deadline:
            app.processEvents();time.sleep(.01)
        app.processEvents()
        assert not deferred_window.busy and deferred_window.open_txt.isEnabled()
        assert deferred_window.record_state.text()=='已分析'
        assert (deferred_folder/'camera.mp4').is_file()
        deferred_window.close()
        result['checks'].append('Processing settings persist, external raw recording is discovered and analyzed manually')
        tail_folder=DATA_ROOT/'sessions'/'packaged_test_tail';tail_folder.mkdir(exist_ok=True)
        tail_metadata=dict(metadata,mode='test',protocol_version=3,recording_protocol_version=3,
            transition_exclusion_s=.5,points=[dict(x=.5,y=.5,seconds=1.)],
            planned_duration_s=1.,actual_duration_s=1.15,delete_raw_after_analysis=False)
        (tail_folder/'session.json').write_text(json.dumps(tail_metadata),encoding='utf8')
        for filename in ['capture.csv','gaze.csv']:
            with (folder/filename).open(encoding='utf-8-sig',newline='') as source:
                reader=csv.DictReader(source);fields=reader.fieldnames;rows=list(reader)
            for index,row in enumerate(rows):row['elapsed_s']=str(.5+index*.05)
            with (tail_folder/filename).open('w',encoding='utf-8-sig',newline='') as destination:
                writer=csv.DictWriter(destination,fields);writer.writeheader();writer.writerows(rows)
        tail_stats=report(tail_folder)
        assert tail_stats['video_created'] and (tail_folder/'report.json').is_file()
        result['checks'].append('Protocol-3 test replay safely passes the final target endpoint')
        result['ok']=True
    except Exception:result['error']=traceback.format_exc()
    finally:
        if app is not None:
            try:
                release_diagnostic_windows(app)
                result['checks'].append('Diagnostic windows, media players and analysis threads released before Qt shutdown')
            except Exception:
                result['ok']=False;result['cleanup_error']=traceback.format_exc()
    output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    return 0 if result['ok'] else 1
